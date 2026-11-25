"""Выгрузка встречи в базу знаний (Obsidian и т. п.) по шаблону папки.

Внутри `export.meetings_dir` на встречу заводится папка по шаблону
(`{date} - {title}` → «2026-09-30 - Планирование спринта»; «/» в шаблоне —
подпапки), и в неё кладутся выбранные файлы: транскрипт в Markdown, итоги
(summary.md как есть), субтитры, запись.

Правила, ради которых модуль устроен так, а не проще:

* **Повторная выгрузка идёт туда же — на место по шаблону.** Путь выгрузки
  хранится в meta.json записи (`kb_export.path`); итоги, сделанные позже, не
  плодят вторую папку. Если прежняя папка не на месте по текущему шаблону
  (шаблон сменили или встречу переименовали), выгрузка сначала переносит её
  туда целиком, `os.rename`, вместе с заметками человека (`_place`). Место
  занято — выгрузка идёт на место по шаблону, а прежняя папка остаётся как
  была (строка «Прежняя папка оставлена» в ответе). Папку вне папки для
  встреч человек перенёс сам — её не трогаем. Все выгруженные встречи разом —
  «Переложить по шаблону» в настройках (`relocate_all`).
* **Чужое и правленое не трогаем.** Перезаписываются только наши файлы, и
  только пока их не меняли: в meta.json (`kb_export.files`) лежит SHA-256
  каждого записанного файла. Файл правили в базе знаний (хеш другой) или он
  лежал в папке до нас (хеша нет) — он остаётся как есть, а выгрузка сообщает
  о нём (`kept`). Копий «(обновлено)» не плодим: правка человека важнее
  свежей версии, а новую всегда можно получить, удалив файл. Заметки рядом и
  уже существующая папка с тем же именем остаются как были. Папка, занятая
  другой встречей, получает суффикс « (2)».
* **Переименовали встречу — папка следует за названием** сразу
  (`follow_title`), если она своя: в ней ровно наши файлы прошлой выгрузки, а
  папки с новым именем ещё нет. Иначе её перенесёт следующая выгрузка (выше).
* **Запись атомарная:** временный файл рядом и `os.replace` — база знаний
  (синхронизация, индексатор Obsidian) не увидит половину файла.
"""

import hashlib
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from meet import library, plat

TOKENS = ("date", "time", "year", "month", "day", "title")
TOKEN_RE = re.compile(r"\{([^{}]*)\}")
NOT_SET = "Папка для встреч не задана"
NO_TRANSCRIPT = "Транскрипта нет — сначала расшифруйте запись"
SRT_NAME = "Субтитры.srt"
AUDIO_STEM = "Запись"
# Две дорожки (собеседники и микрофон) сводятся в одну: так её можно слушать.
MIXED_EXT = ".opus"
FALLBACK_TITLE = "Встреча"
# Пример для предпросмотра шаблона, когда в библиотеке ещё нет записей.
SAMPLE_TITLE = "Планирование спринта"
SAMPLE_START = datetime(2026, 9, 30, 10, 0)


# --- шаблоны ---------------------------------------------------------------------


def _token_error(text: str) -> str | None:
    for name in TOKEN_RE.findall(text):
        if name not in TOKENS:
            known = ", ".join("{" + t + "}" for t in TOKENS)
            return f"Неизвестная подстановка {{{name}}}. Доступны: {known}"
    return None


def check_folder_template(template: str) -> str | None:
    """Почему шаблон папки нельзя использовать, или None. Шаблон — путь
    внутри папки для встреч: относительный, без «..» и пустых частей."""
    text = (template or "").strip()
    if not text:
        return "Шаблон папки не может быть пустым"
    if text[0] in "/\\":
        return "Шаблон папки должен быть относительным — без «/» в начале"
    if re.match(r"^[A-Za-z]:", text):
        return "Шаблон папки не может содержать букву диска"
    parts = re.split(r"[\\/]", text)
    if any(not part.strip() for part in parts):
        return "В шаблоне папки есть пустая часть пути — уберите лишнюю «/»"
    if any(part.strip() in ("..", ".") for part in parts):
        return "В шаблоне папки нельзя использовать «..» и «.»"
    return _token_error(text)


def check_file_name(name: str) -> str | None:
    """Почему имя файла нельзя использовать, или None."""
    text = (name or "").strip()
    if not text:
        return "Имя файла не может быть пустым"
    if "/" in text or "\\" in text:
        return "Имя файла не может содержать «/» или «\\»"
    return _token_error(text)


def _md_name(name: str) -> str:
    text = name.strip()
    return (text if text.lower().endswith(".md") else f"{text}.md").lower()


def check_names(transcript_name: str, summary_name: str) -> str | None:
    """Имена файлов расшифровки и итогов не должны совпадать друг с другом и
    с файлами субтитров и записи — иначе один файл затёр бы другой."""
    names = {"расшифровки": _md_name(transcript_name), "итогов": _md_name(summary_name)}
    if names["расшифровки"] == names["итогов"]:
        return "Имена файлов расшифровки и итогов совпадают — задайте разные"
    srt_stem = SRT_NAME.rsplit(".", 1)[0].lower()
    for which, name in names.items():
        stem = name[:-3]
        if stem == AUDIO_STEM.lower():
            return f"Имя файла {which} совпадает с именем записи «{AUDIO_STEM}» — выберите другое"
        if stem in (srt_stem, SRT_NAME.lower()):
            return f"Имя файла {which} совпадает с именем субтитров «{SRT_NAME}» — выберите другое"
    return None


def _values(title: str, start: datetime) -> dict:
    return {
        "date": start.strftime("%Y-%m-%d"),
        "time": start.strftime("%H-%M"),
        "year": start.strftime("%Y"),
        "month": start.strftime("%m"),
        "day": start.strftime("%d"),
        "title": title,
    }


def _fill(text: str, values: dict) -> str:
    return TOKEN_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def render_folder(template: str, title: str, start: datetime) -> list[str]:
    """Части пути папки встречи. Подстановки — после разбиения на части:
    «/» в названии встречи не создаёт подпапок, а каждая часть очищается
    `export.safe_filename`."""
    from meet.export import safe_filename

    values = _values(title, start)
    parts = [p.strip() for p in re.split(r"[\\/]", template.strip())]
    return [safe_filename(_fill(part, values), FALLBACK_TITLE) for part in parts]


def render_file(template: str, title: str, start: datetime, default: str = "Файл.md") -> str:
    """Имя файла по шаблону: подстановки, очистка, «.md», если его нет."""
    from meet.export import safe_filename

    name = safe_filename(_fill(template.strip(), _values(title, start)), default)
    return name if name.lower().endswith(".md") else f"{name}.md"


# --- встреча -----------------------------------------------------------------------


def meeting(folder: Path) -> tuple[str, datetime]:
    """Название и начало встречи: название из карточки (meta.json), иначе
    «Встреча ЧЧ:ММ»; начало — из имени папки записи, иначе время папки."""
    folder = Path(folder)
    card = library.describe(folder)
    start = None
    if card and card.started_at:
        try:
            start = datetime.fromisoformat(card.started_at)
        except ValueError:
            start = None
    if start is None:
        start = datetime.fromtimestamp(folder.stat().st_mtime)
    title = str(library.read_meta(folder).get("title") or "").strip()
    return title or f"{FALLBACK_TITLE} {start:%H:%M}", start


def _audio_sources(folder: Path) -> list[Path]:
    tracks = {stem: library.find_track(folder, stem) for stem in library.TRACK_STEMS}
    if tracks["source"]:
        return [tracks["source"]]
    return [p for p in (tracks["sys"], tracks["mic"]) if p]


def _audio_name(sources: list[Path]) -> str | None:
    if not sources:
        return None
    ext = MIXED_EXT if len(sources) > 1 else sources[0].suffix.lower()
    return f"{AUDIO_STEM}{ext}"


def _plan(cfg, title: str, start: datetime, has_summary: bool,
          audio: str | None) -> list[tuple[str, str]]:
    """(что, имя файла) в порядке выгрузки."""
    files = []
    if cfg.include_transcript:
        files.append(("transcript", render_file(cfg.transcript_name, title, start, "Транскрипт.md")))
    if cfg.include_summary and has_summary:
        files.append(("summary", render_file(cfg.summary_name, title, start, "Итоги.md")))
    if cfg.include_srt:
        files.append(("srt", SRT_NAME))
    if cfg.include_audio and audio:
        files.append(("audio", audio))
    return files


# --- запись файлов -----------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str | None:
    try:
        return _sha(path.read_bytes())
    except OSError:
        return None


def _write_bytes(path: Path, data: bytes) -> None:
    """Байты как есть (переводы строк — LF): хеш в meta.json — от того, что на
    диске, а текстовый режим Windows подменял бы их на CRLF."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _recorded(record: dict) -> dict:
    """Наши файлы прошлой выгрузки: {имя: sha256 или None}. None — наш, но
    без хеша (аудио; первая версия выгрузки писала только список имён)."""
    files = record.get("files")
    if isinstance(files, dict):
        return {str(k): v if isinstance(v, str) and v else None for k, v in files.items()}
    if isinstance(files, list):
        return {str(name): None for name in files}
    return {}


def _ours(path: Path, name: str, recorded: dict) -> bool:
    """Файл можно перезаписать: его нет, или он наш и с тех пор не менялся."""
    if not path.exists():
        return True
    if name not in recorded:
        return False  # лежал в папке до нас
    sha = recorded[name]
    return sha is None or _file_sha(path) == sha


def _fresh(target: Path, sources: list[Path]) -> bool:
    """Аудио уже выгружено и новее дорожек — гигабайт заново не копируем."""
    try:
        made = target.stat()
        return made.st_size > 0 and all(made.st_mtime >= s.stat().st_mtime for s in sources)
    except OSError:
        return False


def _write_audio(folder: Path, sources: list[Path], target: Path, mix) -> None:
    """Запись встречи в базу знаний. Две дорожки — то же сведение, что слышит
    плеер карточки (`playback.opus`, микрофон выровнен по громкости): кэш
    сведения общий, второй раз ffmpeg не запускается. `mix` — подмена для тестов."""
    from meet import playback

    if _fresh(target, sources):
        return
    tmp = target.with_name(target.name + ".tmp")
    try:
        # Файлы записи открыты, пока копируем: «Удалить» подождёт (как плеер).
        with playback.using(folder):
            if len(sources) > 1 and mix is not None:
                mix(sources[0], sources[1], tmp)
            elif len(sources) > 1:
                mixed = playback.playback_path(folder)
                if mixed is None:
                    raise RuntimeError("дорожек записи нет")
                shutil.copyfile(mixed, tmp)
            else:
                shutil.copyfile(sources[0], tmp)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _key(path: Path) -> str | None:
    try:
        return plat.path_key(path.resolve())
    except OSError:
        return None


def _same(a: Path, b: Path) -> bool:
    key = _key(a)
    return key is not None and key == _key(b)


def _claims(lib_root: Path) -> dict[str, set[str]]:
    """Папки базы знаний, которые заняли встречи библиотеки (их
    `kb_export.path`): ключ пути → имена папок записей. Считается один раз на
    выгрузку или на весь перенос, а не на каждое сравнение."""
    claims: dict[str, set[str]] = {}
    try:
        entries = [p for p in Path(lib_root).iterdir() if p.is_dir()]
    except OSError:
        return claims
    for rec in entries:
        path = (library.read_meta(rec).get("kb_export") or {}).get("path")
        key = _key(Path(path)) if isinstance(path, str) and path else None
        if key:
            claims.setdefault(key, set()).add(rec.name)
    return claims


def _claimed_by_other(candidate: Path, folder: Path, claims: dict | None = None) -> bool:
    """Папку уже заняла другая встреча библиотеки (её kb_export.path)."""
    if claims is None:
        claims = _claims(folder.parent)
    return bool(claims.get(_key(candidate) or "", set()) - {folder.name})


def _holds_other(old: Path, folder: Path, claims: dict) -> bool:
    """Внутри папки лежит папка другой встречи (шаблон с подпапками по
    названию): такую папку целиком не переносим."""
    key = _key(old)
    if key is None:
        return True
    prefix = key.rstrip("\\/") + os.sep
    return any(k.startswith(prefix) and names - {folder.name} for k, names in claims.items())


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _previous(record, root: Path) -> Path | None:
    """Папка прошлой выгрузки — если она есть и лежит внутри папки для встреч
    (но не она сама). Вне её — встречу человек перенёс сам (например, личную
    в другое хранилище): её не трогаем, выгрузка идёт туда (`_moved_out`)."""
    path = record.get("path") if isinstance(record, dict) else None
    if not isinstance(path, str) or not path:
        return None
    prev = Path(path)
    if prev.is_dir() and _inside(prev, root) and not _same(prev, root):
        return prev
    return None


def _moved_out(record, root: Path) -> Path | None:
    """Папка прошлой выгрузки, которую человек перенёс за пределы папки для
    встреч (есть на диске, вне неё); иначе None."""
    path = record.get("path") if isinstance(record, dict) else None
    if not isinstance(path, str) or not path:
        return None
    prev = Path(path)
    if prev.is_dir() and not _inside(prev, root) and not _inside(root, prev):
        return prev
    return None


def _by_template(root: Path, cfg, folder: Path, title: str, start: datetime,
                 claims: dict | None = None) -> Path:
    """Место встречи по шаблону; занятое другой встречей — с суффиксом « (2)»."""
    parts = render_folder(cfg.folder_template, title, start)
    base = root.joinpath(*parts)
    if not _inside(base, root):  # ссылка (junction) наружу
        raise ValueError("Шаблон папки выводит за пределы папки для встреч")
    if claims is None:
        claims = _claims(folder.parent)
    candidate, n = base, 2
    while _claimed_by_other(candidate, folder, claims):
        candidate = base.with_name(f"{base.name} ({n})")
        n += 1
    return candidate


# --- перенос папки встречи (шаблон или название изменились) ---------------------------

MOVED = "moved"
# Почему папка осталась на прежнем месте — для человека (счётчики в ответе
# «Переложить по шаблону», без названий встреч).
TAKEN = "место по шаблону уже занято"
NESTED = "папки встреч вложены одна в другую"
FAILED = "папку не удалось переименовать — возможно, она открыта в другой программе"
MOVED_NOTE = "Папка перенесена по шаблону, прежняя: {}"
LEFT_NOTE = "Прежняя папка оставлена: {}"
NOT_MOVED_NOTE = "Папку не удалось перенести по шаблону — выгрузка в прежнюю: {}"


def _kinds(record: dict, recorded: dict) -> dict[str, str]:
    """Какой из наших файлов чей: {вид: имя}. Прежние выгрузки этого не
    помнили — тогда субтитры и запись узнаются по имени, а тексты — по порядку
    записи (расшифровка всегда раньше итогов)."""
    kinds = record.get("kinds") if isinstance(record, dict) else None
    if isinstance(kinds, dict):
        return {str(k): v for k, v in kinds.items() if isinstance(v, str) and v in recorded}
    guess: dict[str, str] = {}
    texts = []
    for name in recorded:
        if name == SRT_NAME:
            guess["srt"] = name
        elif Path(name).stem == AUDIO_STEM:
            guess["audio"] = name
        elif name.lower().endswith(".md"):
            texts.append(name)
    if len(texts) == 2:
        guess["transcript"], guess["summary"] = texts
    elif len(texts) == 1:
        guess["transcript"] = texts[0]
    return guess


def _drop_empty(path: Path, root: Path) -> None:
    """Опустевшие папки прежнего шаблона (год, месяц) — убрать, вверх до папки
    для встреч, её саму не трогая. `rmdir` удаляет только пустую папку."""
    while path != path.parent and _inside(path, root) and not _same(path, root):
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


def _move_folder(folder: Path, root: Path, record: dict, old: Path, new: Path,
                 renames: list[tuple[str, str, str]]) -> bool:
    """Папку встречи old → new одним `os.rename` (атомарно; заметки человека
    едут вместе с ней). Родительские папки шаблона создаются, при отказе —
    убираются. Затем наши файлы с `{title}` в имени: `renames` — (вид,
    прежнее имя, новое); переименовывается только наш файл и только если
    нового ещё нет. Затем — путь в meta.json. Не переименовалось — False, всё
    на месте; meta.json не записался — всё назад и исключение (иначе следующая
    выгрузка завела бы вторую папку)."""
    recorded = _recorded(record)
    kinds = _kinds(record, recorded)
    created, parent = [], new.parent
    while not parent.exists() and parent != parent.parent:
        created.append(parent)
        parent = parent.parent

    def drop_created() -> None:
        for path in created:  # от глубокой к верхней
            try:
                path.rmdir()
            except OSError:
                break

    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        os.rename(old, new)
    except OSError:
        drop_created()
        return False
    files = dict(recorded)
    moved: list[tuple[str, str]] = []
    for kind, was, name in renames:
        if was == name or was not in files or (new / name).exists():
            continue
        try:
            os.rename(new / was, new / name)
        except OSError:
            continue
        files[name] = files.pop(was)
        kinds[kind] = name
        moved.append((was, name))

    def change(meta: dict) -> dict:
        current = meta.get("kb_export")
        current = dict(current) if isinstance(current, dict) else {}
        current.update(path=str(new), files=files, kinds=kinds)
        return {**meta, "kb_export": current}

    try:
        library.update_meta(folder, change)
    except Exception:
        for was, name in reversed(moved):
            try:
                os.rename(new / name, new / was)
            except OSError:
                pass
        try:
            os.rename(new, old)
        except OSError:
            pass
        else:
            drop_created()
        raise
    _drop_empty(old.parent, root)
    return True


def _relocate(folder: Path, root: Path, cfg, record: dict, old: Path, new: Path,
              title: str, start: datetime, claims: dict) -> str:
    """Перенести папку встречи на место по шаблону: MOVED или почему нет
    (TAKEN, NESTED, FAILED). Чужое не трогается: занятое место — не наше."""
    if new.exists():
        return TAKEN
    if _inside(new, old) or _holds_other(old, folder, claims):
        return NESTED
    now = dict(_plan(cfg, title, start, True, _audio_name(_audio_sources(folder))))
    kinds = _kinds(record, _recorded(record))
    renames = [(kind, was, now[kind]) for kind, was in kinds.items() if kind in now]
    return MOVED if _move_folder(folder, root, record, old, new, renames) else FAILED


def _place(root: Path, cfg, folder: Path, title: str, start: datetime) -> tuple[Path, list[str], bool]:
    """Куда выгружать: (папка, строки для человека, учёт наших файлов —
    заново). Прежняя папка не на месте по шаблону (шаблон или название
    сменились) — сначала переезжает туда целиком; место занято — выгрузка
    идёт на место по шаблону, а прежняя папка остаётся как есть."""
    record = library.read_meta(folder).get("kb_export")
    record = record if isinstance(record, dict) else {}
    moved_out = _moved_out(record, root)
    if moved_out is not None:
        # Человек сам перенёс папку встречи за пределы папки для встреч (личную —
        # в другое хранилище) и поправил kb_export.path: выгрузка — туда, без
        # переезда по шаблону и без второй копии в папке для встреч.
        return moved_out, [], False
    old = _previous(record, root)
    # Обычный случай — папка на месте по шаблону: meta.json всей библиотеки
    # (кто какое место занял) читать незачем.
    if old is not None and _same(old, _by_template(root, cfg, folder, title, start, {})):
        return old, [], False
    claims = _claims(folder.parent)
    new = _by_template(root, cfg, folder, title, start, claims)
    if old is None or _same(old, new):
        return old or new, [], False
    outcome = _relocate(folder, root, cfg, record, old, new, title, start, claims)
    if outcome == MOVED:
        return new, [MOVED_NOTE.format(old)], False
    if outcome == FAILED:
        return old, [NOT_MOVED_NOTE.format(old)], False
    # В новом месте файлы с нашими именами — не наши: учёт с нуля.
    return new, [LEFT_NOTE.format(old)], True


def _transcript_md(data: dict, title: str, start: datetime, folder: Path | None = None,
                   category: str | None = None) -> str:
    """Транскрипт в формате проекта (тот же, что у экспорта .md и бывшей
    заметки): frontmatter для Obsidian, «# название», «Содержание» (главы
    анализа встречи, если он есть), «## ВРЕМЯ — Спикер». `category` — имя
    категории встречи для frontmatter."""
    from meet import export, output

    return output.to_markdown(title, export.md_segments(data), start.strftime("%Y-%m-%d"),
                              contents=export.chapters_of(folder, data), category=category)


OLD_NOTE = "Старая заметка оставлена: {}"


def _old_note(root: Path, folder: Path, data: dict, title: str, start: datetime) -> str | None:
    """Заметка прежней версии (0.1.0, «В заметки»): один файл
    `<ГГГГ-ММ-ДД> <название>.md` в папке заметок — после переноса настроек это
    папка для встреч. Название тогда бралось из карточки или транскрипта."""
    from meet.export import safe_filename

    titles = {title, library.title_and_date(folder, data)[0]}
    for name in sorted(f"{start:%Y-%m-%d} {safe_filename(t, folder.name)}.md" for t in titles if t):
        if (root / name).is_file():
            return name
    return None


def _category_name(folder: Path, full) -> str | None:
    """Имя категории встречи (нет или удалена из настроек — None). Даны только
    настройки выгрузки — список категорий берём из файла настроек."""
    from meet import categories, settings

    try:
        return categories.display_name(folder, full if hasattr(full, "categories") else settings.load())
    except Exception:
        return None  # категория — украшение заметки, не повод срывать выгрузку


def _export(folder: Path, cfg, mix, category: str | None = None) -> dict:
    from meet import assistant, export

    if not cfg.meetings_dir:
        raise ValueError(NOT_SET)
    root = Path(cfg.meetings_dir)
    if not root.is_dir() and root.parent.is_dir() and root.parent != root:
        # Последняя часть пути ещё не создана (перенесённая «подпапка
        # заметок»); отключённый диск или шару не создаём — это ошибка.
        root.mkdir(exist_ok=True)
    if not root.is_dir():
        raise ValueError(f"Папка для встреч не найдена: {root}")
    data = library.with_display_names(library.read_transcript(folder))
    if data is None:
        raise ValueError(NO_TRANSCRIPT)
    if library.is_text_phase(data):
        raise ValueError(library.TEXT_ONLY)
    title, start = meeting(folder)
    summary_path = folder / assistant.SUMMARY_MD
    sources = _audio_sources(folder)
    plan = _plan(cfg, title, start, summary_path.is_file(), _audio_name(sources))
    target, notes, fresh = _place(root, cfg, folder, title, start)
    target.mkdir(parents=True, exist_ok=True)
    previous = library.read_meta(folder).get("kb_export")
    previous = previous if isinstance(previous, dict) else {}
    recorded = {} if fresh else _recorded(previous)
    kinds = {} if fresh else _kinds(previous, recorded)
    if not previous.get("path"):
        # Первая выгрузка встречи, у которой есть заметка прежней версии: новая
        # папка — рядом, а заметку не трогаем (её могли дописать) и говорим о ней.
        old_note = _old_note(root, folder, data, title, start)
        if old_note:
            notes.append(OLD_NOTE.format(old_note))
    written, kept = [], []
    for kind, name in plan:
        path = target / name
        if not _ours(path, name, recorded):
            kept.append(name)
            continue
        if kind == "audio":
            _write_audio(folder, sources, path, mix)
            recorded[name] = None  # гигабайты не хешируем: аудио не правят
        else:
            if kind == "transcript":
                text = _transcript_md(data, title, start, folder, category)
            elif kind == "summary":
                text = summary_path.read_text(encoding="utf-8")
            else:
                text = export.render(data, "srt")
            content = text.encode("utf-8")
            _write_bytes(path, content)
            recorded[name] = _sha(content)
        written.append(name)
    # `kinds` — какой файл чей: при переносе папки файлы с {title} в имени
    # переименовываются вслед за названием.
    library.write_meta(folder, {"kb_export": {"path": str(target), "at": time.time(),
                                              "files": recorded, "kept": kept,
                                              "kinds": {**kinds, **dict(plan)}}})
    return {"path": str(target), "files": written, "kept": kept, "notes": notes}


def export_recording(folder, cfg, *, mix=None) -> dict:
    """Выгрузить встречу: `{"path": папка, "files": [записанные], "kept":
    [не перезаписанные — изменены вручную или лежали в папке до нас], "notes":
    [строки для человека — например, о заметке прежней версии]}`.

    `cfg` — секция `export` настроек (или настройки целиком). Ошибка —
    исключение; кроме «папка не задана», она запоминается в meta.json
    (`kb_export.error`), чтобы карточка показала её, а удачная выгрузка её
    стирает."""
    full, cfg = cfg, getattr(cfg, "export", cfg)
    folder = Path(folder)
    try:
        return _export(folder, cfg, mix, _category_name(folder, full))
    except Exception as e:
        if str(e) != NOT_SET:
            _remember_error(folder, e)
        raise


def _remember_error(folder: Path, error: Exception) -> None:
    def change(meta: dict) -> dict:
        previous = meta.get("kb_export")
        record = dict(previous) if isinstance(previous, dict) else {}
        record["error"] = str(error) or type(error).__name__
        record["error_at"] = time.time()
        return {**meta, "kb_export": record}

    try:
        library.update_meta(folder, change)
    except Exception:
        pass  # папку записи не записать — ошибку всё равно увидит вызывающий


def previously_exported(folder: Path) -> bool:
    record = library.read_meta(Path(folder)).get("kb_export")
    return isinstance(record, dict) and bool(record.get("path"))


# --- переименование встречи ---------------------------------------------------------


def follow_title(folder, cfg, old_title: str) -> Path | None:
    """Встречу переименовали (название уже в meta.json): переименовать и её
    папку в базе знаний по шаблону — атомарно, `os.rename`. Только если папка
    наша целиком (есть все наши файлы прошлой выгрузки и ничего чужого) и
    папки с новым именем нет; имена наших файлов с `{title}` в шаблоне тоже
    следуют за названием. Новый путь — или None, если папка осталась прежней
    (не выгружалась, чужие файлы, имя занято, ошибка диска)."""
    cfg = getattr(cfg, "export", cfg)
    folder = Path(folder)
    if not cfg.meetings_dir:
        return None
    root = Path(cfg.meetings_dir)
    record = library.read_meta(folder).get("kb_export")
    if not isinstance(record, dict) or not isinstance(record.get("path"), str) or not record["path"]:
        return None
    old = Path(record["path"])
    recorded = _recorded(record)
    try:
        if not old.is_dir() or not _inside(old, root) or not recorded:
            return None
        if {p.name for p in old.iterdir()} != set(recorded):
            return None  # чужие файлы или наших не хватает: папка уже не только наша
    except OSError:
        return None
    title, start = meeting(folder)
    new = root.joinpath(*render_folder(cfg.folder_template, title, start))
    if (_same(new, old) or new.exists() or not _inside(new, root)
            or _claimed_by_other(new, folder)):
        return None
    # Файлы с {title} в имени: та же раскладка по старому и новому названию.
    audio = _audio_name(_audio_sources(folder))
    before = dict(_plan(cfg, old_title, start, True, audio))
    renames = [(kind, before[kind], name) for kind, name in _plan(cfg, title, start, True, audio)
               if kind in before]
    return new if _move_folder(folder, root, record, old, new, renames) else None


# --- «Переложить по шаблону» (настройки) ---------------------------------------------


def _meetings_root(cfg) -> Path:
    if not cfg.meetings_dir:
        raise ValueError(NOT_SET)
    root = Path(cfg.meetings_dir)
    if not root.is_dir():
        raise ValueError(f"Папка для встреч не найдена: {root}")
    return root


def _off_template(lib_root: Path, root: Path, cfg, claims: dict):
    """Выгруженные встречи, чьи папки внутри папки для встреч, но не на месте
    по шаблону: (запись, kb_export, папка, место по шаблону, название, начало).
    Место считается в момент обхода — по `claims`, которые обход обновляет."""
    for rec in library.recording_folders(lib_root):
        record = library.read_meta(rec).get("kb_export")
        old = _previous(record, root)
        if old is None:
            continue
        try:
            title, start = meeting(rec)
            new = _by_template(root, cfg, rec, title, start, claims)
        except (OSError, ValueError):
            continue
        if not _same(old, new):
            yield rec, record, old, new, title, start


def relocation(lib_root, cfg) -> dict:
    """Сколько выгруженных встреч лежит не по сохранённому шаблону —
    `{"count": N}` для строки над кнопкой «Переложить по шаблону». Папки вне
    папки для встреч (перенесённые человеком) не считаются."""
    cfg = getattr(cfg, "export", cfg)
    try:
        root = _meetings_root(cfg)
    except ValueError:
        return {"count": 0}
    lib_root = Path(lib_root)
    return {"count": sum(1 for _ in _off_template(lib_root, root, cfg, _claims(lib_root)))}


def relocate_all(lib_root, cfg, *, on_moved=None) -> dict:
    """«Переложить по шаблону»: папки выгруженных встреч библиотеки, которые
    лежат внутри папки для встреч не на месте по шаблону, переезжают туда
    целиком (как при выгрузке), meta.json правится. Ответ — счётчики без
    названий встреч: `{"moved": N, "left": M, "reasons": [{"reason", "count"}]}`.
    `on_moved(папка записи)` — после каждого переноса (окну — обновить карточку)."""
    cfg = getattr(cfg, "export", cfg)
    root = _meetings_root(cfg)
    lib_root = Path(lib_root)
    claims = _claims(lib_root)
    moved, left = 0, {}
    for rec, record, old, new, title, start in _off_template(lib_root, root, cfg, claims):
        try:
            outcome = _relocate(rec, root, cfg, record, old, new, title, start, claims)
        except Exception:
            outcome = FAILED  # meta.json не записался — папка возвращена на место
        if outcome != MOVED:
            left[outcome] = left.get(outcome, 0) + 1
            continue
        moved += 1
        # Место занято этой встречей — следующая с тем же названием получит « (2)».
        for names in claims.values():
            names.discard(rec.name)
        claims.setdefault(_key(new) or str(new), set()).add(rec.name)
        if on_moved is not None:
            on_moved(rec)
    return {"moved": moved, "left": sum(left.values()),
            "reasons": [{"reason": reason, "count": count} for reason, count in left.items()]}


# --- предпросмотр ------------------------------------------------------------------


_FLAGS = ("include_transcript", "include_summary", "include_audio", "include_srt")
_TEXTS = ("folder_template", "transcript_name", "summary_name")


def _sample(root: Path) -> tuple[str, datetime, str | None]:
    """Последняя запись библиотеки, иначе выдуманный пример."""
    try:
        card = library.latest(root) if root.is_dir() else None
    except OSError:
        card = None
    if card is not None:
        title, start = meeting(card.path)
        return title, start, _audio_name(_audio_sources(card.path))
    return SAMPLE_TITLE, SAMPLE_START, f"{AUDIO_STEM}{MIXED_EXT}"


def preview(cfg, root: Path, overrides: dict) -> dict:
    """Как будет называться папка и что в ней ляжет — для окна настроек.
    `overrides` — несохранённые значения из окна (строки, как в query)."""
    from meet.settings import as_flag

    cfg = getattr(cfg, "export", cfg)
    values = {key: getattr(cfg, key) for key in _TEXTS + _FLAGS}
    for key in _TEXTS:
        if isinstance(overrides.get(key), str):
            values[key] = overrides[key]
    for key in _FLAGS:
        if key in overrides:
            values[key] = as_flag(overrides[key], values[key])
    error = check_folder_template(values["folder_template"])
    for key in ("transcript_name", "summary_name"):
        error = error or check_file_name(values[key])
    error = error or check_names(values["transcript_name"], values["summary_name"])
    if error:
        return {"folder": None, "files": [], "error": error}
    title, start, audio = _sample(Path(root))
    files = [name for _, name in _plan(SimpleNamespace(**values), title, start, True, audio)]
    folder = "/".join(render_folder(values["folder_template"], title, start))
    return {"folder": folder, "files": files, "error": None}
