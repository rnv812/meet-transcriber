"""Провайдер Antigravity CLI (`agy`, Google): ход через `agy acp` (Agent
Client Protocol), действия — с согласия через ворота и карточки Meet.

Что подтверждено на установленном agy 1.5.11 (Windows, без вызова модели и
без входа: `--help` команд, `--version`, проба ACP `initialize` +
`session/new` с папкой данных во временной папке, строки и журнал изменений
внутри `agy.exe`):

* `agy acp` — сервер ACP по stdio (JSON-RPC 2.0 построчно, поле "jsonrpc").
  `initialize` → `protocolVersion: 1`, `agentCapabilities.loadSession`,
  `sessionCapabilities` {resume, close, load, list, additionalDirectories},
  `promptCapabilities.image`, `_meta.antigravity.systemPrompt.append`;
  `authMethods`: oauth-personal, oauth-business, gemini-api-key,
  agent-platform. Методы в программе: `session/new`, `session/load`,
  `session/resume`, `session/prompt`, `session/cancel`, `session/set_mode`
  (`modeId`), `session/set_model`, `session/set_config_option`,
  `session/request_permission`, `session/update` (agent_message_chunk,
  agent_thought_chunk, tool_call, tool_call_update, current_mode_update…).
* Без входа `session/new` отвечает `-32000 "Authentication required"`
  (секунда-две, модель не зовётся) — по этому Meet узнаёт, что входа нет
  (`login_status`). Вход — `agy acp --login` (Google в терминале).
* `agy acp --gemini-dir <папка>` переносит всё состояние agy: настройки CLI
  (`antigravity-cli/settings.json`), общие настройки и права проектов
  (`config/config.json`, `config/projects/`), MCP-серверы
  (`config/mcp_config.json`), плагины, разговоры. Meet запускает agy со своей
  постоянной папкой данных (`home()`, в папке данных Meet) — настройки и
  разрешающие правила пользователя и проектов не загружаются вовсе, и
  перебить запреты Meet им нечем. В `~/.gemini` Meet не пишет и
  `--dangerously-skip-permissions` не использует.
* `--cli_dir_name <имя>` — файл настроек читается из
  `<gemini-dir>/antigravity-cli/<имя>/settings.json`, а разговоры остаются в
  общей `antigravity-cli/conversations`: у каждого хода свой разовый файл
  правил (`run_settings`), продолжение сеанса не теряется (проба без входа,
  agy 1.5.11: журнал agy с `--cli_dir_name` пишет ту же «ACP app data
  directory» `<gemini-dir>/antigravity-cli`; в разовой папке — только
  settings.json и журнал сервера). Битый JSON agy
  молча заменяет настройками по умолчанию, а обратная косая черта в пути
  ломает его разбор — пути в правилах только с `/`.
* Ключи настроек (из программы и журнала изменений): `agentMode` (`""` —
  режим Default: по умолчанию с 1.5.7 — Auto, а он сам одобряет правку в
  рабочей папке и команды в песочнице), `toolPermission`
  (`always-proceed`, `request-review`, `strict`, `proceed-in-sandbox`;
  `strict` перекрывает Auto и accept-edits), `permissions` {`allow`, `ask`,
  `deny`} с правилами `command(<префикс>)`, `read_file(<путь>)`,
  `write_file(<путь>)`, `mcp(<сервер>/<инструмент>)`, `read_url(<домен>)`.
* MCP-серверы для сеанса клиент передаёт в `session/new`/`load`/`resume`
  (`mcpServers`); у Meet в своей папке данных их нет, поэтому MCP
  пользователя (`~/.gemini/config/mcp_config.json`) идут только в ходы по
  просьбе (`mcp_servers_for`), в ходе по репликам — пусто.
* В режиме `-p` agy сам отклоняет действие, на которое нужен вопрос, и пишет
  в stderr «N tool(s) required approval that headless mode cannot prompt
  for, so they were auto-denied» — запасной путь показывает это строкой хода.
  Формат `stream-json` — события `init`, `step_update` (`step_type`),
  `result` (`finished_reason`); имена полей событий живым ходом не
  проверены, разбор осторожный (`parse_stream`).
* Команды удаления разговора у CLI нет (только в окне `/resume`). Разговор
  лежит в своей папке данных Meet (`antigravity-cli/conversations/<id>.db` и
  `brain/<id>/`) — `forget_session` удаляет их сам; раскладка — по журналу
  изменений, на живом сеансе не проверена.

Не проверено (только живым ходом с входом): сохраняется ли вход agy при своей
`--gemini-dir` (если нет — нужен один вход для Meet, текст ошибки называет
команду), точные поля `toolCall` в запросах разрешения, пометка
`readOnlyHint` у MCP, принимает ли agy MCP-серверы `http` от клиента (без
`mcpCapabilities.http` Meet их не передаёт), применяются ли запреты из
разового файла к чтению внутри рабочей папки (его agy не спрашивает).

Ход (процесс на ход, как у Codex):

1. `agy acp --gemini-dir <home> --cli_dir_name <разовая папка>
   --disable-telemetry [--add-dir …]`; рабочие папки по уровню: NONE —
   папка встречи, READ — она же (остальное читается по запросу разрешения,
   ворота пропускают чтение), USER — встреча и база знаний; без ворот —
   `allowed_dirs`;
2. `initialize` (без возможностей fs и терминала: файлы и команды agy
   выполняет сам, спрашивая разрешения); не ответил, ошибка протокола —
   `Unavailable` → запасной путь `-p`;
3. `session/resume` (или `session/load`) с id из `sessions.json`, иначе
   `session/new`; рабочая папка сеанса — служебная `keep_dir()`; режим
   сеанса не Default — `session/set_mode default` (не вышло — хода нет);
   модель — `session/set_model`; уровень рассуждений (`llm.agy_effort`,
   0.6.2) — `session/set_config_option` опции категории `thought_level` из
   ответа сеанса (опции нет или уровень не принят — ход без него, строка в
   журнал без текста встречи);
4. `session/prompt` — системный промпт Meet в начале первого сообщения
   нового сеанса (как у Codex), дальше — только сообщение;
5. `session/update` → строки хода работы по ходу (`tool_use`, `gate`,
   `tool_result`), текст — из `agent_message_chunk` (по ходу — `on_text`;
   сообщение после вызова инструмента — новое, ответ — последнее);
   `stopReason: cancelled` без остановки Meet — сбой хода, не «Стоп»;
   `session/request_permission` → ворота Meet (`consent.agy_approval`):
   чтение, команды, правка, MCP — по уровню хода и таблице доступа;
   прочие запросы агента (fs, терминал) — отказ;
6. «Стоп» и таймаут — `session/cancel`, через CANCEL_GRACE_S — гашение
   дерева процессов. Разовый вызов (итоги, названия: без `keep_session` и
   `resume`) — `session/close` и удаление файлов сеанса после хода.

Изображения agy не отправляем (не в `llm.VISION_PROVIDERS`): пометка, как у
OpenCode. В журналы — ничего из хода."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from meet import netproxy, tempdirs
from meet.llm import codex_app
from meet.llm.base import (
    CANCELLED_ERROR, EMPTY_ERROR, NO_VISION_NOTE, TIMEOUT_ERROR, AgentReply, deny_prompt, drop_session_markers,
    kill_tree, popen_kwargs, resume_failure,
)
from meet.llm.detect import AGY_NOT_FOUND, find_agy

_ON_WINDOWS = sys.platform == "win32"
PROTOCOL_VERSION = 1
INIT_TIMEOUT_S = 30.0       # запуск agy (сервер языка, миграции) — секунды
REQUEST_TIMEOUT_S = 60.0    # session/new, load, resume, set_mode, set_model
CANCEL_GRACE_S = 3.0        # после session/cancel — и дерево процессов гасится
CLOSE_GRACE_S = 3.0         # после хода agy сам выходит по закрытому stdin
LOGIN_TIMEOUT_S = 30.0
_POLL_S = 0.1
_ERR_LIMIT = 500
AUTH_CODE = -32000
# Ошибки JSON-RPC «протокол не тот»: разбор, нет метода, не те параметры.
_PROTOCOL = (-32700, -32601, -32602)
HOME_DIR = "agy"                     # папка данных agy для Meet — в папке данных Meet
KEEP_WORKDIR = "meet-agy-sessions"   # рабочая папка сеансов (служебная, во временной папке)
RUN_PREFIX = "meet-run-"             # разовые папки настроек хода

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,127}")
MODEL_ERROR = ("В имени модели Antigravity недопустимые символы: только латиница, цифры и . _ - : @ + /, "
               "например gemini-3-pro")
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_MCP_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# Сценарий .cmd запускается через cmd.exe: эти символы в пути он разберёт по-своему.
_CMD_META = set('&%^|<>"!')
SHIM_UNSAFE = ("Antigravity CLI найден только как сценарий agy.cmd, а в пути к нему есть символы, "
               "которые cmd.exe понимает как команды: нужна программа agy.exe (установщик Antigravity "
               "CLI ставит её в %LOCALAPPDATA%\\agy\\bin)")
SOFT_DENIED = "отклонено: нужно согласие"
SELF_CANCELLED = "Antigravity прервал ход сам"
# Уровни рассуждений `agy --effort` (запасной путь `-p`, `agy --help` 1.5.11).
PRINT_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_EFFORT = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
# Опции сеанса ACP (`configOptions`): запасные id опций модели и уровня.
_MODEL_OPTION_IDS = ("model",)
_THOUGHT_OPTION_IDS = ("thought_level", "thinking_level", "reasoning_effort", "effort")
_SOFT_DENY = re.compile(r"(\d+)?\s*tool\(s\)\s+required approval that headless mode cannot prompt for",
                        re.IGNORECASE)
PERMISSION = "session/request_permission"


class Unavailable(Exception):
    """ACP для этого хода не годится (ход ещё не начался): `reason` — коротко."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- проверки до запуска ------------------------------------------------------------------


def model_error(value) -> str | None:
    """Негодное имя модели agy — текст для человека; пусто — годится (модель
    из настроек agy). Имя уходит в протокол, но и в командную строку
    запасного пути `-p`, а сценарий .cmd разбирает cmd.exe."""
    if value is None:
        return None
    if not isinstance(value, str):
        return MODEL_ERROR
    text = value.strip()
    if text and not _MODEL.fullmatch(text):
        return MODEL_ERROR
    return None


def launch_error(exe: str) -> str | None:
    """Запуск через сценарий .cmd/.bat с метасимволами cmd.exe в пути — отказ."""
    low = str(exe).lower()
    if _ON_WINDOWS and low.endswith((".cmd", ".bat")) and any(ch in _CMD_META for ch in str(exe)):
        return SHIM_UNSAFE
    return None


def home() -> Path:
    """Своя папка данных agy для Meet (`--gemini-dir`): в папке данных Meet —
    закрыта для агента (`consent.sensitive_paths`)."""
    from meet import paths

    return paths.data_dir() / HOME_DIR


def keep_dir() -> str:
    """Рабочая папка сеансов agy (служебная, пустая, во временной папке)."""
    path = tempdirs.system_temp() / KEEP_WORKDIR
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def user_config_dir() -> Path:
    """Общие настройки agy пользователя (`~/.gemini/config`): Meet только читает
    оттуда список MCP-серверов (`mcp_config.json`)."""
    return Path.home() / ".gemini" / "config"


def login_command(exe: str | None = None) -> str:
    """Команда входа в agy для Meet (со своей папкой данных)."""
    exe = exe or find_agy() or "agy"
    return f'"{exe}" acp --login --gemini-dir "{home()}"'


def login_hint(exe: str | None = None) -> str:
    return (f"Antigravity CLI: вход не выполнен — войдите один раз в терминале: {login_command(exe)} "
            "(Meet запускает agy со своей папкой настроек)")


def command(exe: str, flags) -> list[str]:
    """Командная строка сервера ACP (тесты подменяют на поддельный)."""
    return [exe, "acp", *flags]


# --- разовые правила хода -----------------------------------------------------------------


def _rule_path(path) -> str | None:
    text = str(path).replace("\\", "/").rstrip("/")
    if not text or any(ch in text for ch in "()\n\r"):
        return None          # скобки ломают запись правила: такой путь закрывают только ворота
    return text


def _command_prefixes() -> list[str]:
    """Шаблоны `consent.command_denial_patterns`, которые выражаются правилом
    agy `command(<префикс>)`: без `*`/`?` внутри (`rm`, `rm *`, `git push*`).
    Остальные (`curl *-d*`) решают ворота по запросу разрешения."""
    from meet.llm import consent

    out: list[str] = []
    for rule in consent.command_denial_patterns():
        body = rule[:-2] if rule.endswith(" *") else rule[:-1] if rule.endswith("*") else rule
        body = body.strip()
        if not body or "*" in body or "?" in body:
            continue
        for variant in (body, body.lower()):
            if variant not in out:
                out.append(variant)
    return out


def run_settings(*, sensitive=(), deny_paths=(), roots=(), strict: bool = False, yolo: bool = False) -> dict:
    """Разовые настройки хода (`<home>/antigravity-cli/<разовая>/settings.json`):
    режим Default (`agentMode: ""`), проверка `strict` (agy спрашивает о
    правке и командах), запреты — команды удаления, отправки и публикации
    (`command(…)`), чтение и запись закрытых папок (`kb_exclude`), путей
    `consent.sensitive_paths` и `.env` в корне рабочих папок (их agy читает
    без вопроса). Разрешающих правил нет. `strict` — ещё и запрет всех
    команд и записи (запасной путь `-p` вне хода по просьбе). `yolo` (режим
    YOLO встречи) — без запретов команд: закрытые папки и пути остаются."""
    deny = [] if yolo else [f"command({p})" for p in _command_prefixes()]
    own = _rule_path(home())
    for p in (*(deny_paths or ()), *(sensitive or ())):
        rp = _rule_path(p)
        if rp is None or (own and (rp == own or rp.startswith(own + "/"))):
            continue
        deny += [f"read_file({rp})", f"write_file({rp})"]
    for r in roots or ():
        rp = _rule_path(r)
        if rp is not None:
            deny += [f"read_file({rp}/.env)", f"write_file({rp}/.env)"]
    if strict:
        deny += ["command(*)", "write_file(*)"]
    return {"agentMode": "", "toolPermission": "strict", "permissions": {"deny": list(dict.fromkeys(deny))}}


def _write_run(settings: dict) -> tuple[str, Path]:
    """Разовая папка настроек хода → (имя для `--cli_dir_name`, папка)."""
    name = f"{RUN_PREFIX}{os.getpid()}-{uuid.uuid4().hex[:12]}"
    folder = home() / "antigravity-cli" / name
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "settings.json.tmp"
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, folder / "settings.json")
    return name, folder


def _drop_run(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


# --- MCP пользователя ---------------------------------------------------------------------


def user_mcp_config() -> dict:
    """MCP-серверы agy пользователя (`~/.gemini/config/mcp_config.json`, только
    чтение): {имя: описание}. Нет файла, не читается — пусто."""
    path = user_config_dir() / "mcp_config.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    servers = raw.get("mcpServers") if isinstance(raw.get("mcpServers"), dict) else raw
    return {k: v for k, v in servers.items() if isinstance(k, str) and isinstance(v, dict)}


def _pairs(value) -> list[dict]:
    if not isinstance(value, dict):
        return []
    return [{"name": str(k), "value": str(v)} for k, v in value.items() if isinstance(k, str)]


def acp_mcp_servers(config: dict, *, http: bool = False) -> list[dict]:
    """Описание серверов agy → `mcpServers` ACP: stdio — `{name, command, args,
    env}`; http — `{type: "http", name, url, headers}`, только если agy
    объявил `mcpCapabilities.http`. Выключенные (`disabled`) и с негодным
    именем — пропускаются."""
    out = []
    for name, spec in (config or {}).items():
        if not isinstance(spec, dict) or spec.get("disabled") is True or spec.get("enabled") is False:
            continue
        if not _MCP_NAME.match(str(name)):
            continue
        cmd = spec.get("command")
        url = spec.get("url") or spec.get("serverUrl") or spec.get("httpUrl")
        if isinstance(cmd, str) and cmd.strip():
            args = spec.get("args") if isinstance(spec.get("args"), list) else []
            out.append({"name": name, "command": cmd, "args": [str(a) for a in args],
                        "env": _pairs(spec.get("env"))})
        elif http and isinstance(url, str) and url.strip():
            out.append({"type": "http", "name": name, "url": url, "headers": _pairs(spec.get("headers"))})
    return out


def mcp_servers_for(access: str | None) -> dict:
    """MCP пользователя по уровню хода: ход по репликам (NONE) и разовые
    вызовы — нет; ход по просьбе (USER, ❓) — серверы пользователя."""
    from meet.llm import consent

    if access not in (consent.USER, consent.READ):
        return {}
    return user_mcp_config()


# --- протокол -----------------------------------------------------------------------------


class _Peer(codex_app._Peer):
    """Процесс `agy acp`: как у Codex app-server, но с полем "jsonrpc"."""

    def write(self, msg: dict) -> bool:
        return super().write({"jsonrpc": "2.0", **msg})

    def notify_params(self, method: str, params: dict) -> None:
        self.write({"method": method, "params": params})

    def start(self, method: str, params: dict) -> list:
        """Запрос без блокирующего ожидания: → слот [событие, ответ]."""
        rid, slot = self._slot()
        if not self.write({"id": rid, "method": method, "params": params}):
            slot[0].set()
        return slot


def _call(peer: _Peer, method: str, params: dict, timeout: float) -> dict:
    return peer.call(method, params, timeout)


_TERMINAL = ("completed", "failed")


def _text_of(content) -> str:
    """Текст из блоков ACP (`content` вызова: `{type: content, content: {text}}`,
    `{type: diff}`; блок сообщения `{type: text, text}`)."""
    out: list[str] = []
    for block in content if isinstance(content, list) else [content]:
        if not isinstance(block, dict):
            continue
        inner = block.get("content") if isinstance(block.get("content"), dict) else block
        if isinstance(inner.get("text"), str):
            out.append(inner["text"])
        elif block.get("type") == "diff" and block.get("path"):
            out.append(f"правка: {block['path']}")
    return "\n".join(out)


def _row_call(tool_call: dict) -> tuple[str, dict]:
    from meet.llm import consent

    call = consent.agy_call(tool_call, windows=_ON_WINDOWS)
    if call is not None:
        return call
    kind = re.sub(r"[^a-z_]", "", str(tool_call.get("kind") or "other").lower()) or "other"
    return f"agy:{kind}", {"title": " ".join(str(tool_call.get("title") or "").split())[:200]}


class _Turn:
    """Состояние хода: вызовы, строки хода работы, текст.

    Текст — по сообщениям модели: `agent_message_chunk` после вызова
    инструмента начинает новое сообщение (прежнее было
    пояснением по ходу, как у Claude Code). Ответ хода — последнее непустое
    сообщение (`answer`). `on_text(кусок)` — текст по ходу, `on_text(None)` —
    началось новое сообщение (из потока чтения: вызывающий доставляет его в
    свой цикл событий сам)."""

    def __init__(self, approver, emit, on_text=None) -> None:
        self.approver = approver
        self.emit = emit or (lambda _e: None)
        self.on_text = on_text
        self.peer: _Peer | None = None
        self.session_id: str | None = None
        self.calls: dict[str, dict] = {}
        self.open: set[str] = set()
        self.shown: set[str] = set()
        self.messages: list[list[str]] = [[]]
        self._boundary = False          # после текста был вызов инструмента
        self.lock = threading.Lock()

    def answer(self) -> str:
        """Ответ хода: последнее непустое сообщение модели."""
        for parts in reversed(self.messages):
            text = "".join(parts).strip()
            if text:
                return text
        return ""

    def _say(self, piece) -> None:
        if self.on_text is None:
            return
        try:
            self.on_text(piece)
        except Exception:
            pass

    def _message_chunk(self, text: str) -> None:
        if self._boundary and self.messages[-1]:
            self.messages.append([])
            self._say(None)
        self._boundary = False
        self.messages[-1].append(text)
        self._say(text)

    def _use(self, tid: str) -> None:
        from meet.llm.codex import _use_event

        with self.lock:
            if tid in self.shown:
                return
            self.shown.add(tid)
            self.open.add(tid)
            call = dict(self.calls.get(tid) or {})
        name, data = _row_call(call)
        self.emit(_use_event(tid, name, data))

    def _result(self, tid: str, ok: bool, output: str) -> None:
        from meet.llm.codex import _result_event

        with self.lock:
            if tid not in self.open:
                return
            self.open.discard(tid)
        self.emit(_result_event(tid, ok, output))

    def notify(self, msg: dict) -> None:
        if msg.get("method") != "session/update":
            return
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        update = params.get("update") if isinstance(params.get("update"), dict) else {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = _text_of(update.get("content"))
            if text:
                self._message_chunk(text)
            return
        if kind not in ("tool_call", "tool_call_update"):
            return
        self._boundary = True
        tid = update.get("toolCallId")
        if not isinstance(tid, str) or not tid:
            return
        with self.lock:
            merged = {**(self.calls.get(tid) or {}), **{k: v for k, v in update.items() if v is not None}}
            self.calls[tid] = merged
        self._use(tid)
        status = update.get("status")
        if status in _TERMINAL:
            output = _text_of(update.get("content"))
            if not output and update.get("rawOutput") is not None:
                output = json.dumps(update["rawOutput"], ensure_ascii=False)[:65536]
            self._result(tid, status == "completed", output)

    def request(self, msg: dict) -> None:
        """Запрос агента (в своём потоке): разрешение — воротам Meet, прочее — отказ."""
        peer = self.peer
        rid = msg.get("id")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if peer is None:
            return
        if msg.get("method") != PERMISSION:
            peer.respond(rid, error={"code": -32601, "message": "Meet этот запрос не поддерживает"})
            return
        asked = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        tid = asked.get("toolCallId") if isinstance(asked.get("toolCallId"), str) else None
        with self.lock:
            merged = {**((self.calls.get(tid) or {}) if tid else {}), **asked}
            if tid:
                self.calls[tid] = merged
        if tid:
            self._use(tid)
        allow, decision = False, None
        if self.approver is not None:
            try:
                allow, decision = self.approver(merged)
            except Exception:
                allow, decision = False, None
        if decision is not None and tid:
            from meet.llm.claude_stream import _gate_event

            name, _data = _row_call(merged)
            self.emit(_gate_event(decision, name, tid, "agy"))
        option = _option(params.get("options"), allow)
        if option is None:
            peer.respond(rid, {"outcome": {"outcome": "cancelled"}})
        else:
            peer.respond(rid, {"outcome": {"outcome": "selected", "optionId": option}})

    def abort_rows(self) -> None:
        from meet.llm.codex import INTERRUPTED

        for tid in list(self.open):
            self._result(tid, False, INTERRUPTED)


def _option(options, allow: bool) -> str | None:
    """Вариант ответа на запрос разрешения: разрешить — один раз (не «всегда»:
    «до конца встречи» помнит Meet), отклонить — один раз."""
    kinds = ("allow_once", "allow_always") if allow else ("reject_once", "reject_always")
    for kind in kinds:
        for opt in options or ():
            if isinstance(opt, dict) and opt.get("kind") == kind and isinstance(opt.get("optionId"), str):
                return opt["optionId"]
    return None


class _Stopper:
    """«Стоп» и таймаут: сеанс есть — `session/cancel`, через CANCEL_GRACE_S —
    дерево процессов; сеанса ещё нет — сразу дерево."""

    def __init__(self, peer: _Peer, proc, turn: _Turn) -> None:
        self.peer, self.proc, self.turn = peer, proc, turn
        self.stopped = False
        self._lock = threading.Lock()

    def stop(self) -> None:
        with self._lock:
            if self.stopped:
                return
            self.stopped = True
        if self.turn.session_id is None:
            kill_tree(self.proc)
            return
        timer = threading.Timer(CANCEL_GRACE_S, kill_tree, args=(self.proc,))
        timer.daemon = True
        timer.start()
        self.peer.notify_params("session/cancel", {"sessionId": self.turn.session_id})


# Вызов для остановки из цикла событий и остаток срока — те же, что у Codex.
_Call = codex_app._codex._Call
_left = codex_app._left


def _auth_error(err: "codex_app._RpcError") -> bool:
    return err.code == AUTH_CODE and "auth" in (err.message or "").lower()


@dataclass
class _Outcome:
    reply: AgentReply
    session_id: str | None = None        # сеанс хода (для уборки разового)
    auth: bool = False
    closable: bool = False
    note: str | None = None              # строка в журнал (без текста встречи)


def run_turn(cmd: list[str], *, env: dict | None, cwd: str, text: str, resume: str | None = None,
             keep_session: bool = False, model: str | None = None, mcp: dict | None = None,
             approver=None, emit=None, timeout_s: float = 180.0, call: _Call | None = None,
             exe: str | None = None, on_text=None, effort: str | None = None) -> _Outcome:
    """Один ход через `agy acp` (блокирующее; см. модуль). Не подошёл до
    начала сеанса — `Unavailable`. `approver(toolCall) -> (разрешить?,
    решение ворот | None)`; `emit` — события хода работы; `on_text` — текст
    модели по ходу (`_Turn`), из потока чтения."""
    deadline = time.monotonic() + timeout_s
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, **popen_kwargs())
    except OSError as e:
        raise Unavailable(f"не запустился ({type(e).__name__})") from None
    turn = _Turn(approver, emit, on_text)
    peer = _Peer(proc, on_notify=turn.notify, on_request=turn.request)
    turn.peer = peer
    stopper = _Stopper(peer, proc, turn)
    if call is not None:
        call.proc = proc
        call.stop = stopper.stop
        if call.cancelled:
            stopper.stop()
    outcome = None
    try:
        outcome = _drive(peer, turn, stopper, cwd=cwd, text=text, resume=resume, keep_session=keep_session,
                         model=model, mcp=mcp or {}, deadline=deadline, call=call, exe=exe, effort=effort)
        if outcome.closable and outcome.session_id and not keep_session and not resume:
            try:
                _call(peer, "session/close", {"sessionId": outcome.session_id}, 5.0)
            except (codex_app._NoAnswer, codex_app._RpcError):
                pass
        return outcome
    finally:
        _close(peer, proc)


def _drive(peer: _Peer, turn: _Turn, stopper: _Stopper, *, cwd, text, resume, keep_session, model, mcp,
           deadline: float, call, exe, effort=None) -> _Outcome:
    cancelled = (lambda: bool(call is not None and call.cancelled))
    sid_out = (lambda sid: sid if (keep_session or resume) else None)
    # 1. Рукопожатие.
    try:
        init = _call(peer, "initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "meet", "title": "Meet", "version": codex_app._client_version()},
        }, _left(deadline, INIT_TIMEOUT_S))
    except codex_app._NoAnswer:
        if cancelled():
            return _Outcome(AgentReply(text="", error=CANCELLED_ERROR, cancelled=True, session_id=resume))
        raise Unavailable("нет ответа на initialize") from None
    except codex_app._RpcError as e:
        raise Unavailable(f"ошибка {e.code} в initialize") from None
    if init.get("protocolVersion") != PROTOCOL_VERSION:
        raise Unavailable(f"версия протокола {init.get('protocolVersion')!r}")
    caps = init.get("agentCapabilities") if isinstance(init.get("agentCapabilities"), dict) else {}
    sess_caps = caps.get("sessionCapabilities") if isinstance(caps.get("sessionCapabilities"), dict) else {}
    mcp_caps = caps.get("mcpCapabilities") if isinstance(caps.get("mcpCapabilities"), dict) else {}
    servers = acp_mcp_servers(mcp, http=mcp_caps.get("http") is True)
    # 2. Сеанс.
    if resume:
        method = ("session/resume" if "resume" in sess_caps
                  else "session/load" if caps.get("loadSession") or "load" in sess_caps else None)
        if method is None:
            return _Outcome(resume_failure("Antigravity не умеет продолжать сеанс"))
        params = {"sessionId": resume, "cwd": cwd, "mcpServers": servers}
    else:
        method, params = "session/new", {"cwd": cwd, "mcpServers": servers}
    try:
        result = _call(peer, method, params, _left(deadline, REQUEST_TIMEOUT_S))
    except codex_app._NoAnswer:
        if cancelled():
            return _Outcome(AgentReply(text="", error=CANCELLED_ERROR, cancelled=True, session_id=resume))
        raise Unavailable(f"нет ответа на {method}") from None
    except codex_app._RpcError as e:
        if _auth_error(e):
            return _Outcome(AgentReply(text="", error=login_hint(exe)), auth=True)
        if resume and e.code not in _PROTOCOL:
            return _Outcome(resume_failure(e.message[-_ERR_LIMIT:] or f"Antigravity не продолжил сеанс {resume}"))
        raise Unavailable(f"ошибка {e.code} в {method}") from None
    reported = result.get("sessionId") if isinstance(result.get("sessionId"), str) else None
    if resume and reported and reported != resume:
        return _Outcome(resume_failure(f"Antigravity продолжил другой сеанс ({reported}) вместо {resume}"))
    sid = resume or reported
    if not sid or not _SESSION_ID.match(sid):
        raise Unavailable(f"нет id сеанса в ответе {method}")
    turn.session_id = sid
    closable = "close" in sess_caps
    # Режим сеанса: только Default — в нём agy спрашивает о действиях.
    modes = result.get("modes") if isinstance(result.get("modes"), dict) else {}
    current = modes.get("currentModeId")
    if isinstance(current, str) and current and current != "default":
        try:
            _call(peer, "session/set_mode", {"sessionId": sid, "modeId": "default"},
                  _left(deadline, REQUEST_TIMEOUT_S))
        except (codex_app._NoAnswer, codex_app._RpcError):
            return _Outcome(AgentReply(text="", error="Antigravity не перешёл в режим с вопросами — ход не начат",
                                       session_id=sid_out(sid)), sid, closable=closable)
    options = result.get("configOptions")
    if model:
        try:
            changed = _call(peer, "session/set_model", {"sessionId": sid, "modelId": model},
                            _left(deadline, REQUEST_TIMEOUT_S))
            # Опции сеанса после смены модели (у другой модели — свои уровни).
            if isinstance(changed.get("configOptions"), list):
                options = changed["configOptions"]
        except codex_app._NoAnswer:
            return _Outcome(AgentReply(text="", error=TIMEOUT_ERROR, session_id=sid_out(sid)), sid,
                            closable=closable)
        except codex_app._RpcError as e:
            return _Outcome(AgentReply(text="", error=f"Antigravity не принял модель {model}: "
                                                      f"{e.message[-_ERR_LIMIT:]}", session_id=sid_out(sid)),
                            sid, closable=closable)
    note = _apply_effort(peer, sid, options, effort, deadline) if effort else None
    if cancelled():
        return _Outcome(AgentReply(text="", error=CANCELLED_ERROR, cancelled=True, session_id=sid_out(sid)),
                        sid, closable=closable, note=note)
    outcome = _prompt(peer, turn, stopper, sid=sid, sid_out=sid_out, closable=closable, text=text,
                      deadline=deadline, cancelled=cancelled, exe=exe)
    outcome.note = note
    return outcome


def _apply_effort(peer: _Peer, sid: str, options, effort: str, deadline: float) -> str | None:
    """Уровень рассуждений сеанса — `session/set_config_option` опции
    категории `thought_level`. Опции нет или agy уровень не принял — ход без
    него; → строка для журнала (без текста встречи) или None."""
    option = config_option(options, "thought_level", _THOUGHT_OPTION_IDS)
    if option is None:
        return "Antigravity: уровень рассуждений не задан — agy не сообщил такой опции сеанса"
    values = [o["value"] for o in option_values(option.get("options"))]
    if values and effort not in values:
        return f"Antigravity: уровня рассуждений {effort} нет у этой модели — ход без него"
    try:
        _call(peer, "session/set_config_option", {"sessionId": sid, "configId": option["id"], "value": effort},
              _left(deadline, REQUEST_TIMEOUT_S))
    except codex_app._NoAnswer:
        return "Antigravity: нет ответа на смену уровня рассуждений — ход без него"
    except codex_app._RpcError as e:
        return f"Antigravity: уровень рассуждений не принят (ошибка {e.code}) — ход без него"
    return None


def _prompt(peer: _Peer, turn: _Turn, stopper: _Stopper, *, sid, sid_out, closable, text, deadline, cancelled,
            exe) -> _Outcome:
    """Ход в готовом сеансе: `session/prompt` и ожидание ответа."""
    # 3. Ход.
    slot = peer.start("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": text}]})
    timed_out = False
    while not slot[0].wait(_POLL_S):
        if peer.closed.is_set() and not slot[0].is_set():
            break
        if cancelled() and not stopper.stopped:
            stopper.stop()
        if time.monotonic() > deadline and not timed_out:
            timed_out = True
            stopper.stop()
        if stopper.stopped and time.monotonic() > deadline + CANCEL_GRACE_S + 1:
            break
    turn.abort_rows()
    answer = turn.answer()
    if cancelled():
        return _Outcome(AgentReply(text="", error=CANCELLED_ERROR, cancelled=True, session_id=sid_out(sid)),
                        sid, closable=closable)
    if timed_out:
        return _Outcome(AgentReply(text="", error=TIMEOUT_ERROR, session_id=sid_out(sid)), sid, closable=closable)
    msg = slot[1]
    if not isinstance(msg, dict):
        err = peer.stderr_tail()
        return _Outcome(AgentReply(text="", error=err[-_ERR_LIMIT:] or "Antigravity завершился посреди хода",
                                   session_id=sid_out(sid)), sid, closable=closable)
    if "error" in msg:
        error = msg["error"] if isinstance(msg["error"], dict) else {}
        err = codex_app._RpcError(error.get("code"), str(error.get("message") or ""))
        if _auth_error(err):
            return _Outcome(AgentReply(text="", error=login_hint(exe)), sid, auth=True, closable=closable)
        return _Outcome(AgentReply(text="", error=err.message[-_ERR_LIMIT:] or "ход не удался",
                                   session_id=sid_out(sid)), sid, closable=closable)
    result = msg.get("result") if isinstance(msg.get("result"), dict) else {}
    stop = result.get("stopReason")
    if stop == "cancelled":
        # Остановки Meet не было (она — выше): agy прервал ход сам. Это сбой хода,
        # а не «Стоп»: иначе ход по репликам повторялся бы без паузы.
        return _Outcome(AgentReply(text="", error=SELF_CANCELLED, session_id=sid_out(sid)),
                        sid, closable=closable)
    if stop == "refusal":
        return _Outcome(AgentReply(text="", error="модель отказалась отвечать", session_id=sid_out(sid)),
                        sid, closable=closable)
    if not answer:
        why = EMPTY_ERROR if stop in (None, "end_turn") else f"ход остановлен ({stop})"
        return _Outcome(AgentReply(text="", error=why, session_id=sid_out(sid)), sid, closable=closable)
    return _Outcome(AgentReply(text=answer, session_id=sid_out(sid)), sid, closable=closable)


def _close(peer: _Peer, proc) -> None:
    """Конец хода: stdin закрыт — agy выходит сам; не вышел за CLOSE_GRACE_S —
    дерево процессов гасится."""
    codex_app._close(peer, proc, grace=CLOSE_GRACE_S)


# --- ворота -------------------------------------------------------------------------------


def _norm(path) -> str:
    text = os.path.normpath(os.path.abspath(str(path)))
    return text.lower() if _ON_WINDOWS or os.name == "nt" else text


def _readonly_approver(roots):
    """Без ворот (итоги, названия, вызов без свободы): только чтение и поиск
    внутри `roots` (кроме `.env`), остальное — отказ."""
    from meet.llm import consent

    normalized = [_norm(r) for r in roots if r]

    def inside(path: str) -> bool:
        p = _norm(path)
        name = os.path.basename(p)
        if (name == ".env" or name.startswith(".env.")) and name != ".env.example":
            return False
        return any(p == r or p.startswith(r.rstrip("\\/") + os.sep) for r in normalized)

    def approve(tool_call: dict) -> tuple[bool, None]:
        call = consent.agy_call(tool_call, windows=_ON_WINDOWS)
        if call is None:
            return False, None
        tool, data = call
        if tool == "TodoWrite":
            return True, None
        if tool in ("Read", "Grep"):
            path = data.get("file_path") or data.get("path")
            return bool(path) and inside(str(path)), None
        return False, None
    return approve


def _gate_approver(gate):
    from meet.llm import consent

    def approve(tool_call: dict):
        decision = consent.agy_approval(gate, tool_call, windows=_ON_WINDOWS)
        return decision.allow, decision
    return approve


# --- запасной путь: -p --output-format stream-json -----------------------------------------


@dataclass
class Stream:
    session_id: str | None = None
    text: str = ""
    events: list[dict] = field(default_factory=list)
    error: str | None = None


_PRINT_TOOLS = {"run_command": "Bash", "view_file": "Read", "read_file": "Read", "list_dir": "Read",
                "write_to_file": "Write", "replace_file_content": "Write", "edit_file": "Write",
                "grep_search": "Grep", "search_web": "WebSearch", "read_url_content": "WebFetch"}


def _print_call(event: dict) -> tuple[str, dict]:
    name = str(event.get("tool_name") or event.get("name") or "")
    args = next((event[k] for k in ("tool_call", "args", "input", "arguments") if isinstance(event.get(k), dict)),
                {})
    meet_name = _PRINT_TOOLS.get(name)
    if meet_name == "Bash":
        return "Bash", {"command": str(args.get("CommandLine") or args.get("command") or "")}
    if meet_name in ("Read", "Write"):
        path = next((args[k] for k in ("AbsolutePath", "TargetFile", "path", "file_path", "DirectoryPath")
                     if isinstance(args.get(k), str)), "")
        return meet_name, {"file_path": path}
    if meet_name:
        return meet_name, dict(args)
    return f"agy:{re.sub(r'[^A-Za-z0-9_]', '', name)[:64] or 'tool'}", dict(args)


def parse_stream(stdout: str, stderr: str) -> Stream:
    """События `agy -p --output-format stream-json` (`init` — id сеанса,
    `step_update` со `step_type: tool` — вызов и его итог, `result` — ответ)
    и пометка отказа без согласия из stderr — строками хода «отклонено:
    нужно согласие». Чужие строки и поля пропускаются."""
    from meet.llm.codex import _result_event, _use_event

    out = Stream()
    shown: set[str] = set()
    done: set[str] = set()
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        sid = event.get("session_id") or event.get("conversation_id")
        if isinstance(sid, str) and sid and out.session_id is None:
            out.session_id = sid
        if kind == "step_update" and event.get("step_type") == "tool":
            key = str(event.get("tool_call_id") or event.get("step_index") or len(shown))
            iid = f"agy-{key}"
            if iid not in shown:
                shown.add(iid)
                out.events.append(_use_event(iid, *_print_call(event)))
            status = str(event.get("status") or "").lower()
            if status in ("done", "completed", "success", "succeeded", "error", "failed", "denied") \
                    and iid not in done:
                done.add(iid)
                ok = status in ("done", "completed", "success", "succeeded")
                result = event.get("result") if event.get("result") is not None else event.get("output")
                text = result if isinstance(result, str) else (
                    json.dumps(result, ensure_ascii=False) if result is not None else "")
                out.events.append(_result_event(iid, ok, text[:65536]))
        elif kind == "result":
            text = next((event[k] for k in ("result", "text", "response") if isinstance(event.get(k), str)), "")
            out.text = text.strip()
            reason = event.get("finished_reason")
            if isinstance(reason, str) and reason and reason != "print_timeout" and not out.text:
                out.error = f"ход остановлен ({reason})"
    found = _SOFT_DENY.search(stderr or "")
    if found:
        count = max(1, min(int(found.group(1) or 1), 10))
        for n in range(count):
            iid = f"agy-denied-{n}"
            out.events.append(_use_event(iid, "agy:denied", {"title": SOFT_DENIED}))
            out.events.append(_result_event(iid, False, SOFT_DENIED))
    return out


def _print_run(exe: str, argv: list[str], stdin_text: str, timeout_s: float, env: dict,
               call: _Call) -> tuple[int, str, str] | None:
    """Процесс `agy -p` (stdin — промпт); не уложился или отменён — дерево
    процессов убито, → None."""
    try:
        proc = subprocess.Popen([exe, *argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, **popen_kwargs())
    except OSError as e:
        return 127, "", f"{type(e).__name__}: {e}"
    call.proc = proc
    if call.cancelled:
        kill_tree(proc)
    try:
        out, err = proc.communicate(stdin_text.encode("utf-8"), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            proc.communicate(timeout=10)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        return None
    return proc.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def _print_turn(exe: str, *, env: dict, text: str, resume: str | None, keep_session: bool, model: str | None,
                access: str | None, add_dirs: list[str], sensitive, deny_paths, timeout_s: float,
                call: _Call, emit, yolo: bool = False, effort: str | None = None) -> AgentReply:
    """Ход через `agy -p` (ACP недоступен): спросить человека agy здесь не
    может — несогласованное отклоняет сам; ход по просьбе — только чтение и
    разрешённое правилами. Настройки пользователя `-p` загружает (флага
    своей папки данных у `-p` проверить не удалось), поэтому вне хода по
    просьбе разовые правила запрещают ещё и любые команды и запись."""
    from meet.llm import consent

    settings = run_settings(sensitive=sensitive, deny_paths=deny_paths, roots=add_dirs,
                            strict=access != consent.USER, yolo=yolo)
    folder = Path(tempdirs.system_temp()) / f"{RUN_PREFIX}{os.getpid()}-{uuid.uuid4().hex[:12]}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "settings.json"
    path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    argv = ["-p", "", "--input-format", "text", "--output-format", "stream-json", "--settings", str(path)]
    if model:
        argv += ["--model", model]
    if effort in PRINT_EFFORTS:
        argv += ["--effort", effort]
    if resume:
        argv += ["--conversation", resume]
    for d in add_dirs:
        argv += ["--add-dir", d]
    try:
        got = _print_run(exe, argv, text, timeout_s, env, call)
    finally:
        _drop_run(folder)
    if call.cancelled:
        return AgentReply(text="", error=CANCELLED_ERROR, cancelled=True, session_id=resume)
    if got is None:
        return AgentReply(text="", error=TIMEOUT_ERROR, session_id=resume)
    code, out, err = got
    parsed = parse_stream(out, err)
    for event in parsed.events:
        emit(event)
    if resume and parsed.session_id and parsed.session_id != resume:
        return resume_failure(f"Antigravity продолжил другой сеанс ({parsed.session_id}) вместо {resume}")
    if resume and code != 0 and re.search(r"conversation|not found", err or "", re.IGNORECASE) \
            and not parsed.text:
        return resume_failure(err[-_ERR_LIMIT:])
    sid = (parsed.session_id or resume) if (keep_session or resume) else None
    if "authentication required" in (err or "").lower() or "not logged in" in (err or "").lower():
        return AgentReply(text="", error=login_hint(exe), session_id=sid)
    if parsed.text:
        return AgentReply(text=parsed.text, session_id=sid, tools=parsed.events)
    error = parsed.error or (err.strip()[-_ERR_LIMIT:] if err and err.strip() else "") or (
        f"Antigravity завершился с кодом {code}" if code else EMPTY_ERROR)
    reply = AgentReply(text="", error=error, session_id=sid, tools=parsed.events)
    if not keep_session and not resume and parsed.session_id:
        forget_session(parsed.session_id)
    return reply


# --- вызов --------------------------------------------------------------------------------


def _add_dirs(access: str | None, allowed_dirs, work_dirs) -> list[str]:
    from meet.llm import consent

    if access is None:
        dirs = list(allowed_dirs or ())
    elif access == consent.USER:
        dirs = list(work_dirs or ()) or list(allowed_dirs or ())[:1]
    else:
        dirs = list(allowed_dirs or ())[:1]
    return [str(d) for d in dict.fromkeys(str(x) for x in dirs if x) if Path(d).is_dir()]


async def run(
    prompt: str,
    *,
    system_prompt: str,
    model: str | None = None,
    resume: str | None = None,
    session_id: str | None = None,
    allowed_dirs: tuple = (),
    cwd: str | Path | None = None,
    timeout_s: float = 180.0,
    max_turns: int = 8,
    on_text=None,
    proxy: str | None = None,
    effort: str | None = None,
    images=(),
    keep_session: bool = False,
    deny_paths=(),
    access: str | None = None,
    work_dirs=(),
    on_event=None,
    gate=None,
    log=None,
) -> AgentReply:
    """Один ход Antigravity CLI; ошибки — в AgentReply.error.

    `keep_session` — сохранить сеанс и вернуть его id; `resume` — продолжить
    сохранённый (id проверяется, неверный — `resume_failed` без запуска).
    Без них — разовый вызов: сеанс закрывается и удаляется после хода.
    `access` — уровень хода агента-участника (NONE / READ / USER), `gate` —
    его ворота согласия: запросы разрешения agy решают они и карточки Meet;
    без ворот разрешено только чтение `allowed_dirs`. `work_dirs` — рабочие
    папки хода USER (`--add-dir`). `deny_paths` — запрет в разовых правилах и
    просьба в тексте. `model` — `session/set_model` (имя проверяется).
    `on_event` — ход работы по ходу вызова (`tool_use`, `gate`,
    `tool_result`); `on_text` — текст модели по ходу (`agent_message_chunk`;
    `on_text(None)` — новое сообщение после вызова инструмента), оба — в
    цикл событий вызывающего. Изображения agy не уходят (пометка в тексте и
    `notes`). Отмена задачи («Стоп») — `session/cancel`, затем дерево
    процессов. `effort` — уровень рассуждений (опция сеанса `thought_level`,
    в `-p` — `--effort`; негодное имя не передаётся). `max_turns`,
    `session_id`, `cwd` — не используются. ACP не подошёл до начала сеанса — ход через `agy -p`
    (`_print_turn`), в `log` — строка с причиной (без текста встречи)."""
    from meet.llm import consent

    if resume and not _SESSION_ID.match(str(resume)):
        return resume_failure(f"неверный id сеанса Antigravity: {resume!r}")
    if model_error(model):
        return AgentReply(text="", error=model_error(model))
    model = model.strip() if isinstance(model, str) and model.strip() else None
    effort = effort if isinstance(effort, str) and _EFFORT.fullmatch(effort) else None
    exe = find_agy()
    if exe is None:
        return AgentReply(text="", error=AGY_NOT_FOUND)
    shim = launch_error(exe)
    if shim:
        return AgentReply(text="", error=shim)
    dropped = [str(p) for p in images or ()]
    notes = [NO_VISION_NOTE] if dropped else []
    body = "\n".join([prompt, *[f"({n})" for n in notes]]) if notes else prompt
    rule = deny_prompt(deny_paths)
    if rule:
        body = f"{body}\n\n{rule}"
    text = body if resume else f"{system_prompt}\n\n{body}"
    env = netproxy.child_env(proxy)
    drop_session_markers(env)
    sensitive = await asyncio.to_thread(consent.sensitive_paths) if gate is not None or access is not None else []
    sensitive = [p for p in sensitive if Path(p).exists()]
    add_dirs = _add_dirs(access, allowed_dirs, work_dirs)
    workdir = await asyncio.to_thread(keep_dir)
    roots = [*add_dirs, workdir]
    approver = _gate_approver(gate) if gate is not None else _readonly_approver(roots)
    yolo = gate is not None and gate.yolo       # режим YOLO встречи: без запретов команд
    settings = run_settings(sensitive=sensitive, deny_paths=deny_paths, roots=add_dirs, yolo=yolo)
    run_name, run_folder = await asyncio.to_thread(_write_run, settings)
    flags = ["--gemini-dir", str(home()), "--cli_dir_name", run_name, "--disable-telemetry"]
    for d in add_dirs:
        flags += ["--add-dir", d]
    mcp = await asyncio.to_thread(mcp_servers_for, access)
    loop = asyncio.get_running_loop()
    from meet.llm.codex import _deliver

    emit = _deliver(loop, on_event) if on_event is not None else (lambda _e: None)
    said = _deliver(loop, on_text) if on_text is not None else None
    call = _Call()
    outcome = None
    try:
        try:
            outcome = await asyncio.to_thread(
                run_turn, command(exe, flags), env=env, cwd=workdir, text=text, resume=resume,
                keep_session=keep_session, model=model, mcp=mcp, approver=approver, emit=emit,
                timeout_s=timeout_s, call=call, exe=exe, on_text=said, effort=effort)
        except Unavailable as e:
            if log is not None:
                try:
                    log(f"Antigravity: agy acp не подошёл ({e.reason}) — ход через agy -p")
                except Exception:
                    pass
            reply = await asyncio.to_thread(
                _print_turn, exe, env=env, text=text, resume=resume, keep_session=keep_session, model=model,
                access=access, add_dirs=add_dirs, sensitive=sensitive, deny_paths=deny_paths,
                timeout_s=timeout_s, call=call, emit=emit, yolo=yolo, effort=effort)
            reply.error = netproxy.with_hint(reply.error)
            reply.dropped_images, reply.notes = dropped, notes
            return reply
    except asyncio.CancelledError:
        call.cancel()
        raise
    finally:
        await asyncio.to_thread(_drop_run, run_folder)
        if outcome is not None and outcome.session_id and not keep_session and not resume:
            await asyncio.to_thread(forget_session, outcome.session_id)
    if outcome.note and log is not None:
        try:
            log(outcome.note)
        except Exception:
            pass
    reply = outcome.reply
    reply.error = netproxy.with_hint(reply.error)
    reply.dropped_images, reply.notes = dropped, notes
    return reply


# --- вход и удаление сеансов --------------------------------------------------------------


def _probe_session(exe: str, proxy: str | None = None) -> tuple[dict | None, str | None, bool]:
    """Служебный сеанс без модели: `initialize` и `session/new` в своей папке
    данных Meet; созданный сеанс закрывается и удаляется. → (ответ
    `session/new`, None, False) или (None, текст ошибки, нет ли входа)."""
    shim = launch_error(exe)
    if shim:
        return None, shim, False
    env = netproxy.child_env(proxy)
    drop_session_markers(env)
    flags = ["--gemini-dir", str(home()), "--disable-telemetry"]
    probe = str(Path(tempdirs.system_temp()) / KEEP_WORKDIR)
    Path(probe).mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(command(exe, flags), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, **popen_kwargs())
    except OSError as e:
        return None, f"Antigravity CLI не запустился ({type(e).__name__})", False
    peer = _Peer(proc, on_notify=lambda _m: None,
                 on_request=lambda m: peer.respond(m.get("id"), error={"code": -32601, "message": "нет"}))
    sid = None
    try:
        try:
            _call(peer, "initialize", {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {},
                                       "clientInfo": {"name": "meet", "version": codex_app._client_version()}},
                  LOGIN_TIMEOUT_S)
            result = _call(peer, "session/new", {"cwd": probe, "mcpServers": []}, LOGIN_TIMEOUT_S)
        except codex_app._NoAnswer:
            return None, "Antigravity CLI не ответил (agy acp)", False
        except codex_app._RpcError as e:
            if _auth_error(e):
                return None, login_hint(exe), True
            return None, (e.message or f"ошибка {e.code}")[-_ERR_LIMIT:], False
        sid = result.get("sessionId") if isinstance(result.get("sessionId"), str) else None
        if sid:
            try:
                _call(peer, "session/close", {"sessionId": sid}, 5.0)
            except (codex_app._NoAnswer, codex_app._RpcError):
                pass
        return result, None, False
    finally:
        _close(peer, proc)
        if sid:
            forget_session(sid)


def login_status(exe: str) -> tuple[bool, str | None]:
    """Есть ли вход в agy для Meet — без модели: `initialize` и `session/new`
    в своей папке данных (без входа — -32000 «Authentication required»);
    созданный сеанс закрывается и удаляется. → (True, None) или (False, текст)."""
    result, error, _auth = _probe_session(exe)
    return (True, None) if result is not None else (False, error)


# --- модели и уровни рассуждений ----------------------------------------------------------


def option_values(options) -> list[dict]:
    """Значения опции сеанса ACP: плоский список `{value, name, description}`
    или группы `{group, name, options: [...]}` — одним списком."""
    out: list[dict] = []
    for item in options if isinstance(options, list) else ():
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("options"), list):
            out += option_values(item["options"])
        elif isinstance(item.get("value"), str) and item["value"]:
            out.append({"value": item["value"], "name": str(item.get("name") or item["value"]),
                        "description": str(item.get("description") or "")})
    return out


def config_option(options, category: str, ids=()) -> dict | None:
    """Опция сеанса ACP (`configOptions`) нужной категории (`model`,
    `thought_level`); у опции без категории — по id. Нет — None."""
    items = [o for o in options if isinstance(o, dict) and isinstance(o.get("id"), str)] \
        if isinstance(options, list) else []
    for item in items:
        if item.get("category") == category:
            return item
    for item in items:
        if item.get("id") in ids and not item.get("category"):
            return item
    return None


def session_models(result: dict) -> dict:
    """Модели и уровни рассуждений из ответа `session/new`: `models`
    (`currentModelId`, `availableModels: [{modelId, name, description}]`) и/или
    `configOptions` (категории `model` и `thought_level`). → `{"models": [{id,
    label, description?, default, efforts, default_effort?}]}`; уровни —
    опции сеанса, у всех моделей одни, текущий — у модели по умолчанию.
    Имена вне правил (`model_error`, `_EFFORT`) пропускаются."""
    options = result.get("configOptions") if isinstance(result, dict) else None
    found: dict[str, dict] = {}
    current = None
    state = result.get("models") if isinstance(result, dict) and isinstance(result.get("models"), dict) else {}
    for item in state.get("availableModels") or ():
        if isinstance(item, dict) and isinstance(item.get("modelId"), str):
            found.setdefault(item["modelId"], {"name": str(item.get("name") or item["modelId"]),
                                               "description": str(item.get("description") or "")})
    if isinstance(state.get("currentModelId"), str):
        current = state["currentModelId"]
    model_opt = config_option(options, "model", _MODEL_OPTION_IDS)
    if model_opt is not None:
        for item in option_values(model_opt.get("options")):
            found.setdefault(item["value"], {"name": item["name"], "description": item["description"]})
        if isinstance(model_opt.get("currentValue"), str):
            current = current or model_opt["currentValue"]
    thought = config_option(options, "thought_level", _THOUGHT_OPTION_IDS)
    efforts = []
    effort_now = None
    if thought is not None:
        efforts = [{"id": o["value"], "label": o["name"],
                    **({"description": o["description"]} if o["description"] else {})}
                   for o in option_values(thought.get("options")) if _EFFORT.fullmatch(o["value"])]
        value = thought.get("currentValue")
        effort_now = value if isinstance(value, str) and any(e["id"] == value for e in efforts) else None
    models = []
    for mid, info in found.items():
        if model_error(mid) or not mid.strip():
            continue
        entry = {"id": mid, "label": info["name"], "default": mid == current, "efforts": list(efforts)}
        if info["description"]:
            entry["description"] = info["description"]
        if mid == current and effort_now:
            entry["default_effort"] = effort_now
        models.append(entry)
    return {"models": models}


def list_models(exe: str, proxy: str | None = None) -> dict:
    """Модели и уровни рассуждений agy для окна настроек — из ответа служебного
    `session/new` (без модели, как `login_status`). → `{"models": [...]}` или
    `{"models": [], "error": текст}`; нет входа — ещё `login` (команда входа).
    Запасного пути нет: `agy models` не принимает `--gemini-dir` (1.5.11), а
    без неё показал бы модели не той папки данных."""
    result, error, auth = _probe_session(exe, proxy)
    if result is None:
        return {"models": [], "error": error, **({"login": login_command(exe)} if auth else {})}
    got = session_models(result)
    if not got["models"]:
        return {"models": [], "error": "Antigravity CLI не сообщил список моделей — задайте имя вручную"}
    return got


def forget_session(session_id: str) -> int:
    """Удалить сохранённый сеанс agy из своей папки данных Meet: файлы
    разговора `antigravity-cli/conversations/<id>.*` и папку
    `antigravity-cli/brain/<id>`. Команды удаления у agy нет; раскладка — по
    журналу изменений agy (не проверена на живом сеансе). Только годный id;
    → сколько удалено."""
    if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):
        return 0
    base = home() / "antigravity-cli"
    removed = 0
    conv = base / "conversations"
    try:
        files = [p for p in conv.iterdir() if p.name == session_id or p.name.startswith(session_id + ".")]
    except OSError:
        files = []
    for f in files:
        try:
            if f.is_symlink() or f.is_file():
                f.unlink()
                removed += 1
        except OSError:
            pass
    brain = base / "brain" / session_id
    if brain.is_dir() and not brain.is_symlink():
        shutil.rmtree(brain, ignore_errors=True)
        if not brain.exists():
            removed += 1
    return removed
