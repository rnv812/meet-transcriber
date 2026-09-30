"""Библиотека записей: что лежит на диске и в каком оно состоянии.

Источник истины — файлы, а не индекс: папка записи самодостаточна, и её можно
скопировать с другой машины (сценарий «записал на ноутбуке — расшифровал на
десктопе» из README). Поэтому здесь нет базы, только чтение каталога; кэш, если
понадобится, всегда можно восстановить перечитыванием.

Структурный транскрипт (`transcript.json`) — источник для редактора; Markdown
остаётся человеческим артефактом и форматом экспорта.
"""

import json
import re
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
SOURCES = ("record", "auto", "import")


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


def write_meta(folder: Path, updates: dict) -> dict:
    """Дописать поля в meta.json атомарно (UI читает его в любой момент)."""
    import os

    data = {**read_meta(folder), **updates}
    tmp = folder / (META_JSON + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, folder / META_JSON)
    return data


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
    # без дорожек это не запись (импорт — запись ещё до копии)
    return bool(tracks) or meta.get("source") == "import"


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
    if isinstance(transcript, dict):
        raw_title = transcript.get("title")
        title = str(raw_title) if raw_title else None
    if meta.get("title"):
        title = str(meta["title"])
    source = meta.get("source") if meta.get("source") in SOURCES else "record"
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
    )


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
            haystack += [str(s.get("text") or "") for s in segments if isinstance(s, dict)]
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
            }
            for s in segments
        ],
    }
