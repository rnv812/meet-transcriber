"""Образец голоса владельца микрофона: по нему разделение микрофона
(meet.mic_split) отличает владельца от людей рядом с ним в комнате.

Хранится отдельно от базы голосов — `<voices>/_owner/owner.json`: база,
список людей и снимок голосов (`voices.load_voices`, `people`,
`speakers._voice_snapshot`) читают `*.json` папки нерекурсивно, и владелец не
становится «человеком» из базы. Только векторы (WeSpeaker той же модели, что
в диаризации), без звука:

    {"version": 1, "model": "...", "samples": [{"id", "embedding", "source",
     "date", "seconds", "device", "recording", "quality"}], "suggestion": null}

Откуда образец (`source`): `enroll` — записан в мастере или настройках,
`meeting` — «Это я» в панели «Спикеры», `auto` — найден по прошлым встречам и
подтверждён человеком. `suggestion` — найденный, но ещё не подтверждённый
образец (его пишет поиск по прошлым встречам); правка образцов его не трогает.
См. .superpowers/sdd/v033/speakers-design.md, §2."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

import numpy as np

from meet.diarize import DIARIZATION_MODEL

SUBDIR = "_owner"
FILE_NAME = "owner.json"
VERSION = 1
SOURCES = ("enroll", "meeting", "auto")
# Образцов не больше: по одному на микрофон и несколько встреч; старые уходят первыми.
MAX_SAMPLES = 8


@dataclass(frozen=True)
class OwnerSample:
    id: str
    embedding: np.ndarray
    source: str
    date: str
    seconds: float
    device: str | None = None
    recording: str | None = None
    quality: float | None = None

    def to_raw(self) -> dict:
        return {"id": self.id, "embedding": [float(x) for x in self.embedding], "source": self.source,
                "date": self.date, "seconds": self.seconds, "device": self.device,
                "recording": self.recording, "quality": self.quality}


def path(voices: Path | None = None) -> Path:
    if voices is None:
        from meet import voices as voice_base

        voices = voice_base.voices_dir()
    return Path(voices) / SUBDIR / FILE_NAME


def _read(p: Path) -> dict:
    """Файл как есть; нет или битый — пустой."""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"голос владельца: пропускаю {p.name} (ошибка: {e})")
        return {}
    return data if isinstance(data, dict) else {}


def _vector(value) -> np.ndarray | None:
    try:
        vec = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if vec.ndim != 1 or not vec.size or not np.isfinite(vec).all() or not np.linalg.norm(vec):
        return None
    return vec


def _optional_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sample(raw) -> OwnerSample | None:
    if not isinstance(raw, dict):
        return None
    vec = _vector(raw.get("embedding"))
    if vec is None or raw.get("source") not in SOURCES or not raw.get("id"):
        return None
    device, recording = raw.get("device"), raw.get("recording")
    return OwnerSample(id=str(raw["id"]), embedding=vec, source=raw["source"], date=str(raw.get("date") or ""),
                       seconds=_optional_float(raw.get("seconds")) or 0.0,
                       device=device if isinstance(device, str) else None,
                       recording=recording if isinstance(recording, str) else None,
                       quality=_optional_float(raw.get("quality")))


def _samples_of(data: dict) -> list[OwnerSample]:
    if data.get("model", DIARIZATION_MODEL) != DIARIZATION_MODEL:
        return []  # векторы другой модели не сравнить с нынешними
    raw = data.get("samples")
    if not isinstance(raw, list):
        return []
    return [s for s in (_sample(x) for x in raw) if s is not None]


def load(voices: Path | None = None) -> list[OwnerSample]:
    """Образцы владельца по порядку добавления; нет файла — пусто."""
    return _samples_of(_read(path(voices)))


def _write(p: Path, data: dict, samples: list[OwnerSample]) -> None:
    """Атомарно (tmp + replace); прочие ключи файла (suggestion) сохраняются."""
    p.parent.mkdir(parents=True, exist_ok=True)
    out = {**data, "version": VERSION, "model": DIARIZATION_MODEL,
           "samples": [s.to_raw() for s in samples]}
    out.setdefault("suggestion", None)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def _replaces(old: OwnerSample, new: OwnerSample) -> bool:
    """Новый образец вытесняет прежний того же происхождения: запись с того же
    микрофона, ту же встречу, прежний авто-образец."""
    if old.source != new.source:
        return False
    if new.source == "enroll":
        return old.device == new.device
    if new.source == "meeting":
        return old.recording == new.recording
    return True  # auto: один, последний подтверждённый


def add(embedding, *, source: str, seconds: float, device: str | None = None,
        recording: str | None = None, quality: float | None = None,
        voices: Path | None = None, date: str | None = None) -> OwnerSample:
    """Дописать образец. Тот же source+device (enroll), тот же recording
    (meeting) или прежний auto — заменяет; образцов не больше MAX_SAMPLES.
    Негодный вектор или источник — ValueError до записи чего-либо."""
    if source not in SOURCES:
        raise ValueError(f"непонятный источник образца: {source!r}")
    vec = _vector(embedding)
    if vec is None:
        raise ValueError("пустой или битый вектор голоса")
    sample = OwnerSample(id=uuid.uuid4().hex, embedding=vec, source=source,
                         date=date or _date.today().isoformat(), seconds=round(float(seconds), 2),
                         device=device, recording=recording,
                         quality=None if quality is None else round(float(quality), 4))
    from meet.people import VOICE_FILE_LOCK

    p = path(voices)
    with VOICE_FILE_LOCK:
        data = _read(p)
        kept = [s for s in _samples_of(data) if not _replaces(s, sample)]
        _write(p, data, (kept + [sample])[-MAX_SAMPLES:])
    return sample


def remove(sample_id: str, voices: Path | None = None) -> bool:
    """Убрать образец по id. → был ли такой."""
    from meet.people import VOICE_FILE_LOCK

    p = path(voices)
    with VOICE_FILE_LOCK:
        data = _read(p)
        samples = _samples_of(data)
        left = [s for s in samples if s.id != sample_id]
        if len(left) == len(samples):
            return False
        _write(p, data, left)
    return True


def _unit(x: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(x))
    return x / n if n else x


def _usable(emb, samples: list[OwnerSample], device: str | None) -> tuple[np.ndarray | None, list[OwnerSample]]:
    vec = _vector(emb)
    if vec is None:
        return None, []
    same = [s for s in samples if s.embedding.shape == vec.shape]
    if device is not None and any(s.device == device for s in same):
        # Есть образцы этого микрофона — с ними (и с образцами без устройства):
        # голос с другого микрофона звучит иначе и сходство не завышает.
        same = [s for s in same if s.device in (device, None)]
    return vec, same


def score(emb, samples: list[OwnerSample], device: str | None = None) -> float:
    """Сходство голоса с владельцем: максимум косинуса по образцам; образцы
    того же устройства первыми — если они есть, сравниваем только с ними (и с
    образцами без устройства). Нечего сравнить — -1.0."""
    vec, usable = _usable(emb, samples, device)
    if vec is None or not usable:
        return -1.0
    vec = _unit(vec)
    return float(max(float(vec @ _unit(s.embedding)) for s in usable))


def best_source(emb, samples: list[OwnerSample], device: str | None = None) -> str | None:
    """Происхождение ближайшего образца (`mic_split.owner_profile`)."""
    vec, usable = _usable(emb, samples, device)
    if vec is None or not usable:
        return None
    vec = _unit(vec)
    return max(usable, key=lambda s: float(vec @ _unit(s.embedding))).source


def centroid(samples: list[OwnerSample]) -> np.ndarray | None:
    """Средний голос владельца (вес — секунды речи образца), нормированный."""
    if not samples:
        return None
    dims = {s.embedding.shape for s in samples}
    shape = max(dims, key=lambda d: sum(s.embedding.shape == d for s in samples))
    use = [s for s in samples if s.embedding.shape == shape]
    w = np.array([max(s.seconds, 1.0) for s in use], dtype=np.float64)
    total = (np.stack([_unit(s.embedding.astype(np.float64)) for s in use]) * w[:, None]).sum(0)
    return _unit(total).astype(np.float32)
