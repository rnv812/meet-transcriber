"""Провайдер OpenCode: `opencode run --format json` с агентом только для чтения.

Один вызов — один процесс `opencode run`; память диалога вызывающий кладёт в
prompt (`resume`/`session_id` не используются, как у Codex). Флаги и формат
сверены с документацией (opencode.ai/docs/cli, /permissions, /agents,
/config) и исходниками opencode (packages/opencode/src/cli/cmd/run.ts,
session.ts, agent/agent.ts, permission/index.ts, 2026-10):

* `--format json` — по строке JSON на событие `{type, timestamp, sessionID,
  …}`: `step_start`, `text` (part.text — готовый кусок текста), `tool_use`,
  `step_finish` (part.reason: `stop` или `tool-calls`), `reasoning`, `error`
  (error.name, error.data.message). В документации формат не описан —
  разбираем осторожно: чужие строки и поля пропускаем;
* права — свой агент `meet-readonly` в `OPENCODE_CONFIG_CONTENT`: сначала
  `"*": "deny"`, потом разрешённое чтение (read/grep/glob/list) и доступ к
  разрешённым папкам (`external_directory`). Правила агента идут после
  правил пользователя и побеждают (последнее подходящее). Запрос «ask» в
  `opencode run` без `--auto` отклоняется сам. Тот же набор — и общими
  правами (`OPENCODE_PERMISSION`): не найдёт OpenCode нашего агента и
  возьмёт свой build — тот тоже только читает;
* рабочая папка — своя пустая на каждый вызов внутри служебной
  (`%TEMP%/meet-opencode/<pid>-<id>`), не папка встречи: сеансы фоновых
  вызовов не попадают в «Продолжить» вкладки «Агент», а настройки проекта
  (opencode.json, AGENTS.md) не подхватываются;
* у `run` нет режима «не сохранять», а текст встречи не должен оседать в
  истории: OpenCode записывает сеанс с сообщением ещё до первого события.
  Поэтому после вызова (и при таймауте, и при отмене) сеансы его папки
  удаляются по списку `opencode session list --format json` (поле
  `directory`), а перед первым вызовом в процессе — сеансы служебных папок,
  чей процесс умер или чья папка уже удалена. Чужие сеансы (другие папки)
  не трогаются никогда.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from meet import netproxy, tempdirs
from meet.llm.base import (
    EMPTY_ERROR, TIMEOUT_ERROR, AgentReply, drop_session_markers, kill_tree, run_tree,
)
from meet.llm.detect import OPENCODE_NOT_FOUND, find_opencode

_ERR_LIMIT = 500
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_kill_tree = kill_tree  # тесты подменяют

# Свой агент фоновых вызовов (в конфиге из переменной среды — у человека его нет).
AGENT = "meet-readonly"
# Заголовок сеанса: свой, чтобы OpenCode не звал модель придумывать название
# (session/prompt.ts: название генерируется, только пока оно по умолчанию).
TITLE = "meet"
WORKDIR = "meet-opencode"
# Конфиг с системным промптом — в переменной среды; длиннее — промпт в stdin,
# как у Codex: одна переменная среды Windows — до 32 767 символов.
ENV_LIMIT = 30_000
SHORT_SYSTEM = ("Ты помогаешь приложению meet с записью встречи. Следуй инструкции в "
                "начале сообщения. Файлы не изменяй, команды не выполняй.")
# Переменные, которые меняют поведение вызова в обход нашего конфига.
_DROP_ENV = ("OPENCODE_CONFIG_CONTENT", "OPENCODE_PERMISSION", "OPENCODE_AUTO_SHARE")
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
# run.ts: агента не нашлось (или он подагент) — OpenCode берёт агента по умолчанию.
_FALLBACK = "falling back to default agent"
AGENT_LOST = ("OpenCode не нашёл агента meet-readonly и взял свой по умолчанию — "
              "ответ не используется")
# Сценарий npm (.cmd) запускается через cmd.exe: эти символы в пути он разберёт по-своему.
_CMD_META = set('&%^|<>"!')
SHIM_UNSAFE = ("OpenCode найден только как сценарий npm (opencode.cmd), а в пути к нему или к "
               "временной папке есть символы, которые cmd.exe понимает как команды: "
               "установите программу opencode.exe (scoop, choco или npm i -g opencode-ai)")
# Сколько ждать служебных команд (список и удаление сеансов).
_HELPER_TIMEOUT_S = 30


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


def _dumps(value) -> str:
    # Кириллица — как есть: с ensure_ascii каждый символ стал бы \\uXXXX (6 знаков).
    return json.dumps(value, ensure_ascii=False)


def prompt_placement(system_prompt: str, prompt: str, dirs, max_turns: int) -> tuple[dict, str]:
    """(конфиг, stdin): системный промпт — в конфиг агента, если переменная
    среды с ним укладывается в ENV_LIMIT; иначе — в начало stdin."""
    config = config_content(system_prompt, dirs, max_turns)
    if len(_dumps(config)) <= ENV_LIMIT:
        return config, prompt
    return config_content(SHORT_SYSTEM, dirs, max_turns), f"{system_prompt}\n\n{prompt}"


def build_command(exe: str, workdir: str, model: str | None) -> list[str]:
    """`opencode run`. Модель — только «провайдер/модель» (`llm.opencode_model`);
    иначе — модель из конфига OpenCode. Сообщение — в stdin."""
    cmd = [exe, "run", "--format", "json", "--agent", AGENT, "--title", TITLE, "--dir", workdir]
    if model and "/" in model:
        cmd += ["-m", model]
    return cmd


def child_env(proxy: str | None, config: dict) -> dict:
    """Окружение вызова: прокси по `llm.proxy`, без меток чужого сеанса, наш
    конфиг вместо унаследованного OPENCODE_CONFIG_CONTENT и права агента ещё
    и общими (`OPENCODE_PERMISSION`). Конфиг и вход человека (OPENCODE_CONFIG,
    auth.json, ключи провайдеров) не трогаем."""
    env = netproxy.child_env(proxy)
    drop_session_markers(env)
    for name in [k for k in env if k.upper() in _DROP_ENV]:
        env.pop(name, None)
    env.update(_SET_ENV)
    env["OPENCODE_CONFIG_CONTENT"] = _dumps(config)
    env["OPENCODE_PERMISSION"] = _dumps(config["agent"][AGENT]["permission"])
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
    (пояснения перед чтением файлов — в прежних сообщениях); куски текста
    одного сообщения — отдельными абзацами. Ошибки — все события `error` по
    порядку."""
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
        found.text = "\n\n".join(p.strip() for p in texts[last_msg] if p.strip())
    if errors:
        body = "\n".join(errors)
        found.error = f"OpenCode: {AUTH_HINT}: {body}" if found.auth else f"OpenCode: {body}"
    return found


# --- служебные папки и уборка сеансов ------------------------------------------------


def _root() -> Path:
    """Служебная папка вызовов во временной папке системы (общей для процессов meet)."""
    path = tempdirs.system_temp() / WORKDIR
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _call_dir(root: Path) -> Path:
    """Своя пустая папка вызова: `<pid>-<id>` — по pid её сеансы узнаёт уборка."""
    path = root / f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(path) -> str:
    """Путь для строгого сравнения: полный (без 8.3 и `..`), регистр по ОС."""
    try:
        text = os.path.realpath(str(path))
    except (OSError, ValueError):
        text = os.path.abspath(str(path))
    return os.path.normcase(os.path.normpath(text))


def _pid_alive(pid: int) -> bool:
    from meet.gpu_lock import _pid_alive as alive

    try:
        return alive(pid)
    except Exception:
        return True  # проверить нечем — не трогаем


def list_sessions(exe: str, cwd: str, env: dict) -> list[dict]:
    """Сеансы OpenCode (`opencode session list --format json`: id, directory…);
    не вышло — пусто."""
    try:
        code, out, _ = run_tree([exe, "session", "list", "--format", "json"],
                                timeout=_HELPER_TIMEOUT_S, cwd=cwd, env=env)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if code != 0:
        return []
    try:
        data = json.loads(out.decode("utf-8", errors="replace") or "[]")
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [s for s in data if isinstance(s, dict) and isinstance(s.get("id"), str)
            and isinstance(s.get("directory"), str)]


def delete_session(exe: str, session_id: str, cwd: str, env: dict) -> None:
    """Удалить сеанс из истории OpenCode; не вышло — не беда."""
    try:
        run_tree([exe, "session", "delete", session_id], timeout=_HELPER_TIMEOUT_S,
                 cwd=cwd, env=env)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass


def ours_in(sessions: list[dict], call_dir) -> list[str]:
    """Сеансы ровно этой папки вызова."""
    key = _key(call_dir)
    return [s["id"] for s in sessions if _key(s["directory"]) == key]


def stale(sessions: list[dict], root, alive=_pid_alive) -> list[str]:
    """Оставленные сеансы служебных папок: сама служебная папка, папка
    вызова умершего процесса или уже удалённая папка. Сеансы живых вызовов
    (папка есть, процесс жив) и любых других папок не трогаются."""
    root_key = _key(root)
    found = []
    for s in sessions:
        directory = Path(s["directory"])
        if _key(directory) == root_key:
            found.append(s["id"])
            continue
        if _key(directory.parent) != root_key:
            continue  # чужая папка — сеанс человека
        pid = directory.name.split("-", 1)[0]
        dead = not pid.isdigit() or (int(pid) != os.getpid() and not alive(int(pid)))
        if dead or not directory.is_dir():
            found.append(s["id"])
    return found


_swept = False


def _sweep_stale(exe: str, root: Path, env: dict) -> None:
    """Перед первым вызовом в процессе: сеансы, оставленные убитыми вызовами."""
    global _swept
    if _swept:
        return
    _swept = True
    for sid in stale(list_sessions(exe, str(root), env), root):
        delete_session(exe, sid, str(root), env)


def _cleanup(exe: str, root: Path, call_dir: Path, env: dict, known: str | None) -> None:
    """После вызова (любого исхода): известный сеанс — удалить; неизвестен
    (таймаут, отмена, OpenCode убит до первого события) — найти сеансы папки
    вызова по списку. Затем — саму папку."""
    ids = [known] if known else ours_in(list_sessions(exe, str(root), env), call_dir)
    for sid in ids:
        delete_session(exe, sid, str(root), env)
    shutil.rmtree(call_dir, ignore_errors=True)


class _Call:
    """Процесс вызова для отмены из цикла событий (поток to_thread не отменить)."""

    def __init__(self) -> None:
        self.proc = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        if self.proc is not None:
            _kill_tree(self.proc)


def _exec(exe: str, root: Path, model: str | None, stdin_text: str, timeout_s: float,
          env: dict, call: _Call) -> AgentReply:
    _sweep_stale(exe, root, env)
    call_dir = _call_dir(root)
    events = Events()
    try:
        if exe.lower().endswith((".cmd", ".bat")) and _CMD_META & set(exe + str(call_dir)):
            return AgentReply(text="", error=SHIM_UNSAFE)
        if call.cancelled:
            return AgentReply(text="", error="вызов отменён")
        try:
            proc = subprocess.Popen(
                build_command(exe, str(call_dir), model), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(call_dir), env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            return AgentReply(text="", error=f"не удалось запустить OpenCode: {e}")
        call.proc = proc
        if call.cancelled:
            _kill_tree(proc)
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
        if timed_out:
            return AgentReply(text="", error=TIMEOUT_ERROR)
        if call.cancelled:
            return AgentReply(text="", error="вызов отменён")
        stderr = _ANSI.sub("", (err or b"").decode("utf-8", errors="replace")).strip()
        if _FALLBACK in stderr.lower() or _FALLBACK in (out or b"").decode("utf-8", "replace").lower():
            return AgentReply(text="", error=AGENT_LOST)
        if events.error:
            return AgentReply(text="", error=events.error[-_ERR_LIMIT:])
        if proc.returncode != 0:
            return AgentReply(
                text="", error=stderr[-_ERR_LIMIT:] or f"OpenCode завершился с кодом {proc.returncode}")
        if not events.text:
            return AgentReply(text="", error=stderr[-_ERR_LIMIT:] or EMPTY_ERROR)
        return AgentReply(text=events.text)
    finally:
        _cleanup(exe, root, call_dir, env, events.session_id)


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
    `allowed_dirs` — только для чтения; `cwd` не нужен (своя папка вызова).
    Отмена (CancelledError) убивает процесс; сеанс убирает поток вызова."""
    exe = find_opencode()
    if exe is None:
        return AgentReply(text="", error=OPENCODE_NOT_FOUND)
    config, stdin_text = prompt_placement(system_prompt, prompt, allowed_dirs, max_turns)
    env = child_env(proxy, config)
    call = _Call()
    try:
        reply = await asyncio.to_thread(_exec, exe, _root(), model, stdin_text, timeout_s, env, call)
    except asyncio.CancelledError:
        call.cancel()
        raise
    reply.error = netproxy.with_hint(reply.error)
    return reply
