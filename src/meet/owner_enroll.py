"""Запись образца голоса владельца: разбор ~25 с, прочитанных вслух в мастере
или в настройках «Звук», в один отпечаток (`owner_voice.add(source="enroll")`).

Звук пишет подпроцесс устройств (`devices_probe --record mic`), разбирает
задача `owner_voice` (meet.job_worker): здесь грузятся torch и модель, а
резиденту с идущей записью они не нужны. Сам WAV удаляет задача, сразу после
разбора; хранится только вектор.

Проверки — до записи чего-либо, каждая со своим текстом для человека
(QualityError):

1. речь по Silero VAD — не меньше MIN_SPEECH_S (по участкам VAD, а не по
   целым кускам записи: калибровка T0 — длинный кусок бывает почти пустым).
   VAD — с паузой внутри участка не длиннее `owner_voice.SAMPLE_RUN_MAX_PAUSE`
   и узкими полями (VAD_PAD_MS): умолчания faster-whisper (паузы до 2 с, поля
   по 0,4 с) засчитали бы речью паузы чтения и подмешали бы их в трети;
2. клиппинг — в исходном WAV (после передискретизации пики сглажены), по
   отсчётам речи (тишина долю не разбавляет), от 0,97 полной шкалы
   (ограничители ОС держат пики чуть ниже неё);
3. уровень голоса над фоном — не меньше MIN_SNR_DB;
4. речь — на три равные трети, у каждой свой отпечаток; попарный косинус не
   ниже THIRDS_MIN_COS, иначе мешали шум или чужие голоса.

Каждый вектор нормируется до усреднения (T0: нормы отпечатков не единичные),
итог — нормированный центроид трёх третей; `quality` — худший попарный
косинус. Эмбеддер — WeSpeaker той же модели, что диаризация
(`segvoices.load_embedder`), с torch не больше чем на EMBED_THREADS потоках:
с потоками по умолчанию время на гибридных ядрах непредсказуемо (T0, §6).
См. .superpowers/sdd/v033/speakers-design.md, §2.2."""

from __future__ import annotations

import os
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
MIN_SPEECH_S = 15.0
MIN_SNR_DB = 15.0
THIRDS_MIN_COS = 0.75
# Клиппинг: отсчёт речи у полной шкалы; таких больше доли CLIP_MAX_SHARE — звук искажён.
CLIP_LEVEL = int(0.97 * 32768)
CLIP_MAX_SHARE = 0.002
FRAME_S = 0.02
# Фон — медиана кадров вне речи, если их хватает; иначе тихий процентиль всех.
NOISE_MIN_FRAMES = 25
NOISE_PERCENTILE = 5
FLOOR_DB = -100.0
EMBED_THREADS = 4
# Поля вокруг участка речи у VAD образца, мс.
VAD_PAD_MS = 100


class QualityError(ValueError):
    """Запись не годится в образец; текст — человеку, что сделать иначе."""


@dataclass(frozen=True)
class Take:
    embedding: np.ndarray
    seconds: float
    quality: float
    snr_db: float


NO_VOICE = ("Голос не слышен. Проверьте, что выбран нужный микрофон и он не выключен, "
            "и повторите.")
CLIPPED = ("Слишком громко: звук искажается. Говорите чуть дальше от микрофона или уменьшите "
           "его громкость в системе и повторите.")
NO_EMBEDDING = "Не удалось получить отпечаток голоса. Повторите запись."
DISAGREE = ("Голос в начале, середине и конце записи звучит по-разному — похоже, мешали шум или "
            "другие голоса. Повторите в тишине.")


def _too_short(seconds: float) -> str:
    return (f"Речи слишком мало: слышно {round(seconds)} с, нужно хотя бы {round(MIN_SPEECH_S)}. "
            "Читайте текст вслух без долгих пауз и повторите.")


def _too_noisy(snr: float) -> str:
    return (f"Голос всего на {max(0, round(snr))} дБ громче фона, нужно хотя бы {round(MIN_SNR_DB)}. "
            "Найдите место потише или говорите ближе к микрофону и повторите.")


def _unit(x: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(x))
    return x / n if n else x


def _frame_db(audio: np.ndarray, rate: int = SAMPLE_RATE) -> np.ndarray:
    size = max(1, int(rate * FRAME_S))
    n = len(audio) // size
    if not n:
        return np.empty(0, dtype=np.float32)
    block = audio[: n * size].reshape(n, size).astype(np.float64)
    power = (block * block).mean(axis=1)
    return np.maximum(10.0 * np.log10(np.maximum(power, 1e-10)), FLOOR_DB)


def _speech_mask(regions, frames: int, rate: int = SAMPLE_RATE) -> np.ndarray:
    mask = np.zeros(frames, dtype=bool)
    for a, b in regions:
        mask[max(0, int(a / FRAME_S)):max(0, int(np.ceil(b / FRAME_S)))] = True
    return mask


def _snr(audio: np.ndarray, regions) -> float:
    db = _frame_db(audio)
    if not db.size:
        return 0.0
    speech = _speech_mask(regions, db.size)
    if not speech.any():
        return 0.0
    quiet = db[~speech]
    floor = (float(np.median(quiet)) if quiet.size >= NOISE_MIN_FRAMES
             else float(np.percentile(db, NOISE_PERCENTILE)))
    return float(np.median(db[speech])) - floor


def _clipped(raw: np.ndarray, rate: int, regions) -> bool:
    """Доля отсчётов речи у полной шкалы больше CLIP_MAX_SHARE."""
    parts = [raw[max(0, int(a * rate)):max(0, int(b * rate))] for a, b in regions]
    speech = np.concatenate([p for p in parts if p.size] or [np.empty(0, dtype=np.int16)])
    if not speech.size:
        return False
    return float(np.mean(np.abs(speech.astype(np.int32)) >= CLIP_LEVEL)) > CLIP_MAX_SHARE


def _speech(audio: np.ndarray, regions) -> np.ndarray:
    parts = [audio[max(0, int(a * SAMPLE_RATE)):max(0, int(b * SAMPLE_RATE))] for a, b in regions]
    parts = [p for p in parts if p.size]
    return np.concatenate(parts) if parts else np.empty(0, dtype=audio.dtype)


def analyze(audio16: np.ndarray, *, raw: np.ndarray | None = None, raw_rate: int | None = None,
            vad, embed) -> Take:
    """Запись (int16 моно 16 кГц; `raw` с частотой `raw_rate` — исходный WAV для
    клиппинга) → отпечаток или QualityError. `vad(audio float32, rate) -> [(start, end)]` в секундах,
    `embed(audio float32) -> вектор | None`."""
    audio = np.asarray(audio16, dtype=np.int16).astype(np.float32) / 32768.0
    regions = sorted((float(a), float(b)) for a, b in vad(audio, SAMPLE_RATE) if float(b) > float(a))
    seconds = sum(b - a for a, b in regions)
    if seconds <= 0:
        raise QualityError(NO_VOICE)
    if seconds < MIN_SPEECH_S:
        raise QualityError(_too_short(seconds))
    clip_src, clip_rate = (raw, raw_rate or SAMPLE_RATE) if raw is not None else (audio16, SAMPLE_RATE)
    if _clipped(np.asarray(clip_src), clip_rate, regions):
        raise QualityError(CLIPPED)
    snr = _snr(audio, regions)
    if snr < MIN_SNR_DB:
        raise QualityError(_too_noisy(snr))
    thirds = np.array_split(_speech(audio, regions), 3)
    vectors = []
    for part in thirds:
        vec = embed(np.ascontiguousarray(part, dtype=np.float32))
        vec = None if vec is None else np.asarray(vec, dtype=np.float64)
        if vec is None or vec.ndim != 1 or not np.isfinite(vec).all() or not np.linalg.norm(vec):
            raise QualityError(NO_EMBEDDING)
        vectors.append(_unit(vec))
    cos = [float(vectors[i] @ vectors[j]) for i, j in ((0, 1), (0, 2), (1, 2))]
    if min(cos) < THIRDS_MIN_COS:
        raise QualityError(DISAGREE)
    centroid = _unit(np.sum(vectors, axis=0)).astype(np.float32)
    return Take(embedding=centroid, seconds=round(seconds, 2), quality=round(min(cos), 4),
                snr_db=round(snr, 1))


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """WAV 16 бит → (моно int16, частота). Многоканальный — среднее каналов."""
    with wave.open(str(path), "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        data = w.readframes(w.getnframes())
    if width != 2:
        raise QualityError("Запись в непонятном формате. Повторите запись.")
    pcm = np.frombuffer(data, dtype="<i2")
    pcm = pcm[: len(pcm) - len(pcm) % channels].reshape(-1, channels)
    return pcm.astype(np.int32).mean(axis=1).round().astype(np.int16), rate


def load_embedder():
    """Эмбеддер диаризации на процессоре, torch — не больше EMBED_THREADS
    потоков (в подпроцессе задачи: ограничение живёт с процессом). Видеокарта
    для трёх кусков по ~5 с не нужна: её запуск (cuDNN, видеопамять) дольше
    самого расчёта и мешал бы идущей расшифровке."""
    import torch

    torch.set_num_threads(max(1, min(EMBED_THREADS, os.cpu_count() or 1)))
    from meet import segvoices

    return segvoices._build_embedder("cpu")


def vad_options():
    """VadOptions образца: паузы внутри участка до SAMPLE_RUN_MAX_PAUSE, поля VAD_PAD_MS."""
    from faster_whisper.vad import VadOptions

    from meet import owner_voice

    return VadOptions(min_silence_duration_ms=int(owner_voice.SAMPLE_RUN_MAX_PAUSE * 1000),
                      speech_pad_ms=VAD_PAD_MS)


def _default_vad(audio: np.ndarray, rate: int):
    from meet.gigaam_asr import speech_regions

    return speech_regions(audio, rate, options=vad_options())


def _default_decode(path: Path) -> np.ndarray:
    from meet import segvoices

    return segvoices.decode(path, SAMPLE_RATE)


def enroll(wav: Path, *, device: str | None, voices: Path | None = None, vad=None, embed=None,
           decode=None, bus=None):
    """Разобрать запись и сохранить образец (`source="enroll"`, по устройству:
    прежний образец того же микрофона заменяется). → OwnerSample. Негодная
    запись — QualityError, и ничего не пишется. Файл не удаляет."""
    from meet import owner_voice

    def progress(note: str) -> None:
        if bus is not None:
            bus.progress("owner_voice", label="образец голоса", note=note)

    wav = Path(wav)
    progress("чтение записи")
    raw, raw_rate = read_wav(wav)
    audio16 = (decode or _default_decode)(wav)
    if embed is None:
        progress("загрузка модели")
        embed = load_embedder()
    progress("отпечаток голоса")
    take = analyze(audio16, raw=raw, raw_rate=raw_rate, vad=vad or _default_vad, embed=embed)
    return owner_voice.add(take.embedding, source="enroll", seconds=take.seconds, device=device,
                           quality=take.quality, voices=voices)
