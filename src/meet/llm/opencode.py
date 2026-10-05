"""Провайдер OpenCode: `opencode run --format json` с агентом только для чтения.

Один вызов — один процесс `opencode run`; память диалога вызывающий кладёт в
prompt (`resume`/`session_id` не используются, как у Codex). Флаги и формат
сверены с документацией (opencode.ai/docs/cli, /permissions, /agents,
/config) и исходниками opencode (packages/opencode/src/cli/cmd/run.ts,
agent/agent.ts, permission/index.ts, 2026-10):

* `--format json` — по строке JSON на событие `{type, timestamp, sessionID,
  …}`: `step_start`, `text` (part.text — готовый кусок текста), `tool_use`,
  `step_finish` (part.reason: `stop` или `tool-calls`), `reasoning`, `error`
  (error.name, error.data.message). В документации формат не описан —
  разбираем осторожно: чужие строки и поля пропускаем;
* права — свой агент `meet-readonly` в `OPENCODE_CONFIG_CONTENT`: сначала
  `"*": "deny"`, потом разрешённое чтение (read/grep/glob/list) и доступ к
  разрешённым папкам (`external_directory`). Правила агента идут после
  правил пользователя и побеждают (последнее подходящее). Запрос «ask» в
  `opencode run` без `--auto` отклоняется сам;
* рабочая папка — служебная пустая (не папка встречи): сеансы фоновых
  вызовов не попадают в «Продолжить» вкладки «Агент», а настройки проекта
  (opencode.json, AGENTS.md) не подхватываются;
* сеанс после ответа удаляется (`opencode session delete`): у `run` нет
  режима «не сохранять», а текст встречи не должен оседать в истории.
"""

import asyncio
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from meet import netproxy, tempdirs
from meet.llm.base import EMPTY_ERROR, TIMEOUT_ERROR, AgentReply, drop_session_markers
from meet.llm.codex import _kill_tree
from meet.llm.detect import OPENCODE_NOT_FOUND, find_opencode

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_ERR_LIMIT = 500
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Свой агент фоновых вызовов (в конфиге из переменной среды — у человека его нет).
AGENT = "meet-readonly"
# Заголовок сеанса: свой, чтобы OpenCode не звал модель придумывать название.
TITLE = "meet"
WORKDIR = "meet-opencode"
# Системный промпт — в конфиг агента (в переменную среды); длиннее — в stdin,
# как у Codex: одна переменная среды Windows — до 32 767 символов.
PROMPT_ENV_LIMIT = 16_000
SHORT_SYSTEM = ("Ты помогаешь приложению meet с записью встречи. Следуй инструкции в "
                "начале сообщения. Файлы не изменяй, команды не выполняй.")
# Переменные, которые меняют поведение вызова в обход нашего конфига.
_DROP_ENV = ("OPENCODE_CONFIG_CONTENT", "OPENCODE_AUTO_SHARE")
_SET_ENV = {
    # opencode.json и AGENTS.md из рабочей папки и выше не читаются.
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    # ~/.claude/CLAUDE.md и навыки Claude Code — не для фоновых вызовов.
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "NO_COLOR": "1",
}
AUTH_HINT = "нет доступа к провайдеру модели — проверьте вход: opencode auth login"
_AUTH_MARKERS = re.compile(
    r"unauthori[sz]ed|authenticat|api[ _-]?key|x-api-key|credential|not logged in|token expired",
    re.IGNORECASE)


def readonly_permission(dirs) -> dict:
    """Права агента фоновых вызовов. Без папок — никаких инструментов (как у
    Claude Code: анализ, названия, тики). С папками — только чтение в них:
    read (кроме .env), grep, glob, list и доступ к этим папкам вне рабочей;
    правка, команды, сеть, подагенты — запрещены (`"*": "deny"` первым)."""
    dirs = [Path(d) for d in dirs if d]
    if not dirs:
        return {"*": "deny"}
    external = {"*": "deny"}
    for d in dirs:
        external[str(d / "*")] = "allow"
    return {
        "*": "deny",
        "read": {"*": "allow", "*.env": "deny", "*.env.*": "deny", "*.env.example": "allow"},
        "grep": "allow",
        "glob": "allow",
        "list": "allow",
        "external_directory": external,
    }


def config_content(prompt: str, dirs, max_turns: int) -> dict:
    """Конфиг поверх конфига человека (`OPENCODE_CONFIG_CONTENT`): агент только
    для чтения, без публикации сеанса, снимков файлов и обновления."""
    return {
        "share": "disabled",
        "snapshot": False,
        "autoupdate": False,
        "agent": {
            AGENT: {
                "mode": "primary",
                "hidden": True,
                "description": "meet: фоновые вызовы, только чтение",
                "prompt": prompt,
                "steps": max(1, int(max_turns)),
                "permission": readonly_permission(dirs),
            },
        },
    }


def build_command(exe: str, workdir: str, model: str | None) -> list[str]:
    """`opencode run`. Модель — только «провайдер/модель» (`llm.opencode_model`);
    иначе — модель из конфига OpenCode. Сообщение — в stdin."""
    cmd = [exe, "run", "--format", "json", "--agent", AGENT, "--title", TITLE, "--dir", workdir]
    if model and "/" in model:
        cmd += ["-m", model]
    return cmd


def child_env(proxy: str | None, config: dict) -> dict:
    """Окружение вызова: прокси по `llm.proxy`, без меток чужого сеанса, наш
    конфиг вместо унаследованного OPENCODE_CONFIG_CONTENT. Конфиг и вход
    человека (OPENCODE_CONFIG, auth.json, ключи провайдеров) не трогаем."""
    env = netproxy.child_env(proxy)
    drop_session_markers(env)
    for name in [k for k in env if k.upper() in _DROP_ENV]:
        env.pop(name, None)
    env.update(_SET_ENV)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    return env


@dataclass
class Events:
    """Итог разбора вывода `opencode run --format json`."""

    text: str = ""
    session_id: str | None = None
    error: str | None = None
    auth: bool = False
    tools: int = 0


def _error_text(err) -> tuple[str, bool]:
    """Событие ошибки → (текст, похоже ли на ошибку входа)."""
    if not isinstance(err, dict):
        return str(err), False
    name = str(err.get("name") or "ошибка")
    data = err.get("data") if isinstance(err.get("data"), dict) else {}
    if name == "ProviderModelNotFoundError":
        model = f"{data.get('providerID', '?')}/{data.get('modelID', '?')}"
        return f"модель {model} не найдена в OpenCode (список — opencode models)", False
    message = str(data.get("message") or name)
    auth = (name == "ProviderAuthError" or data.get("statusCode") == 401
            or bool(_AUTH_MARKERS.search(message)))
    return message, auth


def parse_events(out: str) -> Events:
    """Ответ — текст последнего сообщения модели, в котором был текст
    (пояснения перед чтением файлов — в прежних сообщениях); ошибки — все
    события `error` по порядку."""
    found = Events()
    texts: dict[str, list[str]] = {}
    last_msg = None
    errors: list[str] = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        sid = event.get("sessionID")
        if found.session_id is None and isinstance(sid, str) and sid:
            found.session_id = sid
        kind = event.get("type")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        if kind == "text":
            value = part.get("text")
            if isinstance(value, str):
                msg = str(part.get("messageID") or last_msg or "")
                texts.setdefault(msg, []).append(value)
                last_msg = msg
        elif kind == "tool_use":
            found.tools += 1
        elif kind == "error":
            message, auth = _error_text(event.get("error"))
            errors.append(message)
            found.auth = found.auth or auth
    if last_msg is not None:
        found.text = "".join(texts[last_msg]).strip()
    if errors:
        body = "\n".join(errors)
        found.error = f"OpenCode: {AUTH_HINT}: {body}" if found.auth else f"OpenCode: {body}"
    return found


def _workdir() -> str:
    """Служебная пустая рабочая папка во временной папке системы."""
    path = tempdirs.system_temp() / WORKDIR
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _delete_session(exe: str, session_id: str, workdir: str, env: dict) -> None:
    """Удалить сеанс фонового вызова из истории OpenCode; не вышло — не беда."""
    try:
        subprocess.run([exe, "session", "delete", session_id], capture_output=True,
                       timeout=30, cwd=workdir, env=env, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


def _exec(exe: str, workdir: str, cmd: list[str], stdin_text: str, timeout_s: float,
          env: dict) -> AgentReply:
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=workdir, env=env, creationflags=_NO_WINDOW,
        )
    except OSError as e:
        return AgentReply(text="", error=f"не удалось запустить OpenCode: {e}")
    timed_out = False
    try:
        out, err = proc.communicate(input=stdin_text.encode("utf-8"), timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            out, err = b"", b""
    events = parse_events((out or b"").decode("utf-8", errors="replace"))
    if events.session_id:
        _delete_session(exe, events.session_id, workdir, env)
    if timed_out:
        return AgentReply(text="", error=TIMEOUT_ERROR)
    if events.error:
        return AgentReply(text="", error=events.error[-_ERR_LIMIT:])
    stderr = _ANSI.sub("", (err or b"").decode("utf-8", errors="replace")).strip()
    if proc.returncode != 0:
        return AgentReply(
            text="", error=stderr[-_ERR_LIMIT:] or f"OpenCode завершился с кодом {proc.returncode}")
    if not events.text:
        return AgentReply(text="", error=stderr[-_ERR_LIMIT:] or EMPTY_ERROR)
    return AgentReply(text=events.text)


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
) -> AgentReply:
    """Один вызов `opencode run`; ошибки — в AgentReply.error. `model` —
    `llm.opencode_model` («провайдер/модель»), `proxy` — `llm.proxy`. Папки
    `allowed_dirs` — только для чтения; `cwd` не нужен (служебная папка)."""
    exe = find_opencode()
    if exe is None:
        return AgentReply(text="", error=OPENCODE_NOT_FOUND)
    if len(system_prompt) <= PROMPT_ENV_LIMIT:
        agent_prompt, stdin_text = system_prompt, prompt
    else:
        agent_prompt, stdin_text = SHORT_SYSTEM, f"{system_prompt}\n\n{prompt}"
    env = child_env(proxy, config_content(agent_prompt, allowed_dirs, max_turns))
    workdir = _workdir()
    cmd = build_command(exe, workdir, model)
    reply = await asyncio.to_thread(_exec, exe, workdir, cmd, stdin_text, timeout_s, env)
    reply.error = netproxy.with_hint(reply.error)
    return reply
