"""Библиотека записей: что лежит на диске и в каком оно состоянии.

Источник истины — файлы, а не индекс: папка записи самодостаточна, и её можно
скопировать с другой машины (сценарий «записал на ноутбуке — расшифровал на
десктопе» из README). Поэтому здесь нет базы, только чтение каталога; кэш, если
понадобится, всегда можно восстановить перечитыванием.

Структурный транскрипт (`transcript.json`) — источник для редактора; Markdown
остаётся человеческим артефактом и форматом экспорта.
"""

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

TRANSCRIPT_JSON = "transcript.json"
META_JSON = "meta.json"
FOLDER_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_.+)?$")
# Форматы дорожек в порядке предпочтения — те же, что понимает transcribe.
TRACK_EXTS = (".opus", ".wav", ".ogg", ".flac", ".mp3", ".m4a",
              ".mp4", ".webm", ".mkv")
# Дорожки папки записи: запись пишет sys+mic, импорт кладёт один source.
TRACK_STEMS = ("sys", "mic", "source")
# Что принимаем на импорт: всё это декодирует ffmpeg.
IMPORT_EXTS = (".mp3", ".mp4", ".m4a", ".wav", ".ogg", ".opus", ".webm", ".mkv",
               ".flac")
SOURCES = ("record", "auto", "import", "live", "merge")


@dataclass
class Recording:
    """Одна папка записи. `id` — имя папки: оно же дата и время встречи."""

    id: str
    path: Path
    started_at: str | None = None
    tracks: dict = field(default_factory=dict)
    duration_s: float | None = None
    has_transcript: bool = False
    has_voices: bool = False
    title: str | None = None
    source: str = "record"
    # mtime транскрипта: окно сравнивает его с концом упавшей перерасшифровки.
    transcript_at: float | None = None
    # Пометка транскрипта о диаризации: "skipped_no_token" — расшифровано без
    # разделения на спикеров (не было токена Hugging Face). None — как обычно.
    diarization: str | None = None
    # Выгрузка в базу знаний (meta.json): {"path", "at", "files"} и, если
    # последняя не удалась, "error". Не выгружалась — None.
    kb_export: dict | None = None
    # Объединённая встреча (meet.merge): {"parts", "state", "deleted",
    # "kb_left"} — из скольких записей, удалены ли исходные и какие их папки в
    # базе знаний остались нетронутыми. Не объединённая — None.
    merge: dict | None = None

    def to_raw(self) -> dict:
        return {
            "id": self.id,
            "path": str(self.path),
            "started_at": self.started_at,
            "tracks": {name: str(p) for name, p in self.tracks.items()},
            "duration_s": self.duration_s,
            "has_transcript": self.has_transcript,
            "has_voices": self.has_voices,
            "title": self.title,
            "source": self.source,
            "transcript_at": self.transcript_at,
            "diarization": self.diarization,
            "kb_export": self.kb_export,
            "merge": self.merge,
        }


def find_track(folder: Path, stem: str) -> Path | None:
    for ext in TRACK_EXTS:
        candidate = folder / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def _started_at(name: str) -> str | None:
    """Время начала из имени папки (recorder именует их YYYY-MM-DD_HH-MM)."""
    m = FOLDER_RE.match(name)
    if not m:
        return None
    y, mo, d, h, mi = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:00"


def _duration_from_events(folder: Path) -> float | None:
    """Длительность из `events.jsonl` — её пишет сама запись при остановке.

    Файла нет (старая запись, убитый процесс) — не выдумываем: пусть будет None,
    а не число, которому нельзя верить."""
    path = folder / "events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("kind") == "record.stopped":
            value = event.get("duration_s")
            return float(value) if isinstance(value, (int, float)) else None
    return None


def transcript_path(folder: Path) -> Path:
    return folder / TRANSCRIPT_JSON


def read_transcript(folder: Path) -> dict | None:
    """Структурный транскрипт папки; нет или битый — None."""
    try:
        data = json.loads(transcript_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def display_names(segments: list[dict]) -> dict[str, str]:
    """Сырая метка SPEAKER_XX → «Спикер N» в порядке первого появления.

    Нумерация та же, что в output.speaker_names; прочие метки (имена, «Вы») не трогаем."""
    names: dict[str, str] = {}
    for seg in segments:
        label = seg.get("speaker")
        if isinstance(label, str) and label.startswith("SPEAKER_") and label not in names:
            names[label] = f"Спикер {len(names) + 1}"
    return names


def with_display_names(data: dict | None) -> dict | None:
    """Копия транскрипта, где сырые метки заменены отображаемыми именами."""
    if not data or not isinstance(data.get("segments"), list):
        return data
    names = display_names(data["segments"])
    if not names:
        return data
    segments = [{**seg, "speaker": names.get(seg.get("speaker"), seg.get("speaker"))}
                for seg in data["segments"]]
    return {**data, "segments": segments}


def write_transcript(folder: Path, data: dict) -> Path:
    """Атомарная запись: редактор читает файл в любой момент."""
    path = transcript_path(folder)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    import os

    os.replace(tmp, path)
    return path


def read_meta(folder: Path) -> dict:
    """Метаданные папки записи. Нет файла или он битый — пустой словарь: у
    записей, сделанных до v1, meta.json нет, и это норма."""
    try:
        data = json.loads((folder / META_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# meta.json пишут несколько потоков резидента (выгрузка в базу знаний в фоне,
# переименование из окна, пометка источника записи): чтение-правка-запись —
# под замком папки, иначе одна правка теряет другую.
_META_LOCKS: dict[str, threading.Lock] = {}
_META_LOCKS_GUARD = threading.Lock()
REPLACE_TRIES = 5


def _meta_lock(folder: Path) -> threading.Lock:
    try:
        key = os.path.normcase(str(Path(folder).resolve()))
    except OSError:
        key = os.path.normcase(str(folder))
    with _META_LOCKS_GUARD:
        return _META_LOCKS.setdefault(key, threading.Lock())


def _replace(tmp: Path, target: Path) -> None:
    """os.replace с повтором: на Windows файл, который как раз читает другой
    процесс (окно, задача), заменить нельзя — PermissionError на миг."""
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def update_meta(folder: Path, change) -> dict:
    """Атомарно поправить meta.json: `change(текущее) -> новое` под замком папки.
    Временный файл свой у каждой записи — параллельные записи не делят его."""
    folder = Path(folder)
    with _meta_lock(folder):
        data = change(read_meta(folder))
        tmp = folder / f"{META_JSON}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            _replace(tmp, folder / META_JSON)
        finally:
            tmp.unlink(missing_ok=True)
        return data


def write_meta(folder: Path, updates: dict) -> dict:
    """Дописать поля в meta.json атомарно (UI читает его в любой момент)."""
    return update_meta(folder, lambda data: {**data, **updates})


def create_import(root: Path, src: Path) -> Path:
    """Папка записи под импортируемый файл. Сам файл копирует задача `import`
    (это может быть гигабайт видео — не в потоке HTTP), здесь только папка и
    meta.json. Время в имени — время изменения файла: обычно это конец встречи,
    ближе к правде, чем «сейчас»."""
    if src.suffix.lower() not in IMPORT_EXTS:
        raise ValueError(f"формат {src.suffix or 'без расширения'} не поддерживается")
    stamp = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d_%H-%M")
    root.mkdir(parents=True, exist_ok=True)
    # mkdir как проверка: exists()+mkdir() — гонка при двух импортах сразу, и
    # проигравший получал бы FileExistsError вместо своей папки.
    folder = root / f"{stamp}_import"
    n = 2
    while True:
        try:
            folder.mkdir()
            break
        except FileExistsError:
            folder = root / f"{stamp}_import-{n}"
            n += 1
    write_meta(folder, {
        "source": "import",
        "original_path": str(src),
        "original_name": src.name,
        "title": src.stem,
    })
    return folder


def _tracks(folder: Path) -> dict:
    tracks = {}
    for stem in TRACK_STEMS:
        track = find_track(folder, stem)
        if track is not None:
            tracks[stem] = track
    return tracks


def _recognised(tracks: dict, meta: dict) -> bool:
    # без дорожек это не запись (импорт — запись ещё до копии, объединение —
    # пока собирается звук)
    return bool(tracks) or meta.get("source") in ("import", "merge")


def is_recording(folder: Path) -> bool:
    """Та же проверка «это папка записи», что у describe, но без чтения
    транскрипта: обходу всей библиотеки (статистика людей) хватает одного
    разбора transcript.json на папку."""
    return folder.is_dir() and _recognised(_tracks(folder), read_meta(folder))


def recording_folders(root: Path) -> list[Path]:
    """Папки записей библиотеки по имени (= по дате), от старых к новым."""
    if not root.is_dir():
        return []
    return [f for f in sorted(root.iterdir(), key=lambda p: p.name) if is_recording(f)]


def title_and_date(folder: Path, data: dict | None, *,
                   today_if_unknown: bool = False) -> tuple[str, str]:
    """Название и дата (ГГГГ-ММ-ДД) встречи — одни для экспорта, итогов,
    вопросов и заметки: название из карточки, иначе из транскрипта, иначе имя
    папки; дата — начало записи. Неизвестна — "" (экспорт не выдумывает), а
    с `today_if_unknown` — сегодня (заметке нужна дата в имени файла)."""
    folder = Path(folder)
    card = describe(folder)
    title = (card.title if card else None) or (data or {}).get("title") or folder.name
    date = ((card.started_at if card else None) or "")[:10]
    if not date and today_if_unknown:
        date = datetime.now().strftime("%Y-%m-%d")
    return str(title), date


def describe(folder: Path) -> Recording | None:
    """Папка записи → карточка для библиотеки. Не папка записи — None."""
    if not folder.is_dir():
        return None
    tracks = _tracks(folder)
    meta = read_meta(folder)
    if not _recognised(tracks, meta):
        return None
    transcript = read_transcript(folder)
    title = None
    diarization = None
    if isinstance(transcript, dict):
        raw_title = transcript.get("title")
        title = str(raw_title) if raw_title else None
        flag = transcript.get("diarization")
        diarization = str(flag) if isinstance(flag, str) and flag else None
    if meta.get("title"):
        title = str(meta["title"])
    source = meta.get("source") if meta.get("source") in SOURCES else "record"
    try:
        transcript_at = transcript_path(folder).stat().st_mtime
    except OSError:
        transcript_at = None
    return Recording(
        id=folder.name,
        path=folder,
        started_at=_started_at(folder.name),
        tracks=tracks,
        duration_s=_duration_from_events(folder),
        has_transcript=transcript is not None or bool(list(folder.glob("*_transcript.md"))),
        has_voices=bool(list(folder.glob("*_speakers.json"))),
        title=title,
        source=source,
        transcript_at=transcript_at,
        diarization=diarization,
        kb_export=meta["kb_export"] if isinstance(meta.get("kb_export"), dict) else None,
        merge=_merge_summary(meta),
    )


def _merge_summary(meta: dict) -> dict | None:
    info = meta.get("merge")
    if meta.get("source") != "merge" or not isinstance(info, dict):
        return None
    sources = meta.get("merged_from")
    kb_left = info.get("kb_exported")
    return {
        "parts": len(sources) if isinstance(sources, list) else 0,
        "state": str(info.get("state") or "pending"),
        "deleted": bool(info.get("deleted")),
        "kb_left": [str(p) for p in kb_left] if isinstance(kb_left, list) else [],
    }


def listing(root: Path, limit: int = 200) -> list[dict]:
    """Записи от свежих к старым. Имена папок сортируются как даты."""
    if not root.is_dir():
        return []
    found = []
    for folder in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        card = describe(folder)
        if card is not None:
            found.append(card.to_raw())
        if len(found) >= limit:
            break
    return found


def search(root: Path, q: str, limit: int = 200) -> list[dict]:
    """Записи, где запрос (без учёта регистра) есть в названии или в тексте
    любой реплики. Пустой запрос — вся библиотека, как у `listing`. Полный
    обход с чтением transcript.json: библиотека — сотни папок, индекс был бы
    вторым источником истины."""
    q = (q or "").strip().lower()
    if not q:
        return listing(root, limit)
    found = []
    for card in listing(root, limit=10**9):
        transcript = read_transcript(Path(card["path"])) or {}
        haystack = [card.get("title") or "", str(transcript.get("title") or "")]
        segments = transcript.get("segments")
        if isinstance(segments, list):
            haystack += [str(s.get("text") or "") for s in segments
                         if isinstance(s, dict) and s.get("kind") != "break"]
        if any(q in part.lower() for part in haystack):
            found.append(card)
            if len(found) >= limit:
                break
    return found


def latest(root: Path) -> Recording | None:
    for folder in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        card = describe(folder)
        if card is not None:
            return card
    return None


def segments_to_raw(segments, speakers: dict | None = None,
                    title: str | None = None, source: str | None = None) -> dict:
    """Сегменты пайплайна → структурный транскрипт для редактора.

    Хранит и текст, и таймкоды, и метки нахлёста: редактору нужно ровно то же,
    что видит человек в Markdown, но в виде данных."""
    return {
        "version": 1,
        "title": title,
        "source": source,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "speakers": speakers or {},
        "segments": [
            {
                "start": round(float(s.start), 2),
                "end": round(float(s.end), 2),
                "speaker": s.speaker,
                "text": s.text,
                "uncertain": bool(getattr(s, "uncertain", False)),
                # отметка перерыва объединённой встречи (meet.merge)
                **({"kind": s.kind} if getattr(s, "kind", None) else {}),
            }
            for s in segments
        ],
    }
