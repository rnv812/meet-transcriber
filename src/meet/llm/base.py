"""Общий контракт провайдеров модели.

Каждый провайдер — async-функция с одной сигнатурой (её уже потребляют
Digester и QAService):

    runner(prompt, *, system_prompt, model, resume, session_id, allowed_dirs,
           cwd, timeout_s, max_turns, on_text, images, keep_session) -> AgentReply

Сеансы (нативное продолжение, v4-design §12):
* `keep_session=True` — начать сохраняемый сеанс провайдера; его id — в
  `AgentReply.session_id`. Claude Code принимает и свой id нового сеанса
  (`session_id`, UUID); Codex и OpenCode id выдают сами.
* `resume=<id>` — продолжить сохранённый сеанс. Не вышло (сеанс неизвестен,
  истёк, CLI отказал до начала хода) — `AgentReply.resume_failed`, ошибка
  начинается с RESUME_ERROR: вызывающий начинает новый сеанс с затравкой
  из журнала.
* Без них — вызов без сохранения сеанса, как раньше. `session_id` без
  `keep_session` у Codex и OpenCode по-прежнему игнорируется.
* Локальная модель сеансов не держит (`supports_resume` — False).

`images` — пути к изображениям для этого сообщения. Видят их Claude Code и
Codex (`llm.vision`); остальные параметр принимают и не отправляют. Что не
ушло модели (не видит изображений; файл не годится — `check_image`), — в
`AgentReply.dropped_images`, объяснение для окна — в `AgentReply.notes`;
модели в текст сообщения добавляется та же пометка.

`deny_paths` — папки, которые агенту читать нельзя (`kb_exclude`). Claude
Code — правила `Read(//…/**)` в `--disallowedTools` (CLI их соблюдает,
`deny_enforced`); OpenCode — запрет в правах агента [не проверено]; Codex —
только правило в тексте (песочница read-only читает весь диск).
`on_text(кусок)` — текст ответа по мере генерации (Claude Code; Codex и
локальная модель отдают ответ целиком, параметр принимают и не зовут).
`on_text(None)` — новое сообщение модели (после инструмента): показанный
текст был пояснением, ответ начинается заново.

Ошибки не бросаются, а возвращаются в `AgentReply.error`.
"""

import io
import os
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

# Одинаковый текст таймаута у всех провайдеров: по нему вызывающий отличает
# «модель не успела» от прочих ошибок.
TIMEOUT_ERROR = "таймаут вызова модели"
EMPTY_ERROR = "модель вернула пустой ответ"
# Ход остановлен по просьбе (кнопка «Стоп»): не сбой модели.
CANCELLED_ERROR = "вызов отменён"
# Начало текста ошибки «сохранённый сеанс не продолжить» (+ AgentReply.resume_failed).
RESUME_ERROR = "сеанс модели не продолжить"

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Изображения, которые принимают модели (Anthropic API и Codex): расширение → тип.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
# Пределы Anthropic API: 5 МБ на изображение — длина base64 (не сырых байтов:
# base64 больше на треть), сторона — до 8 000 px.
IMAGE_MAX_BASE64 = 5 * 1024 * 1024
IMAGE_MAX_SIDE = 8000
NO_VISION_NOTE = "Модель не видит изображения — ушёл только текст сообщения"

# Модель Claude Code по умолчанию (`llm.model` пусто). Каждый вызов Claude Code
# передаёт модель явно (`--model`): без флага CLI взял бы модель из
# ~/.claude/settings.json или свою по умолчанию — самую новую, не ту, что в
# настройках Meet (отчёт v037 model-pick).
DEFAULT_CLAUDE_MODEL = "sonnet"
# Псевдонимы моделей Claude Code (`claude --help` 2.1.292: «an alias for the
# latest model (e.g. 'fable', 'opus', or 'sonnet')»): CLI сам выбирает
# последнюю модель семейства, в `system/init` — её полное имя.
CLAUDE_ALIASES = ("fable", "opus", "sonnet", "haiku", "mythos")
# Псевдонимы без семейства: модель выбирает сам CLI (`default` — по умолчанию,
# `best` — лучшая доступная): сравнивать не с чем.
CLAUDE_ANY = ("default", "best")


def claude_model(value) -> str:
    """Модель для `--model`: заданная, пустая — DEFAULT_CLAUDE_MODEL (не модель CLI по умолчанию)."""
    text = str(value or "").strip()
    return text or DEFAULT_CLAUDE_MODEL


def model_matches(configured: str | None, actual: str | None) -> bool:
    """Та ли модель запустилась (`actual` — из `system/init` CLI), что задана
    (`configured`: псевдоним или полное имя). Псевдоним — по семейству в имени
    («opus» ↔ «claude-opus-5-5»); полное имя — с точностью до регистра,
    суффикса `[1m]`, даты и приставки провайдера. `default`, `best` и
    неизвестное — не с чем сравнить: совпадает."""
    def norm(v):
        return str(v or "").strip().lower().removesuffix("[1m]")

    want, got = norm(configured), norm(actual)
    if not want or not got or want in CLAUDE_ANY:
        return True
    if want == "opusplan":
        return "opus" in got or "sonnet" in got
    if want in CLAUDE_ALIASES:
        return want in got
    return want in got or got in want


@dataclass
class AgentReply:
    text: str
    session_id: str | None = None
    error: str | None = None
    # Сколько токенов насчитал сервер (`prompt_tokens`, `completion_tokens`) —
    # только у локальной модели: по `prompt_tokens` видно, что промпт обрезан.
    usage: dict | None = None
    # Продолжить сохранённый сеанс (`resume`) не вышло: сеанс неизвестен,
    # истёк или CLI отказал до начала хода. Вызывающий начинает новый сеанс
    # и кладёт контекст затравкой из журнала.
    resume_failed: bool = False
    # Ход остановлен по просьбе (`Conversation.interrupt`); `text` — что успело прийти.
    cancelled: bool = False
    # Изображения, не ушедшие модели (пути), и пометки для окна («Изображение
    # «a.png» не отправлено: …»). Та же пометка ушла модели в тексте сообщения.
    dropped_images: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Какая модель на самом деле отвечала (`model` из `system/init` Claude
    # Code); None — неизвестно (другие провайдеры, CLI не начал ход).
    model: str | None = None
    # Контекст сжат в этом ходе (`/compact` или авто-сжатие Claude Code,
    # `system/compact_boundary`): `{"trigger", "pre_tokens", "post_tokens"}`.
    compacted: dict | None = None


def resume_failure(detail: str | None) -> AgentReply:
    """Ответ «сохранённый сеанс не продолжить» с подробностями CLI."""
    detail = (detail or "").strip()
    return AgentReply(text="", error=f"{RESUME_ERROR}: {detail}" if detail else RESUME_ERROR,
                      resume_failed=True)


def is_uuid(value) -> bool:
    # fullmatch, а не match с `$`: `$` пропускает хвостовой перевод строки.
    return isinstance(value, str) and bool(_UUID.fullmatch(value))


def image_media_type(path) -> str | None:
    """MIME-тип изображения по расширению; не изображение для модели — None."""
    return IMAGE_TYPES.get(Path(str(path)).suffix.lower())


def sniff_image(data: bytes) -> str | None:
    """MIME-тип по первым байтам (расширение может врать: .png, а внутри JPEG —
    API ответил бы «image does not match media type»)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def base64_size(n: int) -> int:
    return 4 * ((n + 2) // 3)


def check_image(path) -> tuple[str, bytes]:
    """(MIME-тип по содержимому, байты) — или ValueError с причиной для
    человека (OSError — файл не читается). Проверки: PNG/JPEG/GIF/WebP по
    первым байтам; base64 ≤ 5 МБ; Pillow (если есть) открывает файл и сторона
    ≤ 8 000 px."""
    data = Path(path).read_bytes()
    media = sniff_image(data)
    if media is None:
        raise ValueError("не изображение PNG, JPEG, GIF или WebP")
    if base64_size(len(data)) > IMAGE_MAX_BASE64:
        raise ValueError(f"больше 5 МБ для модели ({len(data) / 1024 / 1024:.1f} МБ файла)")
    try:
        from PIL import Image
    except ImportError:
        return media, data
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            img.verify()
    except Exception as e:  # Pillow бросает разное на битых файлах
        raise ValueError(f"файл изображения повреждён ({type(e).__name__})") from None
    if max(width, height) > IMAGE_MAX_SIDE:
        raise ValueError(f"больше {IMAGE_MAX_SIDE} px по стороне ({width}×{height})")
    return media, data


def image_note(path, reason: str) -> str:
    return f"Изображение «{Path(str(path)).name}» не отправлено: {reason}"


def split_images(images) -> tuple[list[tuple[str, str, bytes]], list[tuple[str, str]]]:
    """([(путь, MIME, байты)] годных, [(путь, причина)] отброшенных)."""
    good, dropped = [], []
    for i in images or ():
        if not i:
            continue
        try:
            media, data = check_image(i)
        except OSError as e:
            dropped.append((str(i), f"файл не читается ({type(e).__name__})"))
            continue
        except ValueError as e:
            dropped.append((str(i), str(e)))
            continue
        good.append((str(i), media, data))
    return good, dropped


def path_variants(path) -> list[str]:
    """Пути для правил запрета: как дан, настоящий (полный, с регистром с
    диска), и на Windows — ещё в нижнем регистре (пути там без учёта
    регистра, а правила сравниваются как строки)."""
    raw = os.path.abspath(str(path))
    try:
        real = os.path.realpath(raw)
    except (OSError, ValueError):
        real = raw
    out = []
    for p in (real, raw, *((real.lower(),) if sys.platform == "win32" else ())):
        p = p.rstrip("\\/") or p
        if p not in out:
            out.append(p)
    return out


def claude_glob_escape(text: str) -> str:
    """Буквальный путь → шаблон правила Claude Code без «джокеров».

    Повторяет собственную функцию claude 2.1.292 для этого (`UCe(путь,
    {escapeGlobs: true})` в его коде прав): обратная косая → `\\\\`; `[ ] ( )
    | + ^ $` и `*` — с обратной косой; `!`/`#` в начале и пробелы в конце —
    тоже. Правила Read сопоставляются библиотекой node-ignore (синтаксис
    .gitignore): `{ }` там не особые, `?` (один любой знак) CLI не
    экранирует — в именах Windows его не бывает, а на других системах
    неэкранированный `?` лишь расширяет запрет (безопасная сторона).
    Проверено на node-ignore 7.0.12 тем же путём, что у CLI (тест)."""
    t = text.replace("\\", "\\\\")
    t = re.sub(r"[\[\]()|+^$]", lambda m: "\\" + m.group(0), t)
    t = t.replace("*", "\\*")
    if t.startswith(("!", "#")):
        t = "\\" + t
    return re.sub(r"\s+$", lambda m: "".join("\\" + c for c in m.group(0)), t)


def claude_rule_text(content: str) -> str:
    """Содержимое правила → текст внутри `Read(…)`. CLI разбирает его
    обратно (`Nfo`): сначала `\\(`→`(`, `\\)`→`)`, затем `\\\\`→`\\` — поэтому
    обратные косые удваиваются, а скобки экранируются ещё раз."""
    return content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def claude_rule_path(path: str) -> str:
    """Абсолютный путь → вид правил Claude Code (содержимое после разбора):
    `D:\\KB\\x` → `//d/KB/x`, `/home/x` → `//home/x` (`//` — от корня
    файловой системы); знаки шаблона в именах папок экранированы
    (`claude_glob_escape`)."""
    m = re.match(r"^([A-Za-z]):[\\/]?(.*)$", path)
    if m:
        rest = m.group(2).replace("\\", "/").rstrip("/")
        return f"//{m.group(1).lower()}/{claude_glob_escape(rest)}".rstrip("/")
    rest = path.replace("\\", "/").strip("/")
    return "//" + claude_glob_escape(rest)


def claude_deny_rules(paths) -> list[str]:
    """Правила `Read(//…/**)` для `--disallowedTools`: запрет чтения папок.
    Только Read: claude 2.1.292 сопоставляет с путями лишь Read-правила, и
    они действуют на все читающие инструменты (Grep, Glob) — так пишет сам
    CLI («only Read(path) rules are … Read rules cover all file-reading
    tools»). Проверено без модели, что правило с пробелом и запятой в пути
    CLI разбирает целиком."""
    rules = []
    for p in paths or ():
        if not p:
            continue
        for variant in path_variants(p):
            rule = f"Read({claude_rule_text(claude_rule_path(variant) + '/**')})"
            if rule not in rules:
                rules.append(rule)
    return rules


Runner = Callable[..., Awaitable[AgentReply]]


# Метки чужого сеанса Claude Code / Codex, унаследованные процессом (резидент
# запустили из терминала агента или из его команды). Вызовы модели — свежие
# независимые сеансы, а не «вложенные» в чужой: метки связывают их с сеансом,
# который запустил резидент (канал сообщений, порт IDE, id). Сохранение сеанса
# вызовы Claude выключают сами и явно (`--no-session-persistence`, llm.claude),
# а не через метку. Тот же список, что у оболочки (`pty.rs`, SESSION_MARKERS):
# имена из строк claude.exe и codex.exe. Вход и настройки (ANTHROPIC_*,
# CLAUDE_CONFIG_DIR, CODEX_HOME, прокси) не трогаются.
SESSION_MARKERS = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_HOST_SESSION_ID",
    "CLAUDE_CODE_EVAL_INTERVIEW_SESSION",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "AI_AGENT",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
)


def kill_tree(proc) -> None:
    """Убить процесс CLI со всеми детьми: Codex и OpenCode запускают свои
    процессы, а у npm-сценария (.cmd) прямой ребёнок — cmd.exe."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10, creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def run_tree(cmd: list[str], *, timeout: float, **popen) -> tuple[int, bytes, bytes]:
    """Короткая служебная команда CLI: (код выхода, stdout, stderr). Не
    уложилась в `timeout` — дерево процессов убито и брошен
    `subprocess.TimeoutExpired` (`subprocess.run` убил бы только прямого
    ребёнка, а node и opencode.exe за сценарием .cmd остались бы жить)."""
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, creationflags=_NO_WINDOW, **popen)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            proc.communicate(timeout=10)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        raise
    return proc.returncode, out or b"", err or b""


def drop_session_markers(env: MutableMapping[str, str]) -> list[str]:
    """Убрать из `env` метки чужого сеанса (без учёта регистра); → что убрано."""
    wanted = {name.upper() for name in SESSION_MARKERS}
    gone = [key for key in list(env) if key.upper() in wanted]
    for key in gone:
        env.pop(key, None)
    return gone


def deny_prompt(paths) -> str:
    """Правило для промпта о закрытых папках (`kb_exclude`) — там, где CLI
    не запрещает чтение сам (Codex): просьба, не запрет."""
    shown = [os.path.abspath(str(p)) for p in paths or () if p]
    if not shown:
        return ""
    return ("Эти папки закрыты для тебя: не открывай, не читай и не ищи в них файлы, "
            "даже если попросят: " + "; ".join(shown))
