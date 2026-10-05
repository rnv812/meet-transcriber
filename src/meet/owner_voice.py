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

Как строить образец (запись в мастере, «Это я», поиск по прошлым встречам —
T2/T7/T8): только по участкам речи — прогонам слов или VAD (пауза внутри не
больше SAMPLE_RUN_MAX_PAUSE, участок от SAMPLE_RUN_MIN_S), никогда по целым
сегментам расшифровки: длинный сегмент Whisper бывает почти пустым (T0: 60 с
со словами на 6-й и 59-й секундах), и такие куски портят центроид. Каждый
вектор перед усреднением и сравнением нормируется: центроиды community-1 не
единичной длины (T0: нормы 0.9–2.9).
См. .superpowers/sdd/v033/speakers-design.md, §2."""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

import numpy as np

from meet.diarize import DIARIZATION_MODEL

SUBDIR = "_owner"
FILE_NAME = "owner.json"
VERSION = 1
SOURCES = ("enroll", "meeting", "auto")
# Образцов не больше: по одному на микрофон и несколько встреч; старые уходят
# первыми, записанный в мастере (enroll) — последним.
MAX_SAMPLES = 8
# Замок чтения-правки-записи между процессами: образец пишут и резидент («Это
# я»), и задача-подпроцесс (запись образца, поиск по прошлым встречам).
LOCK_NAME = ".owner.lock"
# Участки речи для образца (см. выше; T0: участки 2–10 с против общего
# центроида — p10 0.78).
SAMPLE_RUN_MAX_PAUSE = 0.5
SAMPLE_RUN_MIN_S = 2.0
LOCK_WAIT_S = 5.0


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


READ_RETRY_S = 0.05


def _read(p: Path) -> dict:
    """Файл как есть; нет или битый — пустой. На Windows файл, который как раз
    заменяет другой процесс, на миг не открыть (PermissionError) — один
    короткий повтор, а не «образца нет» для всей расшифровки."""
    try:
        try:
            text = p.read_text(encoding="utf-8")
        except PermissionError:
            time.sleep(READ_RETRY_S)
            text = p.read_text(encoding="utf-8")
        data = json.loads(text)
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


@contextmanager
def _locked(p: Path):
    """Чтение-правка-запись файла владельца: замок потоков резидента
    (people.VOICE_FILE_LOCK) и файл-замок рядом с образцом — между
    процессами. Не взяли за LOCK_WAIT_S (или замок недоступен) — правим без
    него, как meta.json (library._meta_file_lock): потерять образец хуже гонки."""
    from meet import library
    from meet.people import VOICE_FILE_LOCK

    with VOICE_FILE_LOCK:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            handle = open(p.parent / LOCK_NAME, "a+b")
        except OSError:
            yield
            return
        locked = False
        try:
            deadline = time.monotonic() + LOCK_WAIT_S
            while True:
                try:
                    library._lock_file(handle)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.01)
            yield
        finally:
            if locked:
                try:
                    library._unlock_file(handle)
                except OSError:
                    pass
            handle.close()


def _write(p: Path, data: dict, samples: list[OwnerSample]) -> None:
    """Атомарно (tmp + замена с повтором: файл может читать другой процесс);
    прочие ключи файла сохраняются. Файл другой модели: его suggestion — тоже
    векторы той модели, они уходят вместе с образцами."""
    from meet import library

    if data.get("model", DIARIZATION_MODEL) != DIARIZATION_MODEL:
        data = {k: v for k, v in data.items() if k != "suggestion"}
    p.parent.mkdir(parents=True, exist_ok=True)
    out = {**data, "version": VERSION, "model": DIARIZATION_MODEL,
           "samples": [s.to_raw() for s in samples]}
    out.setdefault("suggestion", None)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        library._replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def _evict(samples: list[OwnerSample]) -> list[OwnerSample]:
    """Не больше MAX_SAMPLES: первыми уходят старые образцы встреч и
    авто-образец; записанный в мастере (проверенный по качеству) — последним."""
    out = list(samples)
    while len(out) > MAX_SAMPLES:
        spare = [s for s in out if s.source != "enroll"] or out
        out.remove(spare[0])
    return out


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
    p = path(voices)
    with _locked(p):
        data = _read(p)
        kept = [s for s in _samples_of(data) if not _replaces(s, sample)]
        _write(p, data, _evict(kept + [sample]))
    return sample


def remove(sample_id: str, voices: Path | None = None) -> bool:
    """Убрать образец по id. → был ли такой."""
    p = path(voices)
    if not p.exists():
        return False
    with _locked(p):
        data = _read(p)
        samples = _samples_of(data)
        left = [s for s in samples if s.id != sample_id]
        if len(left) == len(samples):
            return False
        _write(p, data, left)
    return True


def put_back(samples: list[dict], voices: Path | None = None) -> int:
    """Вернуть образцы как были (тем же id) — откат шага, который их вытеснил
    («Это я» в панели «Спикеры»). Негодные и уже лежащие пропускаются.
    → сколько вернули."""
    back = [s for s in (_sample(x) for x in samples or []) if s is not None]
    if not back:
        return 0
    p = path(voices)
    with _locked(p):
        data = _read(p)
        have = _samples_of(data)
        ids = {s.id for s in have}
        fresh = [s for s in back if s.id not in ids]
        if fresh:
            _write(p, data, _evict(have + fresh))
    return len(fresh)


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
