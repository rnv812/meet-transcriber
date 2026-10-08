"""Провайдер Claude Code через claude-agent-sdk.

Один per-call вызов модели (свежая сессия или resume), строгий системный
промпт, без настроек проекта. Изменяющие/сетевые/шелл-инструменты запрещены
всегда; чтение папок разрешает колбэк по списку допустимых папок.
claude_agent_sdk импортируется только внутри функций: модуль можно
импортировать в резиденте, вызывать — только в подпроцессе.
"""

import asyncio
import base64
import logging
import os
import uuid
from pathlib import Path

from meet import netproxy
from meet.llm.base import (
    DEFAULT_CLAUDE_MODEL, TIMEOUT_ERROR, AgentReply, check_image, claude_deny_rules, claude_model,
    drop_session_markers, image_note, is_uuid, model_matches, path_variants, resume_failure, split_images,
)
from meet.llm.detect import claude_not_found, find_claude

log = logging.getLogger(__name__)

# Инструменты, запрещённые всегда (в обеих линиях): ассистент ничего не меняет
# и не ходит в сеть/шелл. Read/Grep/Glob сюда не входят — их судьбу решает
# колбэк разрешений (Q&A с хранилищем vs дайджестер совсем без тулзов).
ALL_TOOLS_DENIED = [
    "Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch",
    "Task", "TodoWrite",
]
READ_TOOLS = ("Read", "Grep", "Glob")

# Флаг CLI «не сохранять сеанс» (claude --help 2.1.287: «only works with
# --print»; CLI считает режим --print и тогда, когда stdout не терминал, — так
# его и запускает SDK). ClaudeAgentOptions.extra_args: None — флаг без значения.
NO_PERSISTENCE = {"no-session-persistence": None}


def text_delta(event) -> str:
    """Кусок текста ответа из события потока Anthropic API (`content_block_delta`
    с `text_delta`); размышления и прочие события — пустая строка."""
    if not isinstance(event, dict) or event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta") or {}
    if delta.get("type") != "text_delta":
        return ""
    return str(delta.get("text") or "")


# Что пишет CLI, когда сеанса для --resume нет (2.1.292).
RESUME_MISSING = "no conversation found"


def _block(media: str, data: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media,
                                        "data": base64.b64encode(data).decode("ascii")}}


def image_block(path) -> dict:
    """Блок изображения Anthropic API (base64) из файла; тип — по первым
    байтам. Не годится (`base.check_image`: тип, 5 МБ base64, 8 000 px,
    битый файл) — ValueError, не читается — OSError."""
    media, data = check_image(path)
    return _block(media, data)


def user_content(text: str, images=()) -> tuple[str | list[dict], list[tuple[str, str]]]:
    """(`message.content`, [(путь, причина)] не отправленных изображений).

    Без изображений — строка (как раньше); с ними — блоки: изображения, затем
    текст (порядок, который советует документация Anthropic API для vision).
    Негодное изображение не роняет сообщение: оно не уходит, а модели в
    текст добавляется пометка «Изображение «…» не отправлено: причина»."""
    good, dropped = split_images(images)
    notes = [image_note(path, why) for path, why in dropped]
    if notes:
        text = "\n".join([text, *[f"({n})" for n in notes]]) if text else "\n".join(notes)
    if not good:
        return text, dropped
    blocks = [_block(media, data) for _, media, data in good]
    if text:
        blocks.append({"type": "text", "text": text})
    return blocks, dropped


def dropped_fields(dropped) -> dict:
    """Поля AgentReply о не отправленных изображениях."""
    return {"dropped_images": [p for p, _ in dropped],
            "notes": [image_note(p, why) for p, why in dropped]}


def find_cli() -> str | None:
    """Путь к Claude Code CLI (см. `meet.llm.detect.find_claude`)."""
    return find_claude()


def drop_api_key() -> bool:
    """Убрать ANTHROPIC_API_KEY из окружения этого процесса.

    Ключ перебил бы подписку Claude в CLI. SDK умеет только добавлять
    переменные (`ClaudeAgentOptions.env`), но не удалять их, поэтому чистим
    собственное окружение: вызовы модели идут в отдельном процессе
    (job_worker, meet assist, meet.llm.check), резидента это не касается.
    """
    if os.environ.pop("ANTHROPIC_API_KEY", None) is None:
        return False
    log.warning("ANTHROPIC_API_KEY убран из окружения: Claude Code работает по подписке")
    return True


def make_permission_callback(allowed_dirs: tuple[Path, ...], denied_dirs=()):
    denied = [Path(v) for d in denied_dirs or () if d for v in path_variants(d)]

    async def can_use_tool(tool_name, input_data, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if tool_name in READ_TOOLS and allowed_dirs:
            raw = (input_data or {}).get("file_path") or (input_data or {}).get("path")
            if raw is None:
                # Grep/Glob без path работают от cwd (папка записи) — ок.
                # Read без file_path не имеет смысла и не должен проходить.
                if tool_name in ("Grep", "Glob"):
                    return PermissionResultAllow()
                return PermissionResultDeny(message="Read без file_path")
            try:
                target = Path(raw).resolve()
            except (OSError, ValueError):
                return PermissionResultDeny(message="некорректный путь")
            for bad in denied:  # WindowsPath сравнивает без учёта регистра
                if target.is_relative_to(bad):
                    return PermissionResultDeny(message=f"папка закрыта для чтения: {bad}")
            for base in allowed_dirs:
                if target.is_relative_to(Path(base).resolve()):
                    return PermissionResultAllow()
            return PermissionResultDeny(
                message=f"путь вне разрешённых папок: {target}"
            )
        return PermissionResultDeny(message=f"инструмент {tool_name} отключён")

    return can_use_tool


async def run(
    prompt: str,
    *,
    system_prompt: str,
    model: str | None = DEFAULT_CLAUDE_MODEL,
    resume: str | None = None,
    session_id: str | None = None,
    allowed_dirs: tuple[Path, ...] = (),
    cwd: str | Path | None = None,
    timeout_s: float = 180.0,
    max_turns: int = 8,
    proxy: str | None = None,
    on_text=None,
    thinking: str | None = None,
    images=(),
    keep_session: bool = False,
    deny_paths=(),
    effort: str | None = None,
) -> AgentReply:
    """Один вызов Claude через Agent SDK: свежая сессия (или resume), строгий
    системный промпт, без настроек проекта; ошибки — в AgentReply.error.

    По умолчанию сеанс на диск не сохраняется (`--no-session-persistence`,
    NO_PERSISTENCE): фоновые вызовы — итоги, анализ, названия, тики живого
    ассистента — не засоряют историю Claude Code человека, и `session_id` не
    возвращается (такой сеанс не продолжить).

    Свой сохраняемый сеанс — только по явной просьбе: `session_id` (новый
    сеанс с этим UUID, `--session-id`) или `resume` (продолжить его). Так
    живёт память вопросов живого ассистента (QAService); в ответе — id
    сеанса. Вкладку «Агент» он не задевает: она продолжает свой сеанс по
    своему id.

    `proxy` — `llm.proxy` (по умолчанию «как в системе»): Claude Code сам
    системный прокси Windows не видит, его передаём переменными.

    `on_text(кусок)` — текст ответа по мере генерации (частичные сообщения
    CLI): окно показывает ответ, не дожидаясь конца. `thinking="disabled"` —
    без размышлений (только «Быстрее»: haiku иначе думает по умолчанию и
    отвечает минутами); None — как у модели по умолчанию.

    `keep_session` — начать сохраняемый сеанс со своим UUID (как
    `session_id`, id — в ответе). `resume`, которое не вышло (сеанса нет,
    истёк, CLI отказал до начала хода), — ответ `resume_failed`.
    `images` — пути к изображениям: блоки base64 в сообщении (негодные не
    уходят — `dropped_images`/`notes`). `deny_paths` — папки, закрытые для
    чтения (`kb_exclude`): правила `Read(//…/**)` и отказ в колбэке.

    `model` уходит в CLI всегда (`--model`, и при `resume`): пустая —
    DEFAULT_CLAUDE_MODEL, не модель CLI по умолчанию. Какая модель отвечала
    на самом деле (`model` из `system/init`) — `AgentReply.model`."""
    import claude_agent_sdk
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ResultMessage, StreamEvent, SystemMessage, TextBlock,
    )

    if resume and not is_uuid(resume):
        return resume_failure(f"неверный id сеанса: {resume!r}")
    content, dropped = user_content(prompt, images)
    if keep_session and not (resume or session_id):
        session_id = str(uuid.uuid4())
    drop_api_key()
    # Как и ключ — из окружения своего процесса (SDK переменные только добавляет).
    drop_session_markers(os.environ)
    persist = bool(resume or session_id)
    model = claude_model(model)
    options = ClaudeAgentOptions(
        env=netproxy.prepare(proxy),
        system_prompt=system_prompt,
        model=model,
        resume=resume,
        cwd=str(cwd) if cwd else None,
        cli_path=find_cli(),
        setting_sources=[],
        disallowed_tools=ALL_TOOLS_DENIED + (
            [] if allowed_dirs else list(READ_TOOLS)
        ) + claude_deny_rules(deny_paths),
        can_use_tool=make_permission_callback(allowed_dirs, deny_paths),
        max_turns=max_turns,
        session_id=None if resume else session_id,
        extra_args={} if persist else dict(NO_PERSISTENCE),
        include_partial_messages=on_text is not None,
        thinking={"type": thinking} if thinking else None,
        # Уровень рассуждений из «Моделей ИИ» (0.5); нет — по умолчанию CLI.
        **({"effort": effort} if effort else {}),
    )

    # can_use_tool в этой версии SDK требует streaming-режима ввода: строка-prompt
    # даёт ValueError. Отдаём prompt как AsyncIterable из одного user-сообщения —
    # это включает streaming input и сохраняет per-call семантику.
    async def _single_message():
        yield {"type": "user", "message": {"role": "user", "content": content}}

    text_parts: list[str] = []
    result_text: str | None = None
    reported: str | None = None
    error: str | None = None
    actual: str | None = None   # модель из system/init

    streamed = False
    began = False  # было system/init: CLI начал ход (сеанс для resume найден)

    async def _consume() -> None:
        nonlocal result_text, reported, error, streamed, began, actual
        async for msg in claude_agent_sdk.query(prompt=_single_message(), options=options):
            if isinstance(msg, SystemMessage) and getattr(msg, "subtype", None) == "init":
                began = True
                data = getattr(msg, "data", None)
                data = data if isinstance(data, dict) else {}
                actual = str(data.get("model") or "").strip() or None
                if actual and not model_matches(model, actual):
                    log.warning("Claude Code запустил модель %s, а задана %s", actual, model)
                continue
            if on_text is not None and isinstance(msg, StreamEvent):
                event = msg.event if isinstance(msg.event, dict) else {}
                # Новое сообщение модели после инструмента: прежний текст был
                # пояснением к нему («посмотрю заметки»), ответ — дальше.
                # on_text(None) — «начать текст заново».
                piece = None if event.get("type") == "message_start" else text_delta(event)
                if piece is None and not streamed:
                    continue
                if piece == "":
                    continue
                streamed = piece is not None
                try:
                    on_text(piece)
                except Exception:  # сбой показа не обрывает ответ
                    log.exception("on_text")
                continue
            if isinstance(msg, AssistantMessage):
                if getattr(msg, "error", None):
                    error = str(msg.error)
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
            elif isinstance(msg, ResultMessage):
                reported = msg.session_id
                if getattr(msg, "result", None):
                    result_text = msg.result
                if msg.is_error and not error:
                    error = msg.subtype

    try:
        await asyncio.wait_for(_consume(), timeout=timeout_s)
    except asyncio.TimeoutError:
        error = TIMEOUT_ERROR
    except Exception as e:  # ProcessError, CLIConnectionError, JSONDecode...
        error = f"{type(e).__name__}: {e}"
    if resume and error and error != TIMEOUT_ERROR and (
            not began or RESUME_MISSING in str(error).lower()):
        failed = resume_failure(str(error))
        for key, value in dropped_fields(dropped).items():
            setattr(failed, key, value)
        return failed
    return AgentReply(
        text=(result_text or "".join(text_parts)).strip(),
        session_id=(reported or resume or session_id) if persist else None,
        error=netproxy.with_hint(error),
        model=actual,
        **dropped_fields(dropped),
    )


def _config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def forget_session(session_id: str) -> int:
    """Удалить сохранённый сеанс Claude Code (у CLI нет команды удаления):
    `projects/<папка>/<id>.jsonl` и папку `<id>/` рядом (вложения, подагенты)
    в его папке настроек. Только UUID; → сколько удалено."""
    import shutil

    if not is_uuid(session_id):
        return 0
    removed = 0
    config = _config_dir()
    # Спутники сеанса вне projects/ (история правок файлов, окружение, задачи,
    # отладка) — тоже по id; их в счёт не берём.
    for d in (config / "file-history" / session_id, config / "session-env" / session_id):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    for f in [*config.glob(f"todos/{session_id}*.json"), config / "debug" / f"{session_id}.txt"]:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass
    root = config / "projects"
    if not root.is_dir():
        return 0
    for f in root.glob(f"*/{session_id}.jsonl"):
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    for d in root.glob(f"*/{session_id}"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    return removed



# --- вкладка «Агент»: проект Claude Code папки записи --------------------------

# Сколько первых строк сеанса смотреть в поисках его рабочей папки (`cwd`).
_CWD_SCAN_LINES = 50


def project_dir_name(folder) -> str:
    """Имя папки проекта в `projects/` Claude Code: путь, где каждый знак,
    кроме латиницы и цифр, заменён на «-» (так его кодирует CLI)."""
    return "".join(c if c.isascii() and c.isalnum() else "-" for c in str(folder))


def _same_path(a: str, b) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(str(b)))


def _session_cwd(path: Path) -> str | None:
    """Рабочая папка сеанса по первым строкам его .jsonl; не нашлась — None."""
    import json

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _, line in zip(range(_CWD_SCAN_LINES), f):
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict) and isinstance(data.get("cwd"), str):
                    return data["cwd"]
    except OSError:
        return None
    return None


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda _p: False)(path))
    except OSError:
        return True


def forget_project(folder, log=None) -> dict:
    """Следы вкладки «Агент» по папке записи (её агент работал с cwd = папка
    записи): папка проекта `projects/<закодированный путь>/` (сеансы, память
    `memory/`) и строки `history.jsonl` с этим `project` или id его сеансов.

    Папку проекта удаляем целиком, только если все её сеансы — из этой папки
    (имя у разных путей может совпасть: кодирование с потерями); иначе — только
    свои сеансы. Ссылки не проходим, за пределы `projects/` не выходим.
    `~/.claude.json` (там запись о проекте) не трогаем: CLI переписывает его
    целиком, пока работает, и наша правка могла бы потерять его изменения.
    → {"project": удалена ли папка проекта, "sessions": [id], "history": строк}."""
    import shutil

    config = _config_dir()
    projects = config / "projects"
    report = {"project": False, "sessions": [], "history": 0}
    target = projects / project_dir_name(folder)
    try:
        inside = (target.parent.resolve() == projects.resolve()
                  and target.name not in ("", ".", ".."))
    except OSError:
        inside = False
    if inside and target.is_dir() and not _is_link(target) and not _is_link(projects):
        sessions = sorted(p for p in target.glob("*.jsonl") if p.is_file() and not _is_link(p))
        cwds = {p: _session_cwd(p) for p in sessions}
        mine = [p for p, cwd in cwds.items() if cwd is not None and _same_path(cwd, folder)]
        foreign = [p for p, cwd in cwds.items() if cwd is not None and not _same_path(cwd, folder)]
        report["sessions"] = [p.stem for p in mine]
        if not foreign:
            shutil.rmtree(target, ignore_errors=True)
            report["project"] = not target.exists()
        else:
            for p in mine:
                try:
                    p.unlink()
                except OSError:
                    pass
                side = target / p.stem
                if side.is_dir() and not _is_link(side):
                    shutil.rmtree(side, ignore_errors=True)
    report["history"] = forget_history(config / "history.jsonl", folder, report["sessions"],
                                       log=log)
    if log and (report["project"] or report["history"]):
        log(f"вкладка «Агент» забыта: папка проекта {'удалена' if report['project'] else 'не найдена'}, "
            f"строк истории — {report['history']}")
    return report


# Незнакомая строка в history.jsonl выключает его чистку навсегда — сказать
# об этом в журнал один раз за жизнь процесса, не на каждое удаление.
_HISTORY_SKIP_LOGGED = False


def _history_skipped(log, why: str) -> int:
    global _HISTORY_SKIP_LOGGED
    if log and not _HISTORY_SKIP_LOGGED:
        _HISTORY_SKIP_LOGGED = True
        log(f"history.jsonl Claude Code не почищен: {why} — файл не трогаю")
    return 0


def forget_history(path: Path, folder, session_ids=(), log=None) -> int:
    """Убрать из `history.jsonl` строки этой папки (`project`) или этих сеансов
    (`sessionId`). Безопасно или никак: формат не тот (строка не JSON-объект)
    — не трогаем; остальные строки — байт в байт; запись — атомарная, и если
    файл поменялся, пока мы его читали (CLI дописал строку), — не трогаем.
    → сколько строк убрано."""
    import json

    path = Path(path)
    if _is_link(path) or not path.is_file():
        return 0
    try:
        before = os.stat(path)
        raw = path.read_bytes()
    except OSError:
        return 0
    ids = {s for s in session_ids if isinstance(s, str)}
    kept: list[bytes] = []
    removed = 0
    for line in raw.splitlines(keepends=True):
        body = line.strip()
        if not body:
            kept.append(line)
            continue
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return _history_skipped(log, "строка не JSON")  # формат не наш — не трогаем вовсе
        if not isinstance(data, dict):
            return _history_skipped(log, "строка не объект JSON")
        project = data.get("project")
        sid = data.get("sessionId")
        if (isinstance(project, str) and _same_path(project, folder)) or (isinstance(sid, str) and sid in ids):
            removed += 1
            continue
        kept.append(line)
    if not removed:
        return 0
    tmp = path.with_name(f".{path.name}.{os.getpid()}.meet-tmp")
    try:
        tmp.write_bytes(b"".join(kept))
        # Проверка — вплотную перед заменой (временный файл уже написан):
        # CLI дописал строку, пока читали или писали, — его строку не теряем.
        # Остаётся окно между этой проверкой и самой заменой (микросекунды).
        now = os.stat(path)
        if (now.st_size, now.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            return 0
        os.replace(tmp, path)
    except OSError:
        return 0
    finally:
        tmp.unlink(missing_ok=True)
    return removed

async def check_auth(proxy: str | None = None, model: str = "haiku") -> str | None:
    """Проверка авторизации коротким вызовом. None = ок, иначе текст проблемы.
    `model` — чем проверять: кнопка «Проверить» передаёт `llm.model`, чтобы
    опечатка в имени модели была видна сразу, а не в фоновых задачах.

    ANTHROPIC_API_KEY не ошибка: он убирается из окружения (подписка важнее)."""
    drop_api_key()
    if find_cli() is None:
        return claude_not_found(detail=True)
    reply = await run(
        "Ответь одним словом: ок",
        system_prompt="Отвечай одним словом.",
        model=model, max_turns=1, timeout_s=60.0, proxy=proxy,
    )
    return reply.error
