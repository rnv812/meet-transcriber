"""Голоса отдельных реплик: эмбеддинг каждого сегмента расшифровки той же
моделью WeSpeaker, что внутри диаризации pyannote community-1 и в базе голосов
(векторы косинус-совместимы с ними, см. voice_id). Нужны, чтобы разделить
спикера, в которого диаризация слила двух людей, — без перерасшифровки.

Считает их задача `speaker_split` (подпроцесс очереди, GPU, если есть): звук
нужной дорожки декодируется один раз, затем по эмбеддингу на сегмент. Кэш —
`segment_voices.json` в папке записи, ключ — дорожка и время сегмента
(`sys:12.34-15.70`): так он переживает и правки спикеров, и разрезы реплик —
у неразрезанных сегментов ключ прежний. Отдельный файл, а не сайдкар
расшифровки: его пишет подпроцесс, а сайдкар в это же время может менять
панель «Спикеры» в резиденте — общий файл терял бы чужую запись.

Дорожка сегмента: у новых расшифровок микрофонные сегменты помечены
`track: "mic"`; у старых — вывод по подписи (`stamp_tracks`): подпись, к которой
не ведёт ни один кластер диаризации, — владелец микрофона."""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

import numpy as np

from meet import library

CACHE_NAME = "segment_voices.json"
# Короче — эмбеддинг неустойчив: такие сегменты берут группу соседей.
MIN_SECONDS = 0.8
# Длинный сегмент — середина до 30 с: голос тот же, а время счёта ограничено.
MAX_SECONDS = 30.0
SAMPLE_RATE = 16000
TRACK_STEMS = ("sys", "mic", "source")


# --- дорожки сегментов --------------------------------------------------------


def _has(folder: Path, stem: str) -> bool:
    return library.find_track(folder, stem) is not None


def default_track(folder: Path) -> str:
    """Дорожка сегментов без пометки: собеседники (`sys`) у записи звонка,
    единственная дорожка у импорта."""
    if _has(folder, "sys"):
        return "sys"
    if _has(folder, "source"):
        return "source"
    return "mic" if _has(folder, "mic") else "sys"


def _diarized_labels(data: dict, sidecar: dict | None, steps: list[dict] | None = None,
                     owners: set[str] = frozenset()) -> set[str]:
    """Подписи, к которым ведёт кластер диаризации (через цепочку `names`), и
    те, куда правки истории перенесли реплики таких подписей (кроме
    владельца микрофона)."""
    from meet.speakers import _resolve

    names = data.get("names") if isinstance(data.get("names"), dict) else {}
    out = set()
    for entry in (sidecar or {}).get("speakers") or []:
        if isinstance(entry, dict) and isinstance(entry.get("display"), str):
            out.add(entry["display"])
            out.add(_resolve(names, entry["display"]))
    for step in steps or []:
        for op in step.get("ops") or []:
            if not isinstance(op, dict):
                continue
            src = op.get("from")
            sources = src if isinstance(src, list) else [src, op.get("label")]
            if not any(isinstance(x, str) and x in out for x in sources):
                continue
            targets = [op.get("to")] + (op.get("into") if isinstance(op.get("into"), list) else [op.get("into")])
            out.update(t for t in targets if isinstance(t, str) and t not in owners)
    return out


def stamp_tracks(folder: Path, data: dict, sidecar: dict | None, owners: set[str],
                 steps: list[dict] | None = None) -> bool:
    """Старой расшифровке звонка (без пометок `track`) — пометить микрофонные
    сегменты. Владелец микрофона — подпись без кластера диаризации (а без
    сайдкара — подпись владельца из настроек или «Вы»); «Собеседник» —
    дорожка собеседников. Меняет `data`; True — было что помечать."""
    if data.get("track_marks") or not (_has(folder, "sys") and _has(folder, "mic")):
        return False
    segments = data.get("segments") or []
    if any(isinstance(s, dict) and s.get("track") for s in segments):
        data["track_marks"] = "pipeline"
        return True
    diarized = _diarized_labels(data, sidecar, steps, owners)
    mic: set[str] = set()
    labels = {s.get("speaker") for s in segments if isinstance(s, dict) and s.get("kind") != "break"}
    for label in labels:
        if not isinstance(label, str) or label == "Собеседник":
            continue
        if sidecar is not None and diarized:
            if label not in diarized and not label.startswith("SPEAKER_"):
                mic.add(label)
        elif label in owners:
            mic.add(label)
    for s in segments:
        if isinstance(s, dict) and s.get("speaker") in mic and s.get("kind") != "break":
            s["track"] = "mic"
    data["track_marks"] = "inferred"
    return True


def tracks_of(folder: Path, segments: list[dict]) -> list[str | None]:
    """Дорожка каждого сегмента; у отметки перерыва — None."""
    fallback = default_track(folder)
    out: list[str | None] = []
    for s in segments:
        if s.get("kind") == "break":
            out.append(None)
        else:
            track = s.get("track")
            out.append(track if track in TRACK_STEMS else fallback)
    return out


# --- кэш ------------------------------------------------------------------------


def key(track: str, seg: dict) -> str:
    return f"{track}:{float(seg['start']):.2f}-{float(seg['end']):.2f}"


def _encode(vec: np.ndarray) -> str:
    return base64.b64encode(np.asarray(vec, dtype=np.float16).tobytes()).decode("ascii")


def _decode(text: str) -> np.ndarray | None:
    try:
        vec = np.frombuffer(base64.b64decode(text), dtype=np.float16).astype(np.float32)
    except (ValueError, TypeError):
        return None
    return vec if vec.size and np.isfinite(vec).all() else None


def _items(folder: Path) -> dict[str, str]:
    """Кэш как есть: ключ → вектор (base64 float16) или "" — голоса у
    сегмента нет (модель не справилась), считать заново незачем."""
    from meet.diarize import DIARIZATION_MODEL

    try:
        raw = json.loads((folder / CACHE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("model") != DIARIZATION_MODEL:
        return {}
    items = raw.get("items")
    return {k: v for k, v in items.items() if isinstance(v, str)} if isinstance(items, dict) else {}


def read_cache(folder: Path) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for k, v in _items(folder).items():
        vec = _decode(v) if v else None
        if vec is not None:
            out[k] = vec
    return out


def write_cache(folder: Path, fresh: dict[str, np.ndarray], failed: list[str] = ()) -> Path:
    """Дописать векторы (и сегменты без голоса) к кэшу, атомарно."""
    from meet.diarize import DIARIZATION_MODEL

    path = folder / CACHE_NAME
    items = _items(folder)
    items.update({k: _encode(v) for k, v in fresh.items()})
    items.update({k: "" for k in failed})
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps({"model": DIARIZATION_MODEL, "items": items}), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def prune(folder: Path, segments: list[dict]) -> int:
    """Убрать из кэша голоса сегментов, которых в транскрипте больше нет
    (разрезаны, переразделены, перерасшифрованы). → сколько убрано."""
    items = _items(folder)
    if not items:
        return 0
    tracks = tracks_of(folder, segments)
    alive = {key(t, s) for s, t in zip(segments, tracks) if t is not None}
    stale = [k for k in items if k not in alive]
    if not stale:
        return 0
    from meet.diarize import DIARIZATION_MODEL

    path = folder / CACHE_NAME
    keep = {k: v for k, v in items.items() if k in alive}
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps({"model": DIARIZATION_MODEL, "items": keep}), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return len(stale)


def _duration(seg: dict) -> float:
    try:
        return max(0.0, float(seg["end"]) - float(seg["start"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def needed(folder: Path, segments: list[dict], idx: list[int]) -> list[tuple[int, str, str]]:
    """Какие из сегментов `idx` годятся для голоса (не короче MIN_SECONDS):
    (номер, дорожка, ключ кэша)."""
    tracks = tracks_of(folder, segments)
    out = []
    for i in idx:
        track = tracks[i]
        if track is None or _duration(segments[i]) < MIN_SECONDS:
            continue
        out.append((i, track, key(track, segments[i])))
    return out


def missing(folder: Path, segments: list[dict], idx: list[int]) -> list[tuple[int, str, str]]:
    known = _items(folder)
    return [x for x in needed(folder, segments, idx) if x[2] not in known]


# --- счёт (подпроцесс задачи) ---------------------------------------------------


def decode(src: Path) -> np.ndarray:
    """Дорожка → mono 16 кГц int16 в памяти (ffmpeg в канал, без временного
    файла). Громкость не выравниваем: признаки модели (fbank с вычитанием
    среднего) от неё не зависят."""
    import subprocess

    from meet.audio import ensure_ffmpeg

    ensure_ffmpeg()
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1",
           "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(cmd, capture_output=True, creationflags=flags)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg не смог прочитать {src.name}: "
                           f"{proc.stderr.decode('utf-8', 'replace')[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.int16)


def load_embedder():
    """Эмбеддер WeSpeaker из чекпойнта диаризации: видеокарта, если доступна,
    иначе процессор. audio float32 16 кГц → вектор (256,) или None."""
    import torch
    from pyannote.audio.pipelines.speaker_verification import PretrainedSpeakerEmbedding

    from meet import credentials
    from meet.asr import resolve_device
    from meet.diarize import DIARIZATION_MODEL

    cuda = resolve_device() == "cuda" and torch.cuda.is_available()
    model = PretrainedSpeakerEmbedding(
        {"checkpoint": DIARIZATION_MODEL, "subfolder": "embedding"},
        device=torch.device("cuda" if cuda else "cpu"),
        token=credentials.get_hf_token(),
    )

    def embed(audio: np.ndarray) -> np.ndarray | None:
        wav = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))[None, None, :]
        vec = np.asarray(model(wav)[0], dtype=np.float32)
        return vec if np.isfinite(vec).all() else None

    return embed


def _clip(audio: np.ndarray, seg: dict) -> np.ndarray:
    start, end = float(seg["start"]), float(seg["end"])
    if end - start > MAX_SECONDS:
        mid = (start + end) / 2
        start, end = mid - MAX_SECONDS / 2, mid + MAX_SECONDS / 2
    a, b = max(0, int(start * SAMPLE_RATE)), max(0, int(end * SAMPLE_RATE))
    return audio[a:b].astype(np.float32) / 32768.0


def compute(folder: Path, idx: list[int], bus=None, embed=None, load=decode) -> dict:
    """Посчитать недостающие голоса сегментов `idx` и дописать их в кэш.
    `embed`/`load` подменяются в тестах. → {"computed", "skipped", "cached"}."""
    from meet import events

    bus = bus if bus is not None else events.EventBus()
    data = library.read_transcript(folder) or {}
    segments = [s for s in data.get("segments") or [] if isinstance(s, dict)]
    todo = missing(folder, segments, idx)
    total = len(needed(folder, segments, idx))
    if not todo:
        bus.progress("voices", label="голоса реплик", done=total, total=total)
        return {"computed": 0, "skipped": 0, "cached": total}
    embed = embed or load_embedder()
    fresh: dict[str, np.ndarray] = {}
    failed: list[str] = []
    skipped = 0
    done = total - len(todo)
    bus.progress("voices", label="голоса реплик", done=done, total=total)
    for track in sorted({t for _, t, _ in todo}):
        src = library.find_track(folder, track)
        if src is None:
            raise RuntimeError(f"нет дорожки {track} — голоса реплик не посчитать")
        audio = load(src)
        for i, t, k in todo:
            if t != track:
                continue
            clip = _clip(audio, segments[i])
            vec = embed(clip) if clip.size >= int(MIN_SECONDS * SAMPLE_RATE) else None
            if vec is None:
                skipped += 1
                failed.append(k)
            else:
                fresh[k] = vec
            done += 1
            if done % 10 == 0 or done == total:
                bus.progress("voices", label="голоса реплик", done=done, total=total)
                if len(fresh) >= 100:  # отмена посреди длинного счёта не теряет посчитанное
                    write_cache(folder, fresh, failed)
                    fresh, failed = {}, []
    if fresh or failed:
        write_cache(folder, fresh, failed)
    bus.progress("voices", label="голоса реплик", done=total, total=total)
    return {"computed": len(todo) - skipped, "skipped": skipped, "cached": total - len(todo)}
