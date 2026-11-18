"""Провайдер Codex CLI: `codex exec` в песочнице только-чтение.

По умолчанию сеанса нет (`--ephemeral`): `session_id` игнорируется, память
диалога вызывающий кладёт в prompt.
Рабочая папка — база знаний (первая существующая из `allowed_dirs[1:]`),
иначе `cwd`. Флаги сверены с `codex exec --help` (codex-cli 0.159.0; 0.4 —
0.160.0: `--approve-for-me`, `--add-dir`).
`model` — модель хода (`-m`, и после `resume`): выбирает её `/model`
ассистента; None — модель из конфига Codex. Имя проверяется (`model_error`):
Codex из npm — `codex.cmd`, его аргументы разбирает cmd.exe, поэтому
`& % ^ | < > "` и пробел не проходят. `max_turns` — понятие Claude.
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
  mcp_servers.<имя>={enabled=false,command="__meet_disabled_mcp__"}` для stdio,
  `{enabled=false,url="http://127.0.0.1:9/__meet_disabled_mcp__"}` для streamable_http —
  по списку `codex mcp list --json`, вид транспорта — оттуда же);
  список не прочитался, есть имя, которое так не выключить, или неизвестный вид транспорта — весь
  `config.toml` пользователя не загружается (`--ignore-user-config`, вход
  остаётся); выключены приложения, браузер, управление компьютером (`--disable
  apps browser_use computer_use in_app_browser`) и веб-поиск (`-c
  tools.web_search=false` — ключ `[tools] web_search` из config.toml Codex,
  сверено с полями config разобранного `codex.exe` 0.160.0; флаг `--search`,
  который его включает, у `codex exec` отсутствует, так что по умолчанию он и
  так выключен — ключ держит его выключенным и при своём config пользователя);
* `none` — рабочая папка (`-C`) — папка встречи; читать файлы вне неё
  песочнице не запретить — только инструкция; `read` — постоянная папка
  сеансов.

0.4 — ход по просьбе пользователя (`user`, спец. «ассистент как CLI» §4):
`--approve-for-me` (запросы разрешения уходят
автопроверке Codex — его ближайший аналог автомода [не проверено с
моделью]), рабочие папки (`work_dirs`: запись, база знаний) — `--add-dir`,
веб-поиск не выключается. MCP пользователя по-прежнему выключены:
изменение через MCP до выполнения не остановить, а это категория «спросить
человека». Удаление и отправку наружу Codex остановить не даёт — запрет
только в промпте (`participant_prompts._FREEDOM_RUNNER`); сеть песочницы
workspace-write по умолчанию выключена. Песочницу задаёт сам
`--approve-for-me` (workspace-write, перекрывает `sandbox_mode` пользователя);
`--sandbox` рядом с ним Codex отвергает при разборе — его здесь нет.
Ловушка Windows: без `[windows] sandbox` в `$CODEX_HOME/config.toml` ход
фактически только читает. Meet читает этот единственный ключ
(`windows_sandbox`, tomllib), сам его не ставит, а ассистент один раз за
встречу пишет об этом строку (`sandbox_missing`). В запасном пути
`--ignore-user-config` значение пользователя передаётся явно (`-c
windows.sandbox=…`): без него запись на Windows пропала бы и у настроенного.

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
    is_uuid, kill_tree, popen_kwargs, resume_failure, run_tree, split_images,
)
from meet.llm.detect import find_codex

_ERR_LIMIT = 500
# Хвост stderr при чтении по ходу вызова: ошибка Codex — в конце.
_STDERR_TAIL = 64 * 1024
# Шапка `codex exec` (0.159.0): «session id: 01a112eb-…».
_SESSION_LINE = re.compile(r"^\s*session id:\s*([0-9a-fA-F-]{36})\s*$", re.MULTILINE)
# Сохранённого сеанса нет (0.159.0): «thread/resume failed: no rollout found for thread id …».
_RESUME_FAILED = re.compile(r"no rollout found|thread/resume", re.IGNORECASE)
# Постоянная рабочая папка сохраняемых сеансов (во временной папке системы).
KEEP_WORKDIR = "meet-codex-sessions"
_ON_WINDOWS = sys.platform == "win32"
# Имя модели для `-m`: как у OpenCode (`settings.opencode_model_error`) — без
# знаков cmd.exe (`codex.cmd` из npm), не с «-» (иначе это флаг), до 128 знаков.
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,127}")
MODEL_ERROR = ("В имени модели Codex недопустимые символы: только латиница, цифры и . _ - : @ + /, "
               "в начале — буква или цифра, например gpt-5.5")
# Значение `[windows] sandbox` (elevated, unelevated), которое можно передать `-c`.
_SANDBOX_VALUE = re.compile(r"[A-Za-z0-9_-]{1,32}")


def model_error(value) -> str | None:
    """Негодное имя модели Codex — текст для человека; пусто — годится
    (модель из конфига Codex)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or not _MODEL.fullmatch(value.strip()):
        return MODEL_ERROR
    return None


def windows_sandbox() -> str | None:
    """`[windows] sandbox` из `$CODEX_HOME/config.toml` (по умолчанию
    `~/.codex`) — единственный ключ, который Meet читает из настроек Codex.
    Нет файла, ключа, не строка, файл не разобрался — None. Блокирующее."""
    import tomllib

    try:
        with open(_home() / "config.toml", "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError):
        return None
    section = data.get("windows") if isinstance(data, dict) else None
    value = section.get("sandbox") if isinstance(section, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def sandbox_missing() -> bool:
    """Codex на Windows без `[windows] sandbox`: ход USER фактически только
    читает. На других системах ключ не нужен — False. Блокирующее."""
    return _ON_WINDOWS and windows_sandbox() is None


def keep_dir() -> str:
    path = tempdirs.system_temp() / KEEP_WORKDIR
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _kill_tree(proc) -> None:
    """Codex запускает дочерние процессы — убиваем всё дерево (llm.base)."""
    kill_tree(proc)


# Имя MCP-сервера, которое можно выключить ключом `-c mcp_servers.<имя>={enabled=false,command="__meet_disabled_mcp__"}`:
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
        found = [{"name": str(x["name"]), "enabled": x.get("enabled") is not False,
                  "transport": _transport_type(x)}
                 for x in data if isinstance(x, dict) and isinstance(x.get("name"), str)]
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        found = None
    _mcp_cache[exe] = (time.monotonic(), found)
    return found


def _transport_type(item: dict) -> str | None:
    """Вид транспорта сервера из `codex mcp list --json` (`transport.type`:
    `stdio`, `streamable_http`); нет — None. Серверы плагинов в config.toml не
    записаны, поэтому берём не оттуда."""
    transport = item.get("transport")
    kind = transport.get("type") if isinstance(transport, dict) else None
    return kind if isinstance(kind, str) else None


def _mcp_list_json(exe: str, env: dict | None) -> str:
    """Вывод `codex mcp list --json` (тесты подменяют: CLI не запускается)."""
    _, out, _ = run_tree([exe, "mcp", "list", "--json"], timeout=30, env=env)
    return out.decode("utf-8", errors="replace")


def access_args(access: str | None, servers=(), work_dirs=(), *,
                windows_sandbox: str | None = None) -> list[str]:
    """Флаги exec для свободы (до подкоманды `resume`). None — как в 0.3.6
    (песочница только-чтение). `none`, `read` — только чтение файлов:
    песочница только-чтение, веб-поиск выключен. `user` (0.4) — песочница
    workspace-write с автопроверкой (`--approve-for-me`; `--sandbox` рядом с
    ним Codex отвергает — его нет), существующие `work_dirs` — `--add-dir`,
    веб-поиск — как в настройках Codex. На любом уровне MCP `servers`
    выключены (`servers` None — список не прочитался — или имя, которое
    ключом не выключить, → `--ignore-user-config`; тогда `windows_sandbox` —
    значение `[windows] sandbox` пользователя — передаётся `-c`), приложения,
    браузер и управление компьютером — тоже."""
    if access is None:
        return ["--sandbox", "read-only"]
    if access == "user":
        out = ["--approve-for-me"]
        for d in work_dirs or ():
            if d and Path(d).is_dir():
                out += ["--add-dir", str(d)]
    else:
        out = ["--sandbox", "read-only"]
    enabled = [s for s in servers or () if not isinstance(s, dict) or s.get("enabled", True)] \
        if servers is not None else None
    stubs = {"stdio": 'command="__meet_disabled_mcp__"',
             "streamable_http": 'url="http://127.0.0.1:9/__meet_disabled_mcp__"'}
    entries = [(s.get("name"), s.get("transport")) if isinstance(s, dict) else (s, None)
               for s in enabled or ()]
    if enabled is None or any(not (isinstance(n, str) and _MCP_KEY.match(n) and t in stubs)
                              for n, t in entries):
        out.append("--ignore-user-config")     # в закрытую сторону: без config.toml — без MCP
        if windows_sandbox and _SANDBOX_VALUE.fullmatch(windows_sandbox):
            # Без config.toml пропал бы и `[windows] sandbox`: запись на Windows — только с ним.
            out += ["-c", f'windows.sandbox="{windows_sandbox}"']
    else:
        for name, kind in entries:
            # Заглушка того же транспорта, что у сервера: команда в таблице с url
            # (и наоборот) ломает ход — «url is not supported for stdio».
            out += ["-c", f"mcp_servers.{name}={{enabled=false,{stubs[kind]}}}"]
    for feature in _NONE_FEATURES:
        out += ["--disable", feature]
    if access != "user":
        out += ["-c", "tools.web_search=false"]
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
                  access: str | None = None, servers=(), work_dirs=(),
                  model: str | None = None, windows_sandbox: str | None = None,
                  json_events: bool = False) -> list[str]:
    """Командная строка `codex exec` (prompt — в stdin, `-` последним).
    Продолжение: песочница, папки и автопроверка — до `resume` (у подкоманды
    их нет), остальное — после неё (и `-m model`: он есть у обеих), id
    сеанса — перед `-`. `access` — уровень хода (`access_args`; `servers` —
    MCP-серверы, что выключить; `work_dirs` — рабочие папки хода USER;
    `windows_sandbox` — значение пользователя для запасного пути). `model`
    уже проверен (`model_error`). `json_events` — события JSON и без
    сохраняемого сеанса (ход работы по ходу вызова)."""
    head = [exe, "exec", *access_args(access, servers, work_dirs, windows_sandbox=windows_sandbox),
            "--skip-git-repo-check"]
    # Сохраняемый сеанс — события JSON (id в thread.started; проверено на 0.159.0).
    events = ["--json"] if keep_session or resume or json_events else []
    rest = [*events, "--output-last-message", out_file,
            *(["-m", model] if model else []),
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


# Вызов, который прервали (отмена хода, таймаут): строка закрывается ошибкой.
INTERRUPTED = "прервано"


def _event_item(line: str) -> tuple[str | None, dict]:
    """Строка `codex exec --json` → (вид события, элемент); чужая — (None, {})."""
    line = (line or "").strip()
    if not line.startswith("{"):
        return None, {}
    try:
        event = json.loads(line)
    except ValueError:
        return None, {}
    if not isinstance(event, dict):
        return None, {}
    item = event.get("item") if isinstance(event.get("item"), dict) else {}
    return event.get("type"), item


def _item_call(item: dict) -> tuple[str, dict] | None:
    """Элемент Codex → (имя инструмента, аргументы); не вызов — None."""
    kind = item.get("type")
    if kind == "command_execution":
        return "Bash", {"command": str(item.get("command") or "")}
    if kind == "file_change":
        changes = [{"path": str(c.get("path")), "kind": str(c.get("kind") or "")}
                   for c in item.get("changes") or () if isinstance(c, dict) and c.get("path")]
        return "apply_patch", {"changes": changes}
    if kind == "mcp_tool_call":
        server, tool = str(item.get("server") or "?"), str(item.get("tool") or "?")
        return f"mcp__{server}__{tool}", item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
    if kind == "web_search":
        return "WebSearch", {"query": str(item.get("query") or "")}
    return None


def _use_event(iid: str, name: str, data: dict) -> dict:
    server, tool = (name[5:].split("__", 1) if name.startswith("mcp__") else (None, name))
    return {"type": "tool_use", "id": iid, "name": name, "server": server, "tool": tool,
            "input": data, "parent": None}


def _result_event(iid: str, ok: bool, output: str) -> dict:
    return {"type": "tool_result", "id": iid, "ok": ok, "output": output, "truncated": False,
            "duration_ms": None, "parent": None}


def _item_result(item: dict, data: dict) -> tuple[bool, str]:
    """Законченный элемент → (удался ли, вывод)."""
    kind = item.get("type")
    error = item.get("error") if isinstance(item.get("error"), dict) else {}
    failed = item.get("status") == "failed"
    if kind == "command_execution":
        return not failed and item.get("exit_code") in (0, None), str(item.get("aggregated_output") or "")
    if kind == "file_change":
        return not failed, "\n".join(f"{c['kind']}: {c['path']}" for c in data.get("changes") or ())
    if kind == "mcp_tool_call":
        result = item.get("result")
        return not failed, str(error.get("message") or "") if failed else (
            json.dumps(result, ensure_ascii=False) if result is not None else "")
    return not failed, ""


class ToolStream:
    """Ход работы по ходу вызова (`run(on_event=…)`): строка за строкой
    `item.started` → `tool_use`, `item.completed` → `tool_result` (без начала —
    оба сразу). Веб-поиск без запроса в начале (запрос Codex дописывает к
    концу) — строка при завершении: вызов одной строкой не переписать
    (`tool_rows` берёт первый `tool_use` по id). `abort` — открытые вызовы
    закрываются «прервано»."""

    def __init__(self) -> None:
        self._open: dict[str, None] = {}

    def feed(self, line: str) -> list[dict]:
        kind, item = _event_item(line)
        if kind not in ("item.started", "item.completed"):
            return []
        iid = str(item.get("id") or "")
        call = _item_call(item) if iid else None
        if call is None:
            return []
        name, data = call
        if kind == "item.started":
            if iid in self._open or (name == "WebSearch" and not data["query"]):
                return []
            self._open[iid] = None
            return [_use_event(iid, name, data)]
        out = [] if self._open.pop(iid, False) is None else [_use_event(iid, name, data)]
        return out + [_result_event(iid, *_item_result(item, data))]

    def abort(self) -> list[dict]:
        out = [_result_event(iid, False, INTERRUPTED) for iid in self._open]
        self._open.clear()
        return out


def tool_events(stdout: str) -> list[dict]:
    """Ход работы для чата (0.4, `assist.tool_rows`) из событий `codex exec
    --json` после вызова: законченные элементы (`item.completed`) — команды
    (`command_execution` → Bash), правки файлов (`file_change` →
    `apply_patch`), вызовы MCP (`mcp_tool_call` → `mcp__сервер__инструмент`)
    и веб-поиск (`web_search` → WebSearch) — парами событий `tool_use` и
    `tool_result`, как у `claude_stream`. По ходу вызова те же события даёт
    `ToolStream` (`run(on_event=…)`). Остановить вызов до выполнения Codex не
    даёт. Чужие строки и поля пропускаются."""
    out: list[dict] = []
    for line in (stdout or "").splitlines():
        kind, item = _event_item(line)
        iid = str(item.get("id") or "")
        call = _item_call(item) if kind == "item.completed" and iid else None
        if call is None:
            continue
        name, data = call
        out += [_use_event(iid, name, data), _result_event(iid, *_item_result(item, data))]
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


def _deliver(loop, on_event):
    """Событие из потока чтения → `on_event` в цикле событий `loop`; цикл
    закрыт (ассистент выходит) — событие пропадает. Сбой обработчика ход не
    роняет."""
    def handle(event: dict) -> None:
        try:
            on_event(event)
        except Exception:
            pass

    def emit(event: dict) -> None:
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(handle, event)
        except RuntimeError:
            pass
    return emit


def _stream(proc, stdin_text: str, timeout_s: float, emit) -> tuple[str, str] | None:
    """Вызов с ходом работы по ходу: stdin пишется и закрывается в своём
    потоке, stdout читается построчно в своём (`ToolStream` → `emit`), от
    stderr остаётся хвост `_STDERR_TAIL`. Не уложился в `timeout_s` —
    дерево процессов убито, → None. Убит отменой или таймаутом — открытые
    вызовы закрываются «прервано». → (stdout, stderr)."""
    import threading

    lines: list[str] = []
    err: list[str] = []
    tools = ToolStream()

    def write_in() -> None:
        try:
            proc.stdin.write(stdin_text.encode("utf-8"))
        except (OSError, ValueError):
            pass
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    def read_out() -> None:
        try:
            for raw in proc.stdout:
                text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                lines.append(text)
                for event in tools.feed(text):
                    emit(event)
        except (OSError, ValueError):
            pass

    def read_err() -> None:
        try:
            for raw in proc.stderr:
                err.append(raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw))
                if sum(len(t) for t in err) > _STDERR_TAIL * 2:
                    err[:] = ["".join(err)[-_STDERR_TAIL:]]
        except (OSError, ValueError):
            pass

    threads = [threading.Thread(target=fn, name=f"codex-{fn.__name__}", daemon=True)
               for fn in (write_in, read_out, read_err)]
    for t in threads:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            pass
    for t in threads:
        t.join(timeout=10)      # внук мог унаследовать вывод — не ждём его вечно
    for event in tools.abort():
        emit(event)
    if timed_out:
        return None
    return "".join(lines), "".join(err)[-_STDERR_TAIL:].strip()


def _exec(exe: str, workdir: str, stdin_text: str, timeout_s: float,
          env: dict | None = None, effort: str | None = None, images=(),
          keep_session: bool = False, resume: str | None = None,
          call: _Call | None = None, access: str | None = None, work_dirs=(),
          model: str | None = None, on_event=None, loop=None) -> AgentReply:
    # ignore_cleanup_errors: убитый по таймауту Codex может ещё держать файл.
    # Папка с pid в имени (meet.tempdirs): процесс убили посреди ответа — файл
    # с ответом модели о встрече удалит резидент.
    with tempfile.TemporaryDirectory(prefix=tempdirs.prefix("codex-"),
                                     ignore_cleanup_errors=True) as tmp:
        out_file = Path(tmp) / "last-message.txt"
        servers = mcp_servers(exe, env) if access is not None else ()
        sandbox = windows_sandbox() if access is not None else None
        cmd = build_command(exe, workdir, str(out_file), effort=effort, images=images,
                            keep_session=keep_session, resume=resume, access=access,
                            servers=servers, work_dirs=work_dirs, model=model,
                            windows_sandbox=sandbox, json_events=on_event is not None)
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, **popen_kwargs(),
            )
        except OSError as e:
            return AgentReply(text="", error=f"не удалось запустить Codex: {e}")
        if call is not None:
            call.proc = proc
            if call.cancelled:
                _kill_tree(proc)
        if on_event is not None:
            got = _stream(proc, stdin_text, timeout_s, _deliver(loop, on_event))
            if got is None:
                return AgentReply(text="", error=TIMEOUT_ERROR)
            stdout, stderr = got
        else:
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
    on_event=None,
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
    0.3.6.

    `model` — `-m` (см. модуль); негодное имя — ошибка без запуска Codex.

    `on_event` — ход работы по ходу вызова: события `tool_use`/`tool_result`
    (`ToolStream`) приходят в цикл событий вызывающего, пока Codex работает
    (с `--json`); прерванные вызовы закрываются «прервано». `reply.tools` —
    те же события после вызова, как и без `on_event`."""
    if resume and not is_uuid(resume):
        return resume_failure(f"неверный id сеанса Codex: {resume!r}")
    if model_error(model):
        return AgentReply(text="", error=model_error(model))
    model = model.strip() if isinstance(model, str) and model.strip() else None
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
            sent, keep_session, resume, call, access, tuple(work_dirs or ()), model,
            on_event, asyncio.get_running_loop() if on_event is not None else None,
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
