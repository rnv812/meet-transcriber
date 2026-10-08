"""Слэш-команды чата с ассистентом (0.4, спец. §6 «как в CLI»).

Где: в начале `Participant.post_user_message` (окно, `web.CHAT_PAGE`) и
`Participant.queue_existing` («Продолжить разговор» после встречи,
`job_worker._chat`) — одинаково для всех. Окно только подсказывает список
(`Participant.view()["commands"]`).

**Разбор** (`parse`). Команда — сообщение без вложений вида `/имя`
(`[A-Za-z][\\w:.-]*`), дальше пробел или конец. `//текст` — обычный текст
`/текст` (`unescape`); `/home/x` — текст (за именем не пробел).

**Команды Meet** выполняются сразу, без хода модели; их ответ — системная
строка ленты `card: "command"` (`command`, `re` — сообщение пользователя,
`items` / `servers` — для окна, `text` — то же словами):

* `/help` — команды Meet и команды CLI (Claude Code: ответ `initialize`);
* `/mcp` — MCP-серверы и их состояние (Claude Code — `mcp_status`, Codex —
  `codex mcp list`, у прочих — «недоступно»); `/mcp reconnect [имя]` —
  переподключить (у Claude Code при неудаче — перезапуск с продолжением
  разговора); `/mcp enable|disable <имя>` — `mcp_toggle`;
* `/clear` — новый разговор с агентом: прежний сеанс забыт, затравка без
  переписки чата; лента остаётся;
* `/model [имя]` — без имени текущая модель и список, с именем — сменить
  (Claude Code — `set_model` до конца сессии, Codex/OpenCode — на следующие
  ходы; неизвестную CLI отвергает — строка ошибки);
* `/compact [указание]` — Claude Code: отдельным ходом (сжатие — строкой
  «Контекст сжат (было N токенов)»), у прочих — «недоступно».

**Команды CLI** (Claude Code: `commands` ответа `initialize`, `slash_commands`
`system/init` — свои `.claude/commands`, навыки, `/context`, `/review`…)
уходят отдельным «ходом-командой» дословно, без дельты и затравки, уровень
хода — USER (`via: "command"`); ответ — текстом CLI в ленту. Своя команда
пользователя с `` !`команда` `` проходит тоже: это его собственная настройка,
и вызвать её может только его сообщение (решение контролёра). Команды
терминала (`/login`, `/config`…) — строка «isn't available in this
environment», как у CLI. Неизвестная — «Нет команды /foo — /help» с кнопкой
окна «Отправить как текст» (`//foo …`).

В журнал процесса — только имя команды и исход, без аргументов (CLAUDE.md).
"""

import asyncio
import re
from dataclasses import dataclass

_COMMAND = re.compile(r"/([A-Za-z][\w:.-]*)(?:\s+(.*))?", re.DOTALL)

CLAUDE = "claude-code"
RUNNERS = ("codex", "opencode")

# Команды Meet: имя, подсказка аргументов, описание.
MEET_COMMANDS = (
    ("help", "", "Команды чата"),
    ("mcp", "[reconnect|enable|disable <сервер>]", "MCP-серверы и их состояние"),
    ("clear", "", "Новый разговор с агентом (лента остаётся)"),
    ("model", "[модель]", "Какая модель отвечает, сменить модель"),
    ("compact", "[указание]", "Сжать контекст разговора"),
)
MEET_NAMES = frozenset(name for name, _h, _d in MEET_COMMANDS)
# Что умеет провайдер из команд Meet (остальное — «недоступно для …»).
SUPPORTED = {
    CLAUDE: MEET_NAMES,
    "codex": frozenset({"help", "mcp", "clear", "model"}),
    "opencode": frozenset({"help", "clear", "model"}),
}
DEFAULT_SUPPORTED = frozenset({"help", "clear"})
# Команды терминала Claude Code: в headless-режиме их нет.
TERMINAL_ONLY = frozenset({
    "login", "logout", "config", "theme", "terminal-setup", "vim", "exit", "quit", "doctor", "ide",
    "install-github-app", "upgrade", "resume", "permissions", "hooks", "agents", "memory", "status",
    "bug", "privacy-settings", "statusline", "export", "add-dir", "keybindings", "output-style",
})
MCP_STATUS = {"connected": "подключён", "failed": "ошибка", "needs-auth": "нужен вход",
              "pending": "подключается", "disabled": "выключен"}
CLI_HINT_MAX = 40          # команд CLI в /help


@dataclass(frozen=True)
class Command:
    """Разобранная команда: имя и аргументы (как набраны, без краевых пробелов)."""
    name: str
    args: str = ""

    @property
    def text(self) -> str:
        return f"/{self.name}" + (f" {self.args}" if self.args else "")


def parse(text, attachments=()) -> Command | None:
    """Сообщение — команда? `//…` и сообщение с вложениями — не команда."""
    if attachments or not isinstance(text, str):
        return None
    body = text.strip()
    if body.startswith("//"):
        return None
    m = _COMMAND.fullmatch(body)
    if m is None:
        return None
    return Command(m.group(1), (m.group(2) or "").strip())


def unescape(text: str) -> str:
    """`//текст` → `/текст` (так человек пишет текст, начинающийся с «/»)."""
    if isinstance(text, str) and text.lstrip().startswith("//"):
        head = len(text) - len(text.lstrip())
        return text[:head] + text[head + 1:]
    return text


def supported(provider: str) -> frozenset:
    return SUPPORTED.get(provider, DEFAULT_SUPPORTED)


def meet_commands(provider: str) -> list[dict]:
    """Команды Meet, которые есть у этого провайдера (для окна и /help)."""
    ok = supported(provider)
    return [{"name": n, "hint": h, "description": d, "source": "meet"} for n, h, d in MEET_COMMANDS if n in ok]


def unavailable(name: str, label: str) -> str:
    return f"/{name} недоступно для {label}"


def unknown_text(cmd: Command) -> str:
    return f"Нет команды /{cmd.name} — /help"


def terminal_text(cmd: Command) -> str:
    return f"/{cmd.name} isn't available in this environment."


def mcp_lines(servers: list[dict]) -> list[str]:
    if not servers:
        return ["MCP-серверов нет"]
    out = []
    for s in servers:
        state = MCP_STATUS.get(s.get("status") or "", s.get("status") or "")
        error = f": {s['error']}" if s.get("error") and s.get("status") == "failed" else ""
        tail = " — войдите: claude /mcp в терминале" if s.get("status") == "needs-auth" else ""
        out.append(f"{s.get('name')} — {state}{error}{tail}")
    return out


def help_items(provider: str, cli: list[dict]) -> list[dict]:
    items = meet_commands(provider)
    seen = {i["name"] for i in items}
    for c in cli:
        if c.get("name") and c["name"] not in seen:
            items.append({"name": c["name"], "hint": c.get("hint") or "", "description": c.get("description") or "",
                          "source": "cli"})
            seen.add(c["name"])
    return items


def help_text(items: list[dict], label: str) -> str:
    meet = [i for i in items if i["source"] == "meet"]
    cli = [i for i in items if i["source"] == "cli"]
    lines = ["Команды Meet:"]
    lines += [f"/{i['name']}{' ' + i['hint'] if i['hint'] else ''} — {i['description']}" for i in meet]
    if cli:
        lines.append(f"Команды {label}:")
        for i in cli[:CLI_HINT_MAX]:
            desc = f" — {i['description']}" if i["description"] else ""
            lines.append(f"/{i['name']}{' ' + i['hint'] if i['hint'] else ''}{desc}")
        if len(cli) > CLI_HINT_MAX:
            lines.append(f"… и ещё {len(cli) - CLI_HINT_MAX}")
    lines.append("Текст, который начинается с «/», — через «//».")
    return "\n".join(lines)


async def run(p, msg: dict, cmd: Command) -> str:
    """Выполнить команду сообщения `msg` (запись журнала) у участника `p`.
    → `meet` (выполнена Meet), `turn` (ушла ходом-командой), `line` (ответ
    строкой: неизвестная, недоступная, терминальная)."""
    name = cmd.name
    label = p.provider_label
    if name in MEET_NAMES:
        if name not in supported(p.provider):
            await p.command_line(msg, cmd, unavailable(name, label), level="info")
            return "line"
        if name == "compact":
            p.queue_command(msg, cmd)
            return "turn"
        handler = {"help": _help, "mcp": _mcp, "clear": _clear, "model": _model}[name]
        try:
            await handler(p, msg, cmd)
        except Exception as e:     # команда не роняет ассистента: причина — строкой
            p.log_command(name, f"сбой ({type(e).__name__})")
            await p.command_line(msg, cmd, f"/{name} не выполнена: {e}", level="error")
        return "meet"
    if p.provider == CLAUDE:
        if name in TERMINAL_ONLY:
            await p.command_line(msg, cmd, terminal_text(cmd), level="info")
            return "line"
        if name in await p.cli_command_names():
            p.queue_command(msg, cmd)
            return "turn"
    await p.command_line(msg, cmd, unknown_text(cmd), level="info", unknown=cmd.text)
    return "line"


async def _help(p, msg, cmd) -> None:
    cli = await p.cli_commands() if p.provider == CLAUDE else []
    items = help_items(p.provider, cli)
    await p.command_line(msg, cmd, help_text(items, p.provider_label), items=items)


async def _mcp(p, msg, cmd) -> None:
    words = cmd.args.split()
    action = words[0].lower() if words else ""
    target = words[1] if len(words) > 1 else None
    if p.provider == "codex":
        if action:
            await p.command_line(msg, cmd, "У Codex MCP-серверы поднимаются заново в каждом ходе — "
                                           "переподключать нечего", level="info")
            return
        servers = await asyncio.to_thread(p.codex_mcp)
        if servers is None:
            await p.command_line(msg, cmd, "Список MCP-серверов Codex не прочитался", level="error")
            return
        rows = [{"name": s["name"], "status": "connected" if s.get("enabled") else "disabled"} for s in servers]
        lines = mcp_lines(rows)
        lines.append("В ходах ассистента MCP-серверы Codex выключены: остановить изменение до выполнения "
                     "Codex не умеет — MCP доступен ассистенту на Claude Code")
        await p.command_line(msg, cmd, "\n".join(lines), servers=rows)
        return
    conv = await p.conversation()
    if action in ("", "list", "status"):
        servers = await conv.mcp_status(settle_s=3.0)
        p.mcp_changed()
        await p.command_line(msg, cmd, "\n".join(mcp_lines(servers)), servers=servers)
        return
    if action == "reconnect":
        out = await conv.mcp_reconnect(target)
        if out.get("restarted") and not out.get("context"):
            p.need_seed()
        p.mcp_changed()
        results = out.get("results") or {}
        if not results:
            text = "Все MCP-серверы подключены — переподключать нечего"
        else:
            text = "\n".join(f"{n} — переподключён" if err is None else f"{n} — не переподключился: {err}"
                             for n, err in results.items())
        if out.get("restarted"):
            text += ("\nClaude Code перезапущен — разговор продолжается" if out.get("context")
                     else "\nClaude Code перезапущен — контекст восстановлю из журнала встречи")
        level = "error" if any(err for err in results.values()) else None
        p.log_command("mcp", "reconnect: " + ("ошибка" if level else "ок"))
        await p.command_line(msg, cmd, text, servers=out.get("servers") or [], level=level)
        return
    if action in ("enable", "disable"):
        if not target:
            await p.command_line(msg, cmd, f"Укажите сервер: /mcp {action} <имя>", level="info")
            return
        servers = await conv.mcp_toggle(target, action == "enable")
        p.mcp_changed()
        done = "включён" if action == "enable" else "выключен"
        await p.command_line(msg, cmd, "\n".join([f"{target} — {done} до конца сессии", *mcp_lines(servers)]),
                             servers=servers)
        return
    await p.command_line(msg, cmd, "Так: /mcp, /mcp reconnect [сервер], /mcp enable|disable <сервер>",
                         level="info")


async def _clear(p, msg, cmd) -> None:
    await p.clear_session()
    await p.command_line(msg, cmd, "Новый разговор с агентом: прежний сеанс забыт, лента осталась")


async def _model(p, msg, cmd) -> None:
    name = cmd.args.strip()
    if not name:
        await p.command_line(msg, cmd, await p.model_text(), models=p.model_choices())
        return
    if p.provider not in (CLAUDE, *RUNNERS):
        await p.command_line(msg, cmd, unavailable("model", p.provider_label), level="info")
        return
    await p.halt_turn()
    resolved = await p.set_model(name)
    await p.command_line(msg, cmd, f"Модель до конца сессии: {resolved}" if p.provider == CLAUDE
                         else f"Модель для следующих ходов: {resolved}")
