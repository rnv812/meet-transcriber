"""MLX Whisper — распознавание на видеоядрах Apple Silicon (macOS 14+).

На Mac распознаёт одна выбранная модель (`asr.cpu_backend` + `asr.cpu_model`),
без деления по языку встречи. У Whisper-модели есть чекпоинт MLX
(`checkpoint`: модель MLX каталога, чекпоинт модели faster-whisper по
каталогу — поле `mlx`, или уже скачанная папка/репозиторий в формате MLX),
Mac на macOS 14 или новее, пакет `mlx_whisper` ставится, чекпоинт скачан —
`asr.choose` берёт этот движок; иначе — faster-whisper на процессоре.

Отличия от faster-whisper, которые закрывает обвязка:

* встроенного VAD нет — тишину вырезаем заранее тем же Silero VAD с теми же
  настройками, что у `vad_filter=True` (`faster_whisper.vad`), а время
  сегментов и слов возвращаем к исходной записи так же, как faster-whisper
  (`SpeechTimestampsMap`, слово — по своей середине);
* подсказок `hotwords` нет — термины уходят в `initial_prompt`;
* ответ не ленивый — ход распознавания приходит по концу сегментов уже
  после вызова.

Сегменты и слова — той же формы, что у faster-whisper (`asr.Segment`,
`asr.Word` с ведущим пробелом у слова). `mlx` и `mlx_whisper` импортируются
только на macOS и только внутри функций.
"""

import platform
from pathlib import Path

from meet import plat

BACKEND = "mlx-whisper"
MODEL = "mlx-community/whisper-large-v3-turbo"
# Модели MLX каталога (`models.CATALOGUE`, backend "mlx-whisper"; тест следит).
MODELS = (MODEL, "mlx-community/whisper-large-v3-mlx", "mlx-community/whisper-medium-mlx")
# Устройство в выборе движка (`asr.Choice.device`): видеоядра Apple (Metal).
# Свой ключ, а не «cpu»: замеры шагов MLX не смешиваются с Whisper на процессоре.
DEVICE = "mps"
MIN_MACOS = (14, 0)
SAMPLE_RATE = 16000
# Без этих файлов снапшот в кэше — не модель (загрузка оборвалась).
CONFIG = "config.json"
WEIGHTS = ("weights.safetensors", "weights.npz")


def macos_version() -> tuple[int, ...] | None:
    """Версия macOS (14, 4, 1); не macOS или не прочиталась — None."""
    if not plat.is_macos():
        return None
    try:
        return tuple(int(part) for part in platform.mac_ver()[0].split(".") if part)
    except ValueError:
        return None


def supported() -> bool:
    """Mac на macOS 14 или новее — MLX Whisper здесь возможен."""
    version = macos_version()
    return bool(version) and version >= MIN_MACOS


def _import():
    """Модуль mlx_whisper; не macOS — RuntimeError, импорт не пробуем."""
    if not plat.is_macos():
        raise RuntimeError("MLX Whisper работает только на macOS")
    import mlx_whisper

    return mlx_whisper


def installed() -> bool:
    """Ставится ли mlx_whisper (и с ним mlx) в этом движке."""
    if not supported():
        return False
    try:
        _import()
        return True
    except Exception:
        return False


def model_path(model: str = MODEL) -> Path | None:
    """Папка скачанной модели: своя папка или снапшот в кэше Hugging Face с
    настройками и весами. Нет — None."""
    from meet import models

    folder = Path(model)
    if folder.is_dir():
        return folder if _mlx_layout(folder) else None
    snapshot = models.local_snapshot(model, required=(CONFIG,))
    if snapshot is None or not any((snapshot / name).is_file() for name in WEIGHTS):
        return None
    return snapshot


def _mlx_layout(folder: Path | None) -> bool:
    """Папка — чекпоинт MLX: настройки и веса MLX (у faster-whisper — model.bin)."""
    return folder is not None and (folder / CONFIG).is_file() and any((folder / w).is_file() for w in WEIGHTS)


def checkpoint(model: str) -> str | None:
    """Чекпоинт MLX выбранной модели: модель MLX каталога — она сама; модель
    faster-whisper каталога — её чекпоинт (`mlx`), если он есть; своя папка
    или репозиторий в кэше в формате MLX — они сами. Нет — None."""
    from meet import models

    model = (model or "").strip()
    if not model:
        return None
    for item in models.CATALOGUE:
        if item["id"] == model:
            return model if item.get("backend") == BACKEND else item.get("mlx")
    folder = Path(model)
    if folder.is_dir():
        return model if _mlx_layout(folder) else None
    try:
        snapshot = models.local_snapshot(model, required=(CONFIG,))
    except (OSError, ValueError):
        return None
    return model if _mlx_layout(snapshot) else None


def mlx_only(model: str) -> bool:
    """Модель есть только в формате MLX: faster-whisper её не загрузит."""
    return bool(model) and checkpoint(model) == model


def usable(model: str = MODEL, *, log=print) -> bool:
    """Распознавать ли MLX Whisper: Mac 14+, пакет ставится, модель скачана.
    На Mac 14+ причина отказа — строкой в `log`; на других машинах — молча."""
    if not supported():
        return False
    if not installed():
        log("MLX Whisper не установлен в движке — распознаёт Whisper на процессоре")
        return False
    if model_path(model) is None:
        log("модель MLX Whisper не скачана — распознаёт Whisper на процессоре "
            "(скачать: «Распознавание» → «Модель распознавания»)")
        return False
    return True


def speech_chunks(audio, sr: int = SAMPLE_RATE) -> list[dict]:
    """Участки речи в отсчётах ({"start", "end"}) — Silero VAD faster-whisper с
    настройками по умолчанию, как у его `vad_filter=True`."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    return get_speech_timestamps(audio, VadOptions(), sampling_rate=sr)


def _read(path: Path):
    """Звук файла — float32, 16 кГц, моно (тем же декодером, что у faster-whisper)."""
    from faster_whisper.audio import decode_audio

    return decode_audio(str(path), sampling_rate=SAMPLE_RATE)


def _local(model: str) -> str:
    path = model_path(model)
    if path is None:
        raise RuntimeError("модель MLX Whisper не скачана")
    return str(path)


def transcribe(path: Path, model: str = MODEL, language: str | None = None, prompt: str | None = None,
               on_progress=None):
    """Распознать файл: list[asr.Segment] со словами, время — от начала записи.
    `language` None — язык определит модель; `prompt` — термины (initial_prompt);
    `on_progress(доля)` — по концу каждого сегмента."""
    return transcribe_audio(_read(path), model, language, prompt, on_progress=on_progress)


def transcribe_audio(audio, model: str, language: str | None, prompt: str | None, *, words: bool = True,
                     offset_s: float = 0.0, on_progress=None, **options):
    """Распознать звук (float32, 16 кГц): VAD → mlx_whisper → сегменты Meet.
    `words=False` — без меток слов (живой режим); `offset_s` переводит время
    в абсолютное время встречи; `options` — прочие параметры mlx_whisper
    (температура, condition_on_previous_text)."""
    import numpy as np
    from faster_whisper.vad import SpeechTimestampsMap, collect_chunks

    mlx_whisper = _import()
    audio = np.asarray(audio, dtype=np.float32)
    chunks = speech_chunks(audio, SAMPLE_RATE) if len(audio) else []
    if not chunks:
        return []  # без речи: Whisper на тишине выдумывает фразы
    pieces, _ = collect_chunks(audio, chunks, SAMPLE_RATE)
    result = mlx_whisper.transcribe(
        np.concatenate(pieces), path_or_hf_repo=_local(model), language=language,
        initial_prompt=prompt or None, word_timestamps=words, verbose=None, **options)
    times = SpeechTimestampsMap(chunks, SAMPLE_RATE)
    duration = len(audio) / SAMPLE_RATE
    out = []
    for raw in result.get("segments") or []:
        segment = _segment(raw, times, offset_s)
        out.append(segment)
        if on_progress and duration:
            on_progress(min(1.0, max(0.0, (segment.end - offset_s) / duration)))
    return out


def _segment(raw: dict, times, offset_s: float):
    """Сегмент mlx_whisper (dict) → asr.Segment во времени записи. Как у
    faster-whisper: слово — в кусок речи по своей середине, границы сегмента
    со словами — по первому и последнему слову."""
    from meet.asr import Segment, Word

    words = []
    for w in raw.get("words") or []:
        start, end = float(w["start"]), float(w["end"])
        index = times.get_chunk_index((start + end) / 2)
        words.append(Word(times.get_original_time(start, index) + offset_s,
                          times.get_original_time(end, index) + offset_s, str(w.get("word", ""))))
    if words:
        start, end = words[0].start, words[-1].end
    else:
        start = times.get_original_time(float(raw["start"])) + offset_s
        end = times.get_original_time(float(raw["end"]), is_end=True) + offset_s
    return Segment(start, end, str(raw.get("text", "")).strip(), words=words,
                   no_speech_prob=raw.get("no_speech_prob"), avg_logprob=raw.get("avg_logprob"))


def warm_up(model: str = MODEL) -> None:
    """Загрузить модель первым вызовом на секунде шума (живой режим: модель
    грузится при старте, а не на первой реплике). Сбой — исключение."""
    import numpy as np

    noise = np.random.default_rng(0).normal(0, 0.05, SAMPLE_RATE).astype(np.float32)
    _import().transcribe(noise, path_or_hf_repo=_local(model), word_timestamps=False, verbose=None,
                         temperature=0.0, condition_on_previous_text=False)


def unload() -> None:
    """Отпустить модель, которую mlx_whisper держит между вызовами."""
    try:
        import sys

        holder = getattr(sys.modules.get("mlx_whisper.transcribe"), "ModelHolder", None)
        if holder is not None:
            holder.model = None
            holder.model_path = None
    except Exception:
        pass
