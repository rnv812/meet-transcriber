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

Дорожка сегмента (`track`: микрофон владельца или собеседники) нужна, чтобы
голос реплики брать с той дорожки, где она звучит. У новых расшифровок её
ставит пайплайн (`track_marks: "pipeline"`). У старых записей звонка (0.1.x) её
нет — она вычисляется (`mark_tracks`) в памяти и в файл уходит только вместе
с применённой правкой спикеров, с пометкой источника `track_source`:

- `"audio"` — решено по звуку: задачи «Разделить спикера» и «Переразделить»
  всё равно декодируют дорожки и сравнивают громкость mic.opus и sys.opus на
  отрезке реплики (речь владельца громче в микрофоне, собеседника — в
  системном звуке). Решения задача кладёт в `segment_tracks.json`;
- `"inferred"` — по подписи: микрофон — только подписи владельца («Вы»,
  нынешнее и прежние имена из настроек), всё остальное — собеседники.
  Такая пометка выводима заново и уступает решению по звуку.

Пометкам без источника на старой записи (их писали сборки до этого правила,
иногда ошибочно: переименованный собеседник становился «микрофоном») веры
нет: дорожка вычисляется заново, а первая же правка их переписывает."""

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


TRACKS_NAME = "segment_tracks.json"
TRACK_SOURCES = ("audio", "inferred")
# Версия правила решения по звуку. Решения (segment_tracks.json) и пометки
# «по звуку» другой версии не доверяются: дорожка решается заново.
TRACK_RULE = 2
# Тест громкости: кадры по 50 мс, громкость каждой дорожки — над её же фоном:
# 10-й процентиль кадров, но не ниже FLOOR_MIN_DB (цифровые нули в системном
# звуке иначе тянули бы фон к −100 дБ, и тихий фон звонка казался бы речью).
# «Звучит» — кадр хотя бы одной дорожки громче своего фона на ACTIVE_DB;
# разница — медиана (микрофон минус собеседники) по звучащим кадрам.
# - Решение, совпадающее с подписью, — от DECIDE_DB.
# - Против подписи — от OVERRIDE_DB, и только если «чужая» по подписи дорожка
#   почти молчит: подпись владельца уходит в sys, лишь когда микрофон звучит
#   меньше чем на QUIET_SHARE кадров реплики (и наоборот).
# Неясно — решения нет, дорожка остаётся по подписи. Эхо собеседников в
# микрофоне (колонки вместо наушников) над фоном ниже, чем они же в системном
# звуке, — и реплика собеседника остаётся собеседнику.
ENERGY_RATE = 8000
FRAME_S = 0.05
ACTIVE_DB = 15.0
DECIDE_DB = 6.0
OVERRIDE_DB = 10.0
QUIET_SHARE = 0.2
FLOOR_DB = -100.0
FLOOR_MIN_DB = -70.0
FLOOR_PERCENTILE = 10


def is_call(folder: Path) -> bool:
    """Запись звонка: две дорожки — микрофон владельца и собеседники."""
    return _has(folder, "sys") and _has(folder, "mic")


def owners() -> set[str]:
    """Подписи владельца микрофона: «Вы», нынешнее и прежние имена из настроек."""
    try:
        from meet import settings

        rec = settings.load().recording
        return {"Вы", rec.speaker_name, *rec.former_speaker_names}
    except Exception:
        return {"Вы"}


def span(seg: dict) -> str:
    return f"{float(seg['start']):.2f}-{float(seg['end']):.2f}"


def _segments(data: dict) -> list[dict]:
    return [s for s in data.get("segments") or [] if isinstance(s, dict)]


def pipeline_marked(data: dict) -> bool:
    """Дорожки сегментов поставил пайплайн — им можно верить как есть."""
    marks = data.get("track_marks")
    if marks:
        return marks == "pipeline"
    segments = _segments(data)
    # Расшифровки ранних сборок: пометки есть, а track_marks ещё не было.
    return any(s.get("track") for s in segments) and not any(s.get("track_source") for s in segments)


def read_decisions(folder: Path) -> dict[str, str]:
    """Решения задач по звуку: время сегмента → "mic" | "sys"."""
    try:
        raw = json.loads((folder / TRACKS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict) or raw.get("rule") != TRACK_RULE:
        return {}  # решения прежнего правила — заново
    return {k: v for k, v in items.items() if v in ("mic", "sys")}


def _write_items(path: Path, items: dict[str, str]) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps({"version": 1, "rule": TRACK_RULE, "items": items}), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def write_decisions(folder: Path, fresh: dict[str, str]) -> None:
    if not fresh:
        return
    items = read_decisions(folder)
    items.update(fresh)
    _write_items(folder / TRACKS_NAME, items)


def _audio_mark(s: dict) -> bool:
    """Пометка «по звуку» нынешнего правила (прежние — решаются заново)."""
    return (s.get("track_source") == "audio" and s.get("track") in ("mic", "sys")
            and s.get("track_rule") == TRACK_RULE)


def mark_tracks(folder: Path, data: dict, owner_labels: set[str] | None = None) -> bool:
    """Дорожка каждого сегмента старой записи звонка — в памяти (`data`
    меняется). По старшинству: пометка «по звуку» в транскрипте, решение
    задачи по звуку (segment_tracks.json), пометка «по подписи», иначе — по
    подписи заново. Расшифровку пайплайна не трогает. True — что-то поменялось."""
    if not is_call(folder):
        return False
    if pipeline_marked(data):
        if data.get("track_marks") != "pipeline":
            data["track_marks"] = "pipeline"
            return True
        return False
    labels = owners() if owner_labels is None else owner_labels
    decided = read_decisions(folder)
    changed = False
    for s in _segments(data):
        if s.get("kind") == "break":
            continue
        source, track = s.get("track_source"), s.get("track")
        if _audio_mark(s):
            continue
        try:
            by_audio = decided.get(span(s))
        except (KeyError, TypeError, ValueError):
            by_audio = None
        if by_audio:
            new = (by_audio, "audio")
        elif source == "inferred" and track in ("mic", "sys"):
            continue
        else:
            # Нет решения нынешнего правила (и пометка «по звуку» прежнего
            # правила ему не замена) — по подписи.
            new = ("mic" if s.get("speaker") in labels else "sys", "inferred")
        if (track, source) != new or (new[1] == "audio") != ("track_rule" in s):
            s["track"], s["track_source"] = new
            if new[1] == "audio":
                s["track_rule"] = TRACK_RULE
            else:
                s.pop("track_rule", None)
            changed = True
    if data.get("track_marks") != "segments":
        data["track_marks"] = "segments"
        changed = True
    return changed


def needs_audio(folder: Path, data: dict, idx: list[int]) -> bool:
    """Нужен ли тест громкости сегментам `idx` (номера в списке сегментов)."""
    if not is_call(folder) or pipeline_marked(data):
        return False
    segments = _segments(data)
    decided = read_decisions(folder)
    for i in idx:
        s = segments[i]
        if s.get("kind") == "break" or _audio_mark(s):
            continue
        try:
            if span(s) not in decided:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def _frame_db(audio: np.ndarray, rate: int) -> np.ndarray:
    """Громкость кадров (дБ полной шкалы), по кускам — без float-копии всей дорожки."""
    size = max(1, int(rate * FRAME_S))
    n = len(audio) // size
    out = np.empty(n, dtype=np.float32)
    step = 20000
    for a in range(0, n, step):
        b = min(n, a + step)
        block = audio[a * size:b * size].reshape(b - a, size).astype(np.float32) / 32768.0
        power = (block * block).mean(axis=1)
        out[a:b] = 10.0 * np.log10(np.maximum(power, 1e-10))
    return np.maximum(out, FLOOR_DB)


def _raw_floor(db: np.ndarray) -> float:
    return float(np.percentile(db, FLOOR_PERCENTILE)) if db.size else FLOOR_DB


def _floor(db: np.ndarray) -> float:
    return max(_raw_floor(db), FLOOR_MIN_DB)


def audio_tracks(folder: Path, segments: list[dict], idx: list[int], load=None,
                 owner_labels: set[str] | None = None) -> dict[str, str]:
    """Тест громкости: чья дорожка звучит на отрезке сегмента. → время
    сегмента → "mic" | "sys"; неясные (тишина, оба поровну, спор с подписью
    без явного перевеса) не решены. `load(src) -> int16 при ENERGY_RATE`
    подменяется в тестах."""
    load = load or (lambda src: decode(src, ENERGY_RATE))
    labels = owners() if owner_labels is None else owner_labels
    levels = {}
    for stem in ("mic", "sys"):
        src = library.find_track(folder, stem)
        if src is None:
            return {}
        db = _frame_db(load(src), ENERGY_RATE)
        levels[stem] = (db, _floor(db))
    out: dict[str, str] = {}
    for i in idx:
        s = segments[i]
        if s.get("kind") == "break":
            continue
        try:
            a, b = int(float(s["start"]) / FRAME_S), int(np.ceil(float(s["end"]) / FRAME_S))
            at = span(s)
        except (KeyError, TypeError, ValueError):
            continue
        if b <= a:
            continue
        rel = {}
        for stem, (db, floor) in levels.items():
            part = db[a:b]
            if len(part) < b - a:  # дорожка короче — дальше тишина
                part = np.concatenate([part, np.full(b - a - len(part), floor, dtype=np.float32)])
            rel[stem] = np.maximum(part - floor, 0.0)
        active = (rel["mic"] > ACTIVE_DB) | (rel["sys"] > ACTIVE_DB)
        if not active.any():
            continue
        diff = float(np.median(rel["mic"][active] - rel["sys"][active]))
        by_label = "mic" if s.get("speaker") in labels else "sys"
        if abs(diff) < DECIDE_DB:
            continue
        heard = "mic" if diff > 0 else "sys"
        if heard != by_label:
            # Против подписи — только с явным перевесом и когда дорожка,
            # которой реплика принадлежит по подписи, почти молчит.
            quiet = float(np.mean(rel[by_label] > ACTIVE_DB)) < QUIET_SHARE
            if abs(diff) < OVERRIDE_DB or not quiet:
                continue
        out[at] = heard
    return out


def decide_tracks(folder: Path, data: dict, idx: list[int], bus=None, load=None,
                  owner_labels: set[str] | None = None) -> int:
    """Задача (подпроцесс): решить дорожки сегментов `idx` по звуку и
    сохранить решения для резидента. Не вышло (нет ffmpeg, битая дорожка) —
    не беда: дорожки возьмутся по подписям. → сколько решено."""
    if not needs_audio(folder, data, idx):
        return 0
    try:
        got = audio_tracks(folder, _segments(data), idx, load=load, owner_labels=owner_labels)
    except (OSError, RuntimeError, ValueError) as e:
        if bus is not None:
            bus.emit("log", text=f"дорожки реплик по звуку не определены: {e}")
        return 0
    write_decisions(folder, got)
    return len(got)


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


def prune(folder: Path, data: dict) -> int:
    """Убрать из кэша голоса (и из решений по звуку — дорожки) сегментов,
    которых в транскрипте больше нет (разрезаны, переразделены,
    перерасшифрованы). → сколько голосов убрано."""
    data = {**data, "segments": [dict(s) for s in _segments(data)]}
    mark_tracks(folder, data)  # дорожки — как их видят разделение и кэш
    segments = data["segments"]
    decided = read_decisions(folder)
    if decided:
        spans = set()
        for s in segments:
            try:
                spans.add(span(s))
            except (KeyError, TypeError, ValueError):
                continue
        if any(k not in spans for k in decided):
            _write_items(folder / TRACKS_NAME, {k: v for k, v in decided.items() if k in spans})
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


def decode(src: Path, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Дорожка → mono int16 (16 кГц по умолчанию) в памяти (ffmpeg в канал, без временного
    файла). Громкость не выравниваем: признаки модели (fbank с вычитанием
    среднего) от неё не зависят."""
    import subprocess

    from meet.audio import ensure_ffmpeg

    ensure_ffmpeg()
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1",
           "-ar", str(rate), "-f", "s16le", "-"]
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

    from meet import asr, credentials
    from meet.diarize import DIARIZATION_MODEL

    # Видеокарта — когда torch её видит (`asr.torch_device`), а не по выбору
    # распознавания: тот после `import torch` ещё и ошибался (WinError 127).
    cuda = asr.torch_device() == "cuda"
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


def compute(folder: Path, idx: list[int], bus=None, embed=None, load=decode, energy=None) -> dict:
    """Посчитать недостающие голоса сегментов `idx` и дописать их в кэш.
    `embed`/`load`/`energy` (звук для теста громкости) подменяются в тестах.
    → {"computed", "skipped", "cached"}."""
    from meet import events

    bus = bus if bus is not None else events.EventBus()
    data = library.read_transcript(folder) or {}
    data = {**data, "segments": _segments(data)}
    # Старая запись звонка: с какой дорожки брать голос реплики — по звуку.
    decide_tracks(folder, data, idx, bus=bus, load=energy)
    mark_tracks(folder, data)
    segments = data["segments"]
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
