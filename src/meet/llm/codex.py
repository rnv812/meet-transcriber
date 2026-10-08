"""Провайдер Codex CLI: `codex exec` в песочнице только-чтение.

По умолчанию сеанса нет (`--ephemeral`): `session_id` игнорируется, память
диалога вызывающий кладёт в prompt.
Рабочая папка — база знаний (первая существующая из `allowed_dirs[1:]`),
иначе `cwd`. Флаги сверены с `codex exec --help` (codex-cli 0.159.0; 0.4 —
0.160.0: `--approve-for-me`, `--add-dir`).
`model` и `max_turns` — понятия Claude; Codex берёт модель из своего конфига.
`effort` — усилие рассуждения на один вызов (`-c model_reasoning_effort=…`),
для «Быстрее» в живых подсказках; None — как в конфиге Codex.

V4 (0.3.6, v4-design §5.1, §12):

* `images` — по флагу `--image=<путь>` на файл. Именно `=`: у `-i <FILE>...`
  много значений, и позиционный `-` (prompt из stdin) съелся бы как ещё
  одно изображение (проверено на 0.159.0 без модели: `-i a.png - x` берёт
  `-` и `x` в изображения, `--image=a.png - x` — «unexpected argument 'x'»).
  Запятая в пути делит значение (`value_delimiter`) — такие файлы не шлём;
* `keep_session=True` — сеанс сохраняется (без `--ephemeral`), с `--json`:
  id — из события `thread.started` (stdout), запасной путь — шапка
  `session id: <uuid>` (stderr без `--json`); он в `AgentReply.session_id`.
  Рабочая папка (`-C`) сохраняемых сеансов — своя постоянная
  KEEP_WORKDIR, не папка встречи и не папка вызывающего: `codex resume
  --last` вкладки «Агент» отбирает сеансы по папке, а продолжение не
  зависит от того, что передал вызывающий. Читать песочница read-only
  может весь диск — пути папок агент берёт из промпта;
* `resume=<uuid>` — `codex exec <флаги exec> resume <флаги resume> <id> -`.
  `--sandbox` и `-C` подкоманда `exec resume` не принимает — они идут до
  неё (проверено разбором с `--help`). Только UUID: имя, которого нет,
  Codex молча начинает новым сеансом (проверено). Неизвестный UUID —
  «thread/resume failed: no rollout found for thread id …», код 1 →
  `resume_failed`. Продолжение проверяется ещё и по id в ответе: Codex
  сообщил другой сеанс — тоже `resume_failed`, ответ и id чужого сеанса
  не используются (в нём нет системного промпта);
* `deny_paths` (`kb_exclude`) у Codex — только правило в тексте каждого
  вызова: песочница read-only читает весь диск, запрета по путям в 0.159
  нет (`llm.deny_enforced("codex")` — False).

0.3.7 (A1, fix round 1) — свобода по согласию (`access`, у агента-участника).
Обратного вызова на каждый инструмент у `codex exec` нет — остановить вызов
до решения человека нельзя. Поэтому Codex при включённой свободе получает
**только чтение файлов** (честно и с отказом в закрытую сторону;
`access_args`, проверено разбором на 0.159.0 без модели):

* любой ход (`none` — без просьбы, `read` — по просьбе): `--sandbox
  read-only`; MCP-серверы пользователя выключены (`-c
  mcp_servers.<имя>.enabled=false` по списку `codex mcp list --json`);
  список не прочитался или есть имя, которое так не выключить, — весь
  `config.toml` пользователя не загружается (`--ignore-user-config`, вход
  остаётся); выключены приложения, браузер, управление компьютером (`--disable
  apps browser_use computer_use in_app_browser`) и веб-поиск (`-c
  web_search="disabled"` — [ключ не проверен]);
* `none` — рабочая папка (`-C`) — папка встречи; читать файлы вне неё
  песочнице не запретить — только инструкция; `read` — постоянная папка
  сеансов.

0.4 — ход по просьбе пользователя (`user`, спец. «ассистент как CLI» §4):
`--sandbox workspace-write` и `--approve-for-me` (запросы разрешения уходят
автопроверке Codex — его ближайший аналог автомода [не проверено с
моделью]), рабочие папки (`work_dirs`: запись, база знаний) — `--add-dir`,
веб-поиск не выключается. MCP пользователя по-прежнему выключены:
изменение через MCP до выполнения не остановить, а это категория «спросить
человека». Удаление и отправку наружу Codex остановить не даёт — запрет
только в промпте (`participant_prompts._FREEDOM_RUNNER`); сеть песочницы
workspace-write по умолчанию выключена.

Без свободы (`access=None`) — как в 0.3.6: песочница только-чтение, а
MCP-серверы из `config.toml` пользователя Codex загружает сам (так было и в
0.3.6).
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from meet import netproxy, tempdirs
from meet.llm.base import (
    CANCELLED_ERROR, EMPTY_ERROR, TIMEOUT_ERROR, AgentReply, deny_prompt, drop_session_markers, image_note,
    is_uuid, kill_tree, resume_failure, run_tree, split_images,
)
from meet.llm.detect import find_codex

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_ERR_LIMIT = 500
# Шапка `codex exec` (0.159.0): «session id: 01a112eb-…».
_SESSION_LINE = re.compile(r"^\s*session id:\s*([0-9a-fA-F-]{36})\s*$", re.MULTILINE)
# Сохранённого сеанса нет (0.159.0): «thread/resume failed: no rollout found for thread id …».
_RESUME_FAILED = re.compile(r"no rollout found|thread/resume", re.IGNORECASE)
# Постоянная рабочая папка сохраняемых сеансов (во временной папке системы).
KEEP_WORKDIR = "meet-codex-sessions"


def keep_dir() -> str:
    path = tempdirs.system_temp() / KEEP_WORKDIR
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _kill_tree(proc) -> None:
    """Codex запускает дочерние процессы — убиваем всё дерево (llm.base)."""
    kill_tree(proc)


# Имя MCP-сервера, которое можно выключить ключом `-c mcp_servers.<имя>.enabled=false`:
# точки и кавычки ключ разбирает по-своему (проверено: `"имя"` — новый сервер).
_MCP_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
# Что выключить в ходе без согласия (`codex features list`, 0.159.0: stable).
_NONE_FEATURES = ("apps", "browser_use", "computer_use", "in_app_browser")
_mcp_cache: dict[str, tuple[float, list[dict] | None]] = {}
_MCP_CACHE_S = 60.0     # список серверов — не дольше минуты (добавили сервер — увидим)


def mcp_servers(exe: str | None = None, env: dict | None = None, *, refresh: bool = False) -> list[dict] | None:
    """MCP-серверы из настроек Codex пользователя (`codex mcp list --json`,
    0.159.0: `name`, `enabled`) — `[{"name", "enabled"}]`; Codex не найден —
    пусто; список не прочитался — None (вызывающий отказывает в закрытую
    сторону). Кэш — _MCP_CACHE_S. Блокирующее."""
    import time

    exe = exe or find_codex()
    if not exe:
        return []
    cached = _mcp_cache.get(exe)
    if cached is not None and not refresh and time.monotonic() - cached[0] < _MCP_CACHE_S:
        return cached[1]
    found: list[dict] | None
    try:
        data = json.loads(_mcp_list_json(exe, env))
        if not isinstance(data, list):
            raise ValueError("не список")
        found = [{"name": str(x["name"]), "enabled": x.get("enabled") is not False}
                 for x in data if isinstance(x, dict) and isinstance(x.get("name"), str)]
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        found = None
    _mcp_cache[exe] = (time.monotonic(), found)
    return found


def _mcp_list_json(exe: str, env: dict | None) -> str:
    """Вывод `codex mcp list --json` (тесты подменяют: CLI не запускается)."""
    res = subprocess.run([exe, "mcp", "list", "--json"], capture_output=True, timeout=30,
                         creationflags=_NO_WINDOW, env=env)
    return (res.stdout or b"").decode("utf-8", errors="replace")


def access_args(access: str | None, servers=(), work_dirs=()) -> list[str]:
    """Флаги exec для свободы (до подкоманды `resume`). None — как в 0.3.6
    (песочница только-чтение). `none`, `read` — только чтение файлов:
    песочница только-чтение, веб-поиск выключен. `user` (0.4) — песочница
    workspace-write с автопроверкой (`--approve-for-me`), существующие
    `work_dirs` — `--add-dir`, веб-поиск — как в настройках Codex. На любом
    уровне MCP `servers` выключены (`servers` None — список не прочитался —
    или имя, которое ключом не выключить, → `--ignore-user-config`),
    приложения, браузер и управление компьютером — тоже."""
    if access is None:
        return ["--sandbox", "read-only"]
    if access == "user":
        out = ["--sandbox", "workspace-write", "--approve-for-me"]
        for d in work_dirs or ():
            if d and Path(d).is_dir():
                out += ["--add-dir", str(d)]
    else:
        out = ["--sandbox", "read-only"]
    enabled = [s for s in servers or () if not isinstance(s, dict) or s.get("enabled", True)] \
        if servers is not None else None
    names = [s.get("name") if isinstance(s, dict) else s for s in enabled or ()]
    if enabled is None or any(not (isinstance(n, str) and _MCP_KEY.match(n)) for n in names):
        out.append("--ignore-user-config")     # в закрытую сторону: без config.toml — без MCP
    else:
        for name in names:
            out += ["-c", f"mcp_servers.{name}.enabled=false"]
    for feature in _NONE_FEATURES:
        out += ["--disable", feature]
    if access != "user":
        out += ["-c", 'web_search="disabled"']
    return out


def _workdir(allowed_dirs, cwd) -> str:
    for d in tuple(allowed_dirs)[1:]:
        if d and Path(d).is_dir():
            return str(d)
    if cwd and Path(cwd).is_dir():
        return str(cwd)
    if allowed_dirs and allowed_dirs[0] and Path(allowed_dirs[0]).is_dir():
        return str(allowed_dirs[0])
    return str(tempdirs.system_temp())


def build_command(exe: str, workdir: str, out_file: str, *, effort: str | None = None,
                  images=(), keep_session: bool = False, resume: str | None = None,
                  access: str | None = None, servers=(), work_dirs=()) -> list[str]:
    """Командная строка `codex exec` (prompt — в stdin, `-` последним).
    Продолжение: песочница, папки и автопроверка — до `resume` (у подкоманды
    их нет), остальное — после неё, id сеанса — перед `-`. `access` — уровень
    хода (`access_args`; `servers` — MCP-серверы, что выключить; `work_dirs` —
    рабочие папки хода USER)."""
    head = [exe, "exec", *access_args(access, servers, work_dirs), "--skip-git-repo-check"]
    # Сохраняемый сеанс — события JSON (id в thread.started; проверено на 0.159.0).
    events = ["--json"] if keep_session or resume else []
    rest = [*events, "--output-last-message", out_file,
            *(["-c", f'model_reasoning_effort="{effort}"'] if effort else []),
            *[f"--image={i}" for i in images or ()]]
    if resume:
        return [*head, "-C", workdir, "resume", *rest, resume, "-"]
    # Транскрипты встреч не оседают в ~/.codex/sessions, если сеанс не нужен.
    keep = [] if keep_session else ["--ephemeral"]
    return [*head, *keep, "-C", workdir, *rest, "-"]


def usable_images(images) -> tuple[list[str], list[tuple[str, str]]]:
    """(что отправить, [(путь, причина)] пропущенного): проверки
    `base.check_image` (тип по первым байтам, 5 МБ base64, 8 000 px, битый
    файл) и запятая в пути (Codex делит значение `--image` по запятым)."""
    good, dropped = split_images(images)
    sent = []
    for path, _media, _data in good:
        if "," in path:
            dropped.append((path, "в пути есть запятая — Codex её не понимает"))
        else:
            sent.append(path)
    return sent, dropped


def parse_events(stdout: str) -> tuple[str | None, list[str]]:
    """События `codex exec --json`: (id сеанса из `thread.started`, тексты
    ошибок `error`/`turn.failed` по порядку)."""
    sid, errors = None, []
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
        if kind == "thread.started" and sid is None and isinstance(event.get("thread_id"), str):
            sid = event["thread_id"]
        elif kind == "error" and event.get("message"):
            errors.append(str(event["message"]))
        elif kind == "turn.failed":
            err = event.get("error") if isinstance(event.get("error"), dict) else {}
            errors.append(str(err.get("message") or "ход не удался"))
    return sid, errors


def tool_events(stdout: str) -> list[dict]:
    """Ход работы для чата (0.4, `assist.tool_rows`) из событий `codex exec
    --json`: законченные элементы (`item.completed`) — команды
    (`command_execution` → Bash), правки файлов (`file_change` →
    `apply_patch`), вызовы MCP (`mcp_tool_call` → `mcp__сервер__инструмент`)
    и веб-поиск (`web_search` → WebSearch) — парами событий `tool_use` и
    `tool_result`, как у `claude_stream`. Остановить вызов до выполнения Codex
    не даёт, поэтому строки приходят после вызова. Чужие строки и поля
    пропускаются."""
    out: list[dict] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        kind, iid = item.get("type"), str(item.get("id") or "")
        if not iid:
            continue
        error = item.get("error") if isinstance(item.get("error"), dict) else {}
        failed = item.get("status") == "failed"
        if kind == "command_execution":
            name, data = "Bash", {"command": str(item.get("command") or "")}
            output = str(item.get("aggregated_output") or "")
            ok = not failed and item.get("exit_code") in (0, None)
        elif kind == "file_change":
            changes = [{"path": str(c.get("path")), "kind": str(c.get("kind") or "")}
                       for c in item.get("changes") or () if isinstance(c, dict) and c.get("path")]
            name, data = "apply_patch", {"changes": changes}
            output = "\n".join(f"{c['kind']}: {c['path']}" for c in changes)
            ok = not failed
        elif kind == "mcp_tool_call":
            server, tool = str(item.get("server") or "?"), str(item.get("tool") or "?")
            name = f"mcp__{server}__{tool}"
            data = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            result = item.get("result")
            output = str(error.get("message") or "") if failed else (
                json.dumps(result, ensure_ascii=False) if result is not None else "")
            ok = not failed
        elif kind == "web_search":
            name, data = "WebSearch", {"query": str(item.get("query") or "")}
            output, ok = "", not failed
        else:
            continue
        server, tool = (name[5:].split("__", 1) if name.startswith("mcp__") else (None, name))
        out.append({"type": "tool_use", "id": iid, "name": name, "server": server, "tool": tool,
                    "input": data, "parent": None})
        out.append({"type": "tool_result", "id": iid, "ok": ok, "output": output, "truncated": False,
                    "duration_ms": None, "parent": None})
    return out


def session_from(*streams: str) -> str | None:
    """Id сеанса из шапки `codex exec` (`session id: <uuid>`)."""
    for text in streams:
        m = _SESSION_LINE.search(text or "")
        if m:
            return m.group(1)
    return None


class _Call:
    """Процесс вызова для остановки из цикла событий (поток to_thread не
    отменить): «Стоп» у Codex — убить дерево процессов (v4-design §3.4)."""

    def __init__(self) -> None:
        self.proc = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        if self.proc is not None:
            _kill_tree(self.proc)


def _exec(exe: str, workdir: str, stdin_text: str, timeout_s: float,
          env: dict | None = None, effort: str | None = None, images=(),
          keep_session: bool = False, resume: str | None = None,
          call: _Call | None = None, access: str | None = None, work_dirs=()) -> AgentReply:
    # ignore_cleanup_errors: убитый по таймауту Codex может ещё держать файл.
    # Папка с pid в имени (meet.tempdirs): процесс убили посреди ответа — файл
    # с ответом модели о встрече удалит резидент.
    with tempfile.TemporaryDirectory(prefix=tempdirs.prefix("codex-"),
                                     ignore_cleanup_errors=True) as tmp:
        out_file = Path(tmp) / "last-message.txt"
        servers = mcp_servers(exe, env) if access is not None else ()
        cmd = build_command(exe, workdir, str(out_file), effort=effort, images=images,
                            keep_session=keep_session, resume=resume, access=access,
                            servers=servers, work_dirs=work_dirs)
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=_NO_WINDOW, env=env,
            )
        except OSError as e:
            return AgentReply(text="", error=f"не удалось запустить Codex: {e}")
        if call is not None:
            call.proc = proc
            if call.cancelled:
                _kill_tree(proc)
        try:
            out, err = proc.communicate(input=stdin_text.encode("utf-8"), timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.communicate(timeout=10)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
            return AgentReply(text="", error=TIMEOUT_ERROR)
        stderr = (err or b"").decode("utf-8", errors="replace").strip()
        stdout = (out or b"").decode("utf-8", errors="replace")
        try:
            text = out_file.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
    event_sid, event_errors = parse_events(stdout)
    tools = tool_events(stdout)
    reply = _reply(proc, stdout, stderr, text, event_sid, event_errors, keep_session, resume, call)
    reply.tools = tools
    return reply


def _reply(proc, stdout: str, stderr: str, text: str, event_sid, event_errors, keep_session: bool,
           resume: str | None, call) -> AgentReply:
    """Ответ вызова по его выводу (без событий хода работы)."""
    reported = event_sid or session_from(stderr, stdout)
    if call is not None and call.cancelled:
        return AgentReply(text="", error=CANCELLED_ERROR, cancelled=True,
                          session_id=(reported or resume) if (keep_session or resume) else None)
    if resume and proc.returncode != 0 and _RESUME_FAILED.search(stderr):
        return resume_failure(stderr[-_ERR_LIMIT:])
    if resume and reported and reported.lower() != resume.lower():
        # Codex продолжил не тот сеанс (начал новый): без системного промпта и
        # без прошлого разговора — ответ и id не используем.
        return resume_failure(f"Codex начал другой сеанс ({reported}) вместо {resume}")
    sid = (reported or resume) if (keep_session or resume) else None
    errors = stderr or "\n".join(event_errors[-3:])
    if proc.returncode != 0:
        return AgentReply(
            text="",
            error=errors[-_ERR_LIMIT:] or f"Codex завершился с кодом {proc.returncode}",
            session_id=sid,
        )
    if not text:
        return AgentReply(text="", error=errors[-_ERR_LIMIT:] or EMPTY_ERROR, session_id=sid)
    return AgentReply(text=text, session_id=sid)


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
) -> AgentReply:
    """Один вызов `codex exec`; ошибки — в AgentReply.error. `proxy` —
    `llm.proxy`: Codex системный прокси Windows сам не видит.

    `images` — пути к изображениям (`--image=`); негодные не уходят —
    `dropped_images`, `notes` и пометка модели в тексте. `keep_session` —
    сохранить сеанс и вернуть его id; `resume` — продолжить сохранённый
    (UUID). Оба — в постоянной папке KEEP_WORKDIR. Системный промпт
    продолжению не повторяется: он уже в сеансе. `deny_paths` — правило в
    тексте (не запрет песочницы). Отмена задачи (CancelledError, «Стоп»)
    убивает дерево процессов Codex.

    `access` (0.3.7, агент-участник со свободой) — уровень хода (`none` /
    `read` — только чтение файлов, без MCP и веба; `user` (0.4) — правка
    `work_dirs` и команды с автопроверкой Codex, см. модуль); у `none`
    рабочая папка — первая из `allowed_dirs` (папка встречи). None — как в
    0.3.6."""
    if resume and not is_uuid(resume):
        return resume_failure(f"неверный id сеанса Codex: {resume!r}")
    exe = find_codex()
    if exe is None:
        return AgentReply(text="", error="не найден Codex CLI (codex)")
    sent, dropped = usable_images(images)
    notes = [image_note(path, why) for path, why in dropped]
    body = "\n".join([prompt, *[f"({n})" for n in notes]]) if notes else prompt
    rule = deny_prompt(deny_paths)
    if rule:
        body = f"{body}\n\n{rule}"
    stdin_text = body if resume else f"{system_prompt}\n\n{body}"
    workdir = keep_dir() if (keep_session or resume) else _workdir(allowed_dirs, cwd)
    if access == "none" and allowed_dirs and allowed_dirs[0] and Path(allowed_dirs[0]).is_dir():
        workdir = str(allowed_dirs[0])   # ход без согласия: рабочая папка — встреча
    env = netproxy.child_env(proxy)
    drop_session_markers(env)  # сеанс сам по себе, не «вложенный» (llm.base)
    call = _Call()
    try:
        reply = await asyncio.to_thread(
            _exec, exe, workdir, stdin_text, timeout_s, env, effort,
            sent, keep_session, resume, call, access, tuple(work_dirs or ()),
        )
    except asyncio.CancelledError:
        call.cancel()  # «Стоп»: процесс Codex убит, поток дочитает и выйдет
        raise
    reply.error = netproxy.with_hint(reply.error)
    reply.dropped_images = [path for path, _ in dropped]
    reply.notes = notes
    return reply


def _home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


# `codex delete --force <uuid>` — локальная команда, сеть не нужна (проверено
# на 0.159.0 с мёртвым прокси): секунды.
DELETE_TIMEOUT_S = 30.0


def _cli_delete(session_id: str, run=None) -> bool:
    """Официальное удаление сеанса: `codex delete --force <uuid>` — убирает
    поток и из sqlite-хранилищ Codex (`state_5`, `thread_history_1`), которые
    сами мы не трогаем (их схема — внутреннее дело Codex). → удалось ли."""
    exe = find_codex()
    if exe is None:
        return False
    run = run or run_tree
    env = netproxy.child_env(None)
    drop_session_markers(env)
    try:
        code, _out, _err = run([exe, "delete", "--force", session_id],
                               timeout=DELETE_TIMEOUT_S, env=env)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return code == 0


def forget_session(session_id: str, run=None) -> int:
    """Удалить сохранённый сеанс Codex: официальной командой `codex delete
    --force` (поток в sqlite Codex и его rollout), затем — на случай старого
    CLI без неё — файлы `rollout-*-<id>.jsonl` в `sessions/` и
    `archived_sessions/` CODEX_HOME. Только UUID; → сколько удалено (удача
    команды считается за один)."""
    if not is_uuid(session_id):
        return 0
    removed = 1 if _cli_delete(session_id, run) else 0
    for sub_dir in ("sessions", "archived_sessions"):
        root = _home() / sub_dir
        if not root.is_dir():
            continue
        for f in root.rglob(f"rollout-*-{session_id}.jsonl"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed
