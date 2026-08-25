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
FOLDER_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})$")
# Форматы дорожек в порядке предпочтения — те же, что понимает transcribe.
TRACK_EXTS = (".opus", ".wav", ".ogg", ".flac", ".mp3", ".m4a")


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


def describe(folder: Path) -> Recording | None:
    """Папка записи → карточка для библиотеки. Не папка записи — None."""
    if not folder.is_dir():
        return None
    tracks = {}
    for stem in ("sys", "mic"):
        track = find_track(folder, stem)
        if track is not None:
            tracks[stem] = track
    if not tracks:
        return None  # без дорожек это не запись
    transcript = read_transcript(folder)
    title = None
    if isinstance(transcript, dict):
        raw_title = transcript.get("title")
        title = str(raw_title) if raw_title else None
    return Recording(
        id=folder.name,
        path=folder,
        started_at=_started_at(folder.name),
        tracks=tracks,
        duration_s=_duration_from_events(folder),
        has_transcript=transcript is not None or bool(list(folder.glob("*_transcript.md"))),
        has_voices=bool(list(folder.glob("*_speakers.json"))),
        title=title,
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
