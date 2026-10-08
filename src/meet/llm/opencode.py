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

V4 (0.3.6, v4-design §12) — нативное продолжение [не проверено: OpenCode на
машине разработки не установлен; флаги — по документации opencode.ai/docs/cli
и вкладке «Агент» (`pty.rs`, OPENCODE_SESSION_FLAGS)]:

* `keep_session=True` — сеанс не удаляется; id — из событий (`sessionID`).
  Работает в своей постоянной папке KEEP_WORKDIR (не внутри служебной:
  уборка её не тронет; не папка встречи: `--continue` вкладки «Агент» его
  не подхватит);
* `resume=<id>` — `opencode run --session <id>` в той же папке, без
  `--title`. Ошибка «сеанс не найден» — `resume_failed`; главная защита —
  сверка id: события пришли от другого сеанса (OpenCode начал новый) —
  `resume_failed`, чужой сеанс удаляется, ответ не используется.
  Системный промпт продолжению не повторяется: в stdin — только сообщение,
  длинный промпт (он ушёл первым сообщением) в конфиге заменён коротким;
* `deny_paths` (`kb_exclude`) — запрет `read` и `external_directory` по
  путям после разрешений (последнее подходящее правило побеждает); `grep`
  при запретах выключен: его правило сопоставляется с шаблоном поиска, а
  не с путём, и он вернул бы строки из закрытых файлов;
* изображения OpenCode не отправляем (`llm.vision` — False): флаг вложения
  `--file` не проверен; в ответе — `dropped_images` и NO_VISION_NOTE.

0.3.7 (A1, fix round 1) — свобода по согласию (`access`, агент-участник)
[не проверено: OpenCode не установлен; ключи прав — по документации
opencode.ai/docs/permissions]. Остановить вызов до решения человека OpenCode
не даёт («ask» в `opencode run` отклоняется сам), поэтому при включённой
свободе он получает **только чтение файлов** (`permission_for`):

* `none` — как прежде: только чтение и только папка встречи;
* `read` — чтение где угодно, кроме `kb_exclude`, чувствительных путей
  (`consent.sensitive_paths`) и `.env`; команды, правка, веб, MCP и
  подагенты — `deny` (`"*": "deny"`).

0.4 — ход по просьбе пользователя (`user`, спец. «ассистент как CLI» §4;
сверено с `opencode run --help` 1.18 и разбором его сборки: правка
спрашивает `edit` с путём относительно рабочей папки, а любой путь вне неё
— `external_directory` с шаблоном «папка/*»). `--auto` не используем (он
одобряет всё, что явно не запрещено). Права: `read` — как у `read`;
`edit` — `allow`, а запись вне рабочих папок закрывает `external_directory`
(`"*": "deny"`, рабочие папки — `allow`, закрытое — `deny` последним; читать
вне рабочих папок в этом ходе тоже нельзя — у OpenCode одна проверка на
оба действия); `bash` — `allow`, кроме удаления и отправки наружу (те же
списки, что у ворот Claude Code, `consent`) и оболочек с кодом в строке —
`deny` (спросить человека OpenCode не может); `webfetch`, `websearch` —
`allow`; MCP и подагенты — `deny`.
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
    EMPTY_ERROR, NO_VISION_NOTE, TIMEOUT_ERROR, AgentReply, drop_session_markers, kill_tree, path_variants,
    resume_failure, run_tree,
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
# Папка сохраняемых сеансов (рядом со служебной, не внутри: уборка не трогает).
KEEP_WORKDIR = "meet-opencode-sessions"
# id сеанса OpenCode (`ses_…`); другое в --session не передаём.
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
# Сеанса для --session нет [не проверено: формат ошибки OpenCode]. Только про
# сеанс: «model not found» (ProviderModelNotFoundError) — не повод забыть сеанс.
_RESUME_MISSING = re.compile(
    r"session\b[^\n]{0,60}\bnot ?found|\bnot ?found\b[^\n]{0,60}\bsession|no such session"
    r"|(?<![A-Za-z])NotFoundError", re.IGNORECASE)
# Конфиг с системным промптом — в переменной среды; длиннее — промпт в stdin,
# как у Codex: одна переменная среды Windows — до 32 767 символов.
ENV_LIMIT = 30_000
SHORT_SYSTEM = ("Ты помогаешь приложению meet с записью встречи. Следуй инструкции в "
                "начале сообщения. Файлы не изменяй, команды не выполняй.")
SHORT_SYSTEM_FREE = ("Ты помогаешь приложению meet с записью встречи. Следуй инструкции в "
                     "начале сообщения. Файлы читай только с согласия пользователя.")
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


def readonly_permission(dirs, exclude=()) -> dict:
    """Права агента фоновых вызовов. Без папок — никаких инструментов (как у
    Claude Code: анализ, названия, тики). С папками — только чтение в них:
    read (кроме .env), grep, glob, list и доступ к этим папкам вне рабочей;
    правка, команды, сеть, подагенты — запрещены (`"*": "deny"` первым).

    `exclude` — закрытые папки (`kb_exclude`): `deny` для read и
    external_directory — последними (побеждает последнее подходящее), с
    вариантами пути (настоящий регистр и нижний); grep при этом выключен.
    Шаблоны OpenCode (wildcard) особыми считают только `*` и `?`, способа
    экранировать их нет [по исходникам, не проверено]: в именах Windows их
    не бывает, на других системах такой знак в имени лишь расширяет запрет."""
    dirs = [Path(d) for d in dirs if d]
    if not dirs:
        return {"*": "deny"}
    external = {"*": "deny"}
    for d in dirs:
        external[str(d / "*")] = "allow"
    read = {"*": "allow", "*.env": "deny", "*.env.*": "deny", "*.env.example": "allow"}
    closed = [v for e in exclude or () if e for v in path_variants(e)]
    for v in closed:
        for pattern in (v, str(Path(v) / "*")):
            read[pattern] = "deny"
            external[pattern] = "deny"
    return {
        "*": "deny",
        "read": read,
        "grep": "deny" if closed else "allow",
        "glob": "allow",
        "list": "allow",
        "external_directory": external,
    }


def bash_denials() -> dict:
    """Шаблоны `bash` в ходе USER, которые OpenCode запрещает: удаление,
    отправка наружу, публикация, оболочки с кодом в строке (их содержимое
    шаблоны не видят). Шаблон OpenCode — `*` и `?` по всей строке команды;
    регистр PowerShell — оба варианта."""
    from meet.llm import consent

    rules: list[str] = []
    for prog in [*sorted(consent.DELETE_PROGRAMS | consent.SEND_PROGRAMS), "Remove-Item", "Send-MailMessage"]:
        rules += [prog, f"{prog} *"]
    rules += ["git clean*", "git rm *", "git reset --hard*", "git reset * --hard*", "git checkout -- *",
              "git checkout .*", "git restore *", "git stash drop*", "git stash clear*", "git push*",
              "git * push*", "git * clean*", "git send-email*", "find *-delete*", "find *-exec*",
              "find *-ok*", "xargs *", "rsync *:*"]
    for flag in ("-d", "--data", "-F", "--form", "-T", "--upload", "--json", "-X", "--request"):
        rules.append(f"curl *{flag}*")
    rules += ["wget *--post*", "wget *--method*", "wget *--body*"]
    for cmdlet in ("Invoke-WebRequest", "iwr", "Invoke-RestMethod", "irm"):
        rules += [f"{cmdlet} *{flag}*" for flag in ("-M", "-B", "-I", "-F")]
    for forge in ("gh", "glab"):
        rules += [f"{forge} * {verb}*" for verb in ("create", "comment", "note", "review", "merge", "close",
                                                  "reopen", "edit", "delete", "upload")]
        rules += [f"{forge} api *{flag}*" for flag in ("-f", "-F", "-X", "--field", "--raw-field", "--input",
                                                       "--method")]
    rules += [f"{prog} publish*" for prog in ("npm", "pnpm", "yarn", "cargo", "poetry", "uv")]
    rules += ["twine upload*", "docker push*", "podman push*", "gem push*", "dotnet nuget push*"]
    rules += [f"{shell} -c*" for shell in ("bash", "sh", "zsh", "dash")]
    rules += ["cmd *", "powershell *", "pwsh *", "eval *", "iex *", "Invoke-Expression *",
              "Invoke-Command *", "sudo *", "doas *", "runas *"]
    out: dict[str, str] = {}
    for rule in rules:
        for variant in dict.fromkeys((rule, rule.lower())):
            out[variant] = "deny"
    return out


def _user_permission(work_dirs, exclude=(), sensitive=()) -> dict:
    """Права хода USER (0.4): см. `permission_for`."""
    closed = [v for e in (*(exclude or ()), *(sensitive or ())) if e for v in path_variants(e)]
    read = {"*": "allow", "*.env": "deny", "*.env.*": "deny", "*.env.example": "allow"}
    external = {"*": "deny"}
    for d in work_dirs or ():
        if d:
            for v in path_variants(d):
                external[str(Path(v) / "*")] = "allow"
    for v in closed:
        for pattern in (v, str(Path(v) / "*")):
            read[pattern] = "deny"
            external[pattern] = "deny"
    return {"*": "deny", "read": read, "grep": "deny" if closed else "allow", "glob": "allow",
            "list": "allow", "edit": "allow", "external_directory": external,
            "bash": {"*": "allow", **bash_denials()}, "webfetch": "allow", "websearch": "allow",
            "todowrite": "allow"}


def permission_for(access: str | None, dirs, exclude=(), sensitive=(), work_dirs=()) -> dict:
    """Права агента для уровня хода (`meet.llm.consent`): None и `none` —
    `readonly_permission(dirs)` (у `none` `dirs` — только папка встречи);
    `read` — чтение файлов где угодно, кроме закрытых папок (`exclude`),
    чувствительных путей (`sensitive`) и `.env`; больше ничего; `user` (0.4)
    — правка и команды в рабочих папках (`work_dirs`), веб; удаление,
    отправка наружу и MCP — нет (см. модуль). Запреты — последними
    (побеждает последнее подходящее правило)."""
    if access == "user":
        return _user_permission(work_dirs, exclude, sensitive)
    if access != "read":
        return readonly_permission(dirs, exclude)
    closed = [v for e in (*(exclude or ()), *(sensitive or ())) if e for v in path_variants(e)]
    read = {"*": "allow", "*.env": "deny", "*.env.*": "deny", "*.env.example": "allow"}
    external = {"*": "allow"}
    for v in closed:
        for pattern in (v, str(Path(v) / "*")):
            read[pattern] = "deny"
            external[pattern] = "deny"
    return {"*": "deny", "read": read, "grep": "deny" if closed else "allow", "glob": "allow",
            "list": "allow", "external_directory": external}


def config_content(prompt: str, dirs, max_turns: int, exclude=(), access: str | None = None,
                   sensitive=(), work_dirs=()) -> dict:
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
                "permission": permission_for(access, dirs, exclude, sensitive, work_dirs),
            },
        },
    }


def _dumps(value) -> str:
    # Кириллица — как есть: с ensure_ascii каждый символ стал бы \\uXXXX (6 знаков).
    return json.dumps(value, ensure_ascii=False)


def prompt_placement(system_prompt: str, prompt: str, dirs, max_turns: int, exclude=(),
                     resume: bool = False, access: str | None = None, sensitive=(),
                     work_dirs=()) -> tuple[dict, str]:
    """(конфиг, stdin): системный промпт — в конфиг агента, если переменная
    среды с ним укладывается в ENV_LIMIT; иначе — в начало stdin. У
    продолжения сеанса длинный промпт в stdin не повторяется (он ушёл первым
    сообщением и уже в истории; иначе каждый ход добавлял бы его заново):
    конфиг — с коротким промптом, stdin — только сообщение."""
    config = config_content(system_prompt, dirs, max_turns, exclude, access, sensitive, work_dirs)
    if len(_dumps(config)) <= ENV_LIMIT:
        return config, prompt
    short = config_content(SHORT_SYSTEM if access is None else SHORT_SYSTEM_FREE, dirs, max_turns,
                           exclude, access, sensitive, work_dirs)
    if resume:
        return short, prompt
    return short, f"{system_prompt}\n\n{prompt}"


def build_command(exe: str, workdir: str, model: str | None, resume: str | None = None) -> list[str]:
    """`opencode run`. Модель — только «провайдер/модель» (`llm.opencode_model`);
    иначе — модель из конфига OpenCode. Сообщение — в stdin. `resume` —
    продолжить сеанс (`--session <id>`; заголовок у него уже есть)."""
    cmd = [exe, "run", "--format", "json", "--agent", AGENT]
    if resume:
        cmd += ["--session", resume]
    else:
        cmd += ["--title", TITLE]
    cmd += ["--dir", workdir]
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


# Инструменты OpenCode → имена Claude Code (вид строки в чате), ключи аргументов — тоже.
_TOOL_NAMES = {"bash": "Bash", "read": "Read", "edit": "Edit", "write": "Write", "grep": "Grep", "glob": "Glob",
               "list": "LS", "webfetch": "WebFetch", "websearch": "WebSearch", "patch": "apply_patch",
               "task": "Task", "todowrite": "TodoWrite", "todoread": "TodoRead"}
_ARG_NAMES = {"filePath": "file_path", "oldString": "old_string", "newString": "new_string"}


def tool_events(out: str) -> list[dict]:
    """Ход работы для чата (0.4, `assist.tool_rows`) из событий `opencode run
    --format json`: событие `tool_use` (part: `tool`, `callID`, `state` —
    `status`, `input`, `output`/`error`, `time.start/end` в мс) → пара
    `tool_use` / `tool_result`, как у `claude_stream`. Строки приходят после
    вызова. Чужие строки и поля пропускаются."""
    events: list[dict] = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "tool_use":
            continue
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        cid = str(part.get("callID") or part.get("id") or "")
        raw = str(part.get("tool") or "")
        if not cid or not raw:
            continue
        name = _TOOL_NAMES.get(raw, raw)
        data = state.get("input") if isinstance(state.get("input"), dict) else {}
        data = {_ARG_NAMES.get(k, k): v for k, v in data.items()}
        ok = state.get("status") != "error"
        output = str(state.get("output") or "") if ok else str(state.get("error") or "ошибка")
        times = state.get("time") if isinstance(state.get("time"), dict) else {}
        start, end = times.get("start"), times.get("end")
        duration = (int(end - start) if isinstance(start, (int, float)) and isinstance(end, (int, float))
                    else None)
        events.append({"type": "tool_use", "id": cid, "name": name, "server": None, "tool": name,
                       "input": data, "parent": None})
        events.append({"type": "tool_result", "id": cid, "ok": ok, "output": output, "truncated": False,
                       "duration_ms": duration, "parent": None})
    return events


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


def stale(sessions: list[dict], root, alive=None) -> list[str]:
    """Оставленные сеансы служебных папок: сама служебная папка, папка
    вызова умершего процесса или уже удалённая папка. Сеансы живых вызовов
    (папка есть, процесс жив) и любых других папок не трогаются.

    Сеансы самой служебной папки — от ранней сборки 0.3.3, где все вызовы
    шли в ней: они удаляются всегда, и если такая сборка (другая копия
    приложения) ещё работает, её идущий вызов потеряет свой сеанс."""
    alive = alive or _pid_alive
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


def dead_dirs(root, alive=None) -> list[Path]:
    """Папки вызовов `<pid>-<id>` умерших процессов (свои и живых — нет)."""
    alive = alive or _pid_alive
    found = []
    try:
        children = list(Path(root).iterdir())
    except OSError:
        return []
    for d in children:
        pid = d.name.split("-", 1)[0]
        if d.is_dir() and pid.isdigit() and int(pid) != os.getpid() and not alive(int(pid)):
            found.append(d)
    return found


def _sweep_stale(exe: str, root: Path, env: dict) -> None:
    """Перед первым вызовом в процессе: сеансы, оставленные убитыми вызовами,
    затем папки этих вызовов (их сеансы к этому времени уже удалены)."""
    global _swept
    if _swept:
        return
    _swept = True
    for sid in stale(list_sessions(exe, str(root), env), root):
        delete_session(exe, sid, str(root), env)
    for d in dead_dirs(root):
        shutil.rmtree(d, ignore_errors=True)


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
        self.tools: list[dict] = []     # ход работы вызова (`tool_events`)

    def cancel(self) -> None:
        self.cancelled = True
        if self.proc is not None:
            _kill_tree(self.proc)


def _keep_dir() -> Path:
    """Постоянная папка сохраняемых сеансов (во временной папке системы)."""
    path = tempdirs.system_temp() / KEEP_WORKDIR
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _exec(exe: str, root: Path, model: str | None, stdin_text: str, timeout_s: float,
          env: dict, call: _Call, keep: bool = False, resume: str | None = None) -> AgentReply:
    _sweep_stale(exe, root, env)
    call_dir = _keep_dir() if keep else _call_dir(root)
    events = Events()
    try:
        if exe.lower().endswith((".cmd", ".bat")) and _CMD_META & set(exe + str(call_dir)):
            return AgentReply(text="", error=SHIM_UNSAFE)
        if call.cancelled:
            return AgentReply(text="", error="вызов отменён")
        try:
            proc = subprocess.Popen(
                build_command(exe, str(call_dir), model, resume), stdin=subprocess.PIPE,
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
        decoded = (out or b"").decode("utf-8", errors="replace")
        events = parse_events(decoded)
        call.tools = tool_events(decoded)
        if timed_out:
            return AgentReply(text="", error=TIMEOUT_ERROR)
        if call.cancelled:
            return AgentReply(text="", error="вызов отменён")
        stderr = _ANSI.sub("", (err or b"").decode("utf-8", errors="replace")).strip()
        if _FALLBACK in stderr.lower() or _FALLBACK in (out or b"").decode("utf-8", "replace").lower():
            return AgentReply(text="", error=AGENT_LOST)
        if resume and events.session_id and events.session_id != resume:
            # OpenCode начал другой сеанс: без прошлого разговора — не используем, удаляем.
            delete_session(exe, events.session_id, str(call_dir), env)
            return resume_failure(f"OpenCode начал другой сеанс ({events.session_id}) вместо {resume}")
        failed = events.error or (stderr if proc.returncode != 0 else "")
        if resume and failed and not events.text and _RESUME_MISSING.search(failed):
            return resume_failure(failed[-_ERR_LIMIT:])
        sid = (events.session_id or resume) if keep else None
        if events.error:
            return AgentReply(text="", error=events.error[-_ERR_LIMIT:], session_id=sid)
        if proc.returncode != 0:
            return AgentReply(
                text="", error=stderr[-_ERR_LIMIT:] or f"OpenCode завершился с кодом {proc.returncode}",
                session_id=sid)
        if not events.text:
            return AgentReply(text="", error=stderr[-_ERR_LIMIT:] or EMPTY_ERROR, session_id=sid)
        return AgentReply(text=events.text, session_id=sid)
    finally:
        if not keep:
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
    images=(),
    keep_session: bool = False,
    deny_paths=(),
    access: str | None = None,
    work_dirs=(),
) -> AgentReply:
    """Один вызов `opencode run`; ошибки — в AgentReply.error. `model` —
    `llm.opencode_model` («провайдер/модель»), `proxy` — `llm.proxy`. Папки
    `allowed_dirs` — только для чтения; `cwd` не нужен (своя папка вызова).
    Отмена (CancelledError) убивает процесс; сеанс убирает поток вызова.

    `keep_session` — сеанс сохраняется, id — в ответе; `resume` —
    продолжить его. `images` не отправляются (модель их не видит) —
    `dropped_images` и NO_VISION_NOTE. `deny_paths` — закрытые папки
    (запрет в правах агента). `access` (0.3.7) — уровень согласия хода
    (`permission_for`); у `none` читать можно только первую из `allowed_dirs`
    (папку встречи); у `user` (0.4) правка и команды — в `work_dirs`."""
    if resume and not _SESSION_ID.fullmatch(resume):
        return resume_failure(f"неверный id сеанса OpenCode: {resume!r}")
    exe = find_opencode()
    if exe is None:
        return AgentReply(text="", error=OPENCODE_NOT_FOUND)
    dirs = tuple(allowed_dirs)[:1] if access == "none" else allowed_dirs
    sensitive = ()
    if access in ("read", "user"):
        from meet.llm.consent import sensitive_paths

        sensitive = [p for p in sensitive_paths() if p.exists()]
    config, stdin_text = prompt_placement(system_prompt, prompt, dirs, max_turns,
                                          deny_paths, resume=bool(resume), access=access,
                                          sensitive=sensitive, work_dirs=tuple(work_dirs or ()))
    env = child_env(proxy, config)
    call = _Call()
    keep = bool(keep_session or resume)
    try:
        reply = await asyncio.to_thread(_exec, exe, _root(), model, stdin_text, timeout_s, env, call,
                                        keep, resume)
    except asyncio.CancelledError:
        call.cancel()
        raise
    reply.error = netproxy.with_hint(reply.error)
    reply.tools = list(call.tools)
    sent = [str(i) for i in images or () if i]
    if sent:
        reply.dropped_images, reply.notes = sent, [NO_VISION_NOTE]
    return reply


def forget_session(session_id: str) -> int:
    """Удалить сохранённый сеанс (удалили чат встречи; смоук с `--cleanup`):
    `opencode session delete <id>`. → 1, если команда запускалась, иначе 0."""
    exe = find_opencode()
    if exe is None or not session_id or not _SESSION_ID.fullmatch(session_id):
        return 0
    env = netproxy.child_env(None)
    drop_session_markers(env)
    delete_session(exe, session_id, str(_keep_dir()), env)
    return 1
