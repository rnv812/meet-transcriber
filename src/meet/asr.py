import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Русский fine-tune large-v3 (CT2-конвертация antony66/whisper-large-v3-russian):
# заметно ниже WER на русском, чем стоковая large-v3. WhisperModel сам скачает
# репозиторий с HuggingFace. Это дефолт настройки `asr.model`, а не константа
# пайплайна: язык и модель выбираются в настройках.
MODEL_NAME = "bzikst/faster-whisper-large-v3-russian"
DEFAULT_LANGUAGE = "ru"

# Модель для машин без NVIDIA: большой русский fine-tune на CPU идёт часами.
# Выбрана medium: замер 30.09 (docs/2026-09-30-cpu-profile-bench.md) — turbo
# быстрее всего в 1.34 раза, но искажает имена и часть фраз.
CPU_MODEL_NAME = "Systran/faster-whisper-medium"
DEVICES = ("auto", "cuda", "cpu")
# Порядок попыток по устройству: на CUDA при нехватке VRAM откатываемся на
# квантованную, на CPU float16 не бывает — сразу int8.
COMPUTE_TYPES = {"cuda": ("float16", "int8_float16"), "cpu": ("int8",)}


def cuda_available() -> bool:
    """Видит ли ctranslate2 карту NVIDIA. Любая ошибка — «нет»: на ноутбуке
    без карты это штатная ситуация, а не сбой."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def resolve_device(setting: str | None = None) -> str:
    """Устройство распознавания: явный выбор из настроек или автоопределение.
    None — прочитать настройку `asr.device`; опечатка — как «auto»."""
    if setting is None:
        try:
            from meet import settings

            setting = settings.load().asr.device
        except Exception:
            setting = "auto"
    if setting in ("cuda", "cpu"):
        return setting
    return "cuda" if cuda_available() else "cpu"


def _cpu_model_setting() -> str:
    try:
        from meet import settings

        return settings.load().asr.cpu_model or CPU_MODEL_NAME
    except Exception:
        return CPU_MODEL_NAME


def _asr_settings() -> tuple[str, str]:
    """Модель и язык из настроек. Импорт отложенный: meet.settings берёт отсюда
    дефолт модели, и импорт на уровне модуля замкнул бы круг."""
    try:
        from meet import settings

        cfg = settings.load().asr
        return cfg.model or MODEL_NAME, cfg.language or DEFAULT_LANGUAGE
    except Exception:  # настройки не должны мешать расшифровке
        return MODEL_NAME, DEFAULT_LANGUAGE


def _model_for(device: str, model_name: str | None) -> str:
    """Явно переданная модель важнее; иначе своя для каждого устройства."""
    if model_name:
        return model_name
    if device == "cuda":
        return _asr_settings()[0]
    return _cpu_model_setting()


def _whisper_kwargs(device: str) -> dict:
    """На CPU — все ядра, кроме одного: встреча и UI должны оставаться живыми."""
    if device != "cpu":
        return {}
    import os

    return {"cpu_threads": max(1, (os.cpu_count() or 2) - 1)}


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    words: list[Word] = field(default_factory=list)
    no_speech_prob: float | None = None
    avg_logprob: float | None = None
    # Блок в зоне нахлёста спикеров: атрибуция ненадёжна, в транскрипте
    # помечается «(нахлёст)». Поле последнее: существующие позиционные
    # конструкторы (align.py передаёт 7 аргументов) не меняются.
    uncertain: bool = False
    # "break" — отметка перерыва между частями объединённой встречи
    # (meet.merge): не реплика, а разделитель, без спикера. Иначе None.
    kind: str | None = None
    # Дорожка записи звонка: "mic" — микрофон владельца; None — дорожка
    # собеседников (или единственная). По ней правка спикеров берёт голос.
    track: str | None = None


def _add_nvidia_dll_dirs() -> None:
    """ctranslate2 на Windows ищет DLL cuBLAS/cuDNN; pip-пакеты nvidia-*
    кладут их в site-packages, откуда система их сама не находит.

    IMPORTANT: add_dll_directory мало. cuBLAS ctranslate2 грузит лениво обычным
    LoadLibrary, а тот смотрит PATH, — без CUDA Toolkit в PATH расшифровка падала
    с «cublas64_12.dll is not found» (поймано 30.09.2026 на чистой машине)."""
    import os
    import site

    for sp in site.getsitepackages():
        for bin_dir in (Path(sp) / "nvidia").glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            current = os.environ.get("PATH", "")
            if str(bin_dir) not in current.split(os.pathsep):
                os.environ["PATH"] = str(bin_dir) + os.pathsep + current


def _apply_hf_token() -> None:
    """Пробросить токен HF в окружение до загрузки модели.

    faster-whisper качает веса своим huggingface_hub и наш токен не принимает
    параметром — берёт из окружения. Без него HF шлёт предупреждение и режет
    скорость загрузки. Ставим только если переменной ещё нет: явный env
    приоритетнее диспетчера учётных данных."""
    import os

    if os.environ.get("HF_TOKEN"):
        return
    try:
        from meet import credentials

        token = credentials.get_hf_token()
        if token:
            os.environ["HF_TOKEN"] = token
            os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", token)
    except Exception:
        pass


def _segments_from_whisper(raw_segments, offset_s: float = 0.0) -> list[Segment]:
    """Сегменты faster-whisper → list[Segment]; offset_s переводит таймкоды окна
    (всегда от нуля) в абсолютное время встречи. no_speech_prob/avg_logprob
    сохраняются для фильтра галлюцинаций (drop_hallucinations)."""
    result: list[Segment] = []
    for s in raw_segments:
        words = [
            Word(w.start + offset_s, w.end + offset_s, w.word) for w in (s.words or [])
        ]
        result.append(
            Segment(
                s.start + offset_s,
                s.end + offset_s,
                s.text.strip(),
                words=words,
                no_speech_prob=getattr(s, "no_speech_prob", None),
                avg_logprob=getattr(s, "avg_logprob", None),
            )
        )
    return result


# Консервативный денилист известных артефактов Whisper из обучающих данных
# (титры/«спасибо за просмотр»), которых в реальной рабочей речи не бывает.
# Совпадение по конкретным многословным фразам / уникальным токенам
# (НЕ по голому слову «субтитры»), чтобы не выкинуть легитимную речь.
_HALLUCINATION_PHRASES = (
    "dimatorzok",
    "amara.org",
    "субтитры сделал",
    "субтитры создавал",
    "субтитры подготовил",
    "редактор субтитров",
    "спасибо за просмотр",
    "продолжение следует",
)


def _log_dropped(s: Segment, reason: str) -> None:
    """Дроп сегмента — потеря речи, если фильтр ошибся, поэтому WARNING со всеми
    метриками: иначе выброшенный кусок встречи не оставляет никаких следов."""
    logger.warning(
        "Сегмент отброшен (%s): %r [%.2f-%.2f, no_speech_prob=%s, avg_logprob=%s]",
        reason,
        s.text,
        s.start,
        s.end,
        s.no_speech_prob,
        s.avg_logprob,
    )


def drop_hallucinations(
    segments: list[Segment],
    *,
    min_avg_logprob: float = -1.0,
) -> list[Segment]:
    """Убрать пустые сегменты и похожие на галлюцинации Whisper на тишине/шуме:
    слишком низкая средняя уверенность либо фраза из денилиста известных
    артефактов Whisper. None-метрики (офлайн-путь) по порогам не фильтруются;
    денилист — всегда.

    IMPORTANT: по no_speech_prob не фильтруем сознательно, хотя метрика есть.
    faster-whisper выдаёт её на всё 30-секундное окно декодирования, а не на
    сегмент — все сегменты окна получают одно значение, и порог по нему выносил
    окно целиком, включая уверенно распознанную речь. Окно live-режима (20 с)
    короче окна декодирования, так что терялся весь тик. Тот же баг
    встречался в утилите голосового ввода на Whisper и подтверждён на реальной
    надиктовке (no_speech_prob 0.9985 при avg_logprob -0.07). Тишину отсекает
    vad_filter до декодера, а уверенный бред на шуме ловит денилист ниже."""
    kept: list[Segment] = []
    for s in segments:
        if not s.text:
            continue
        if s.avg_logprob is not None and s.avg_logprob < min_avg_logprob:
            _log_dropped(s, "avg_logprob ниже порога")
            continue
        # денилист независим от метрик: сработает даже при None-метриках
        norm = s.text.lower().strip()
        if any(phrase in norm for phrase in _HALLUCINATION_PHRASES):
            _log_dropped(s, "денилист галлюцинаций")
            continue
        kept.append(s)
    return kept


# Почему встреча распознана не тем движком, что выбран (пометка транскрипта
# `asr_note`): запись не на русском, а GigaAM — только русский.
NOT_RUSSIAN = "not_russian"
# Язык распознавания «определить по записи» (`asr.language`).
AUTO_LANGUAGE = "auto"
# Сколько секунд от начала записи слушать, чтобы определить язык.
DETECT_LANGUAGE_S = 60


@dataclass(frozen=True)
class Choice:
    """Чем распознавать встречу: движок, устройство, модель GigaAM и, если
    движок не тот, что выбран в настройках, — почему (`note`)."""

    backend: str = "faster-whisper"
    device: str = "cpu"
    gigaam_model: str | None = None
    note: str | None = None


def detect_language(path: Path, seconds: float = DETECT_LANGUAGE_S) -> str | None:
    """Язык первой минуты записи — детектором Whisper той модели, что
    распознаёт на этом устройстве. Нужен только при `asr.language = auto`.
    Ошибка — None («не знаю»)."""
    try:
        import wave

        import numpy as np

        _add_nvidia_dll_dirs()
        _apply_hf_token()
        from faster_whisper import WhisperModel

        with wave.open(str(path), "rb") as wf:
            frames = wf.readframes(min(wf.getnframes(), int(seconds * wf.getframerate())))
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        device = resolve_device()
        model = WhisperModel(_model_for(device, None), device=device,
                             compute_type=COMPUTE_TYPES[device][-1], **_whisper_kwargs(device))
        language, probability, _ = model.detect_language(audio)
        del model
        print(f"язык записи: {language} ({probability:.2f})")
        return language
    except Exception as e:
        print(f"язык записи не определён ({type(e).__name__}: {e})")
        return None


def choose(path: Path | None = None, *, detect=None) -> Choice:
    """Движок для встречи — один на обе дорожки.

    GigaAM только русский: язык встречи не русский (или `auto` определил
    другой по первой минуте) — распознаёт Whisper, с пометкой NOT_RUSSIAN.
    Пакета GigaAM нет в движке (старая установка) — тоже Whisper."""
    from meet import gigaam_asr, settings

    device = resolve_device()
    try:
        cfg = settings.load().asr
    except Exception:
        cfg = settings.Asr()
    if cfg.backend_for(device) != "gigaam":
        return Choice("faster-whisper", device)
    language = (cfg.language or DEFAULT_LANGUAGE).strip().lower()
    if language == AUTO_LANGUAGE:
        found = (detect or detect_language)(path) if path is not None else None
        language = (found or DEFAULT_LANGUAGE).lower()
    if language != "ru":
        print(f"GigaAM распознаёт только русский, а язык записи — {language}: "
              "распознаёт Whisper")
        return Choice("faster-whisper", device, note=NOT_RUSSIAN)
    if not gigaam_asr.installed():
        print("GigaAM не установлен в движке — распознаёт Whisper "
              "(переустановите движок в настройках)")
        return Choice("faster-whisper", device)
    return Choice("gigaam", device, cfg.gigaam_model)


def transcribe_wav(
    path: Path,
    hotwords: str | None = None,
    *,
    model_name: str | None = None,
    language: str | None = None,
    choice: Choice | None = None,
) -> list[Segment]:
    """Распознать речь; при нехватке видеопамяти — квантованная модель.

    Модель и язык по умолчанию берутся из настроек (русский fine-tune и `ru`),
    но задаются и параметрами: вызывающий может знать лучше.

    Пословные таймкоды нужны для точной привязки спикеров.
    condition_on_previous_text оставлен включённым (по умолчанию): проверка
    на реальной встрече показала, что без него пунктуация и термины заметно
    деградируют, а зацикливаний и так не было благодаря vad_filter.

    `choice` (asr.choose) с движком GigaAM — распознаёт GigaAM (подсказок
    у него нет: `hotwords` не нужны, термины чинит пайплайн после)."""
    if choice is not None and choice.backend == "gigaam":
        from meet import gigaam_asr

        _add_nvidia_dll_dirs()
        return gigaam_asr.transcribe(
            path, model_name=choice.gigaam_model or gigaam_asr.MODEL_NAME, device=choice.device)
    _add_nvidia_dll_dirs()
    _apply_hf_token()
    from faster_whisper import WhisperModel

    device = choice.device if choice is not None else resolve_device()
    model_name = _model_for(device, model_name)
    language = language or _asr_settings()[1]
    if language.strip().lower() == AUTO_LANGUAGE:
        language = None  # Whisper определит сам
    last_error: Exception | None = None
    for compute_type in COMPUTE_TYPES[device]:
        try:
            model = WhisperModel(model_name, device=device, compute_type=compute_type,
                                 **_whisper_kwargs(device))
            print(f"Распознавание ({device}, {compute_type})...")
            segments, _ = model.transcribe(
                str(path),
                language=language,
                vad_filter=True,
                word_timestamps=True,
                hotwords=hotwords,
            )
            result = _segments_from_whisper(segments)
            del model
            return result
        except RuntimeError as e:
            if "memory" not in str(e).lower():
                raise
            last_error = e
            print(f"Не хватило памяти ({compute_type}), пробую компактнее...")
            if device == "cuda":
                import torch

                torch.cuda.empty_cache()
    raise SystemExit(f"Модель не загрузилась даже в int8: {last_error}")


class Transcriber:
    """Резидентная модель Whisper для живого режима: грузится один раз,
    расшифровывает окна аудио без перезагрузки на каждый вызов."""

    def __init__(self, model_name: str | None = None,
                 language: str | None = None) -> None:
        self._model = None
        self.compute_type: str | None = None
        default_model, default_language = _asr_settings()
        self.device = resolve_device()
        self.model_name = _model_for(self.device, model_name)
        self.language = language or default_language

    def load(self) -> None:
        _add_nvidia_dll_dirs()
        _apply_hf_token()
        from faster_whisper import WhisperModel

        last_error: Exception | None = None
        for compute_type in COMPUTE_TYPES[self.device]:
            try:
                self._model = WhisperModel(
                    self.model_name, device=self.device, compute_type=compute_type,
                    **_whisper_kwargs(self.device),
                )
                self.compute_type = compute_type
                print(f"Модель загружена ({self.device}, {compute_type})")
                return
            except RuntimeError as e:
                if "memory" not in str(e).lower():
                    raise
                last_error = e
                print(f"Не хватило памяти ({compute_type}), пробую компактнее...")
                if self.device == "cuda":
                    import torch

                    torch.cuda.empty_cache()
        raise SystemExit(f"Модель не загрузилась даже в int8: {last_error}")

    def transcribe_window(
        self,
        audio,
        *,
        offset_s: float = 0.0,
        hotwords: str | None = None,
        initial_prompt: str | None = None,
    ) -> list[Segment]:
        if self._model is None:
            raise RuntimeError("Transcriber.load() не был вызван")
        segments, _ = self._model.transcribe(
            audio,
            language=self.language,
            vad_filter=True,
            word_timestamps=True,
            hotwords=hotwords,
            initial_prompt=initial_prompt,
        )
        return _segments_from_whisper(segments, offset_s)

    def unload(self) -> None:
        self._model = None
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
