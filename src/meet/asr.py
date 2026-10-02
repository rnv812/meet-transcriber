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
# Язык распознавания «определить по записи» (`asr.language`).
AUTO_LANGUAGE = "auto"

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


def whisper_language(language: str | None) -> str | None:
    """Язык для Whisper: `auto` (и пусто) — None, Whisper определит сам;
    faster-whisper строку «auto» не принимает."""
    language = (language or "").strip()
    return None if not language or language.lower() == AUTO_LANGUAGE else language


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
# `asr_note`): запись не на русском, а GigaAM — только русский; или GigaAM не
# скачалась / не загрузилась (нет сети, файл битый).
NOT_RUSSIAN = "not_russian"
GIGAAM_FAILED = "gigaam_failed"
# Сколько секунд речи (суммарно, из нескольких мест записи) слушать, чтобы
# определить язык, и сколько кусков для этого брать.
DETECT_LANGUAGE_S = 60
DETECT_PIECES = 4
# Не русский — только если детектор в этом уверен: на тишине, гудках и музыке
# в начале звонка Whisper охотно отвечает «en» с низкой уверенностью.
DETECT_MIN_PROBABILITY = 0.8


@dataclass(frozen=True)
class Choice:
    """Чем распознавать встречу: движок, устройство, модель GigaAM и, если
    движок не тот, что выбран в настройках, — почему (`note`)."""

    backend: str = "faster-whisper"
    device: str = "cpu"
    gigaam_model: str | None = None
    note: str | None = None


# Запасной Whisper на процессоре — от лёгкого к тяжёлому; large-v3 (кроме
# turbo) на CPU не берём никогда: часовая встреча шла бы часами.
CPU_SMALL_MODEL = "Systran/faster-whisper-small"
CPU_TURBO_MODELS = ("deepdml/faster-whisper-large-v3-turbo-ct2", "Systran/faster-whisper-large-v3-turbo")
HUB_URL = "https://huggingface.co"
HUB_TIMEOUT_S = 10


def cpu_friendly(name: str) -> bool:
    """Модель годится для процессора: не large (turbo — можно)."""
    low = name.lower()
    return "large" not in low or "turbo" in low


def local_whisper_model(device: str) -> str | None:
    """Модель Whisper, которая уже лежит на диске. Нет ни одной — None. Для
    запасного пути и определения языка: качать гигабайты ради них не нужно.

    На процессоре — от лёгкой к тяжёлой: small → medium → turbo, затем
    выбранная (если она не large); large-v3 на CPU — никогда. На видеокарте
    — выбранная, затем русский large-v3 и остальные из каталога."""
    from meet import models

    chosen = _model_for(device, None)
    if device == "cpu":
        candidates = [CPU_SMALL_MODEL, CPU_MODEL_NAME, *CPU_TURBO_MODELS]
        candidates += [chosen] if cpu_friendly(chosen) else []
    else:
        candidates = [chosen, MODEL_NAME] + [
            m["id"] for m in models.CATALOGUE
            if m.get("kind") == models.ASR and m.get("backend") == "faster-whisper"]
    for name in dict.fromkeys(candidates):
        if Path(name).is_dir() or models.downloaded(name):
            return name
    return None


def fallback_whisper_model(device: str) -> str:
    """Какую модель Whisper качать для запасного пути, если на диске нет
    ни одной: выбранную для устройства; на процессоре — только не large."""
    chosen = _model_for(device, None)
    if device == "cpu" and not cpu_friendly(chosen):
        return CPU_MODEL_NAME
    return chosen


def model_size_text(name: str) -> str:
    """«около 1,5 ГБ» по каталогу; неизвестна — пусто."""
    from meet import models

    size = next((m.get("size_gb") for m in models.CATALOGUE if m["id"] == name), None)
    return f"около {size:g} ГБ".replace(".", ",") if size else ""


def hub_reachable(timeout: float = HUB_TIMEOUT_S) -> bool:
    """Отвечает ли Hugging Face (откуда качается Whisper) — быстрая проверка
    перед гигабайтной загрузкой. Прокси — из переменных среды задачи."""
    import urllib.request

    try:
        request = urllib.request.Request(HUB_URL, method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout):
            return True
    except Exception as e:
        # 4xx/5xx — сервер ответил: связь есть.
        return getattr(e, "code", None) is not None


def speech_sample(audio, sr: int = 16000, seconds: float = DETECT_LANGUAGE_S,
                  pieces: int = DETECT_PIECES, regions=None):
    """Образец речи для определения языка: до `seconds` секунд из `pieces`
    мест записи, только из участков речи (VAD). Тишина, гудки и ожидание в
    начале звонка в образец не попадают. Речи нет — None."""
    import numpy as np

    if regions is None:
        from meet import gigaam_asr

        regions = gigaam_asr.speech_regions(audio, sr)
    regions = [(s, e) for s, e in regions if e > s]
    total = sum(e - s for s, e in regions)
    if total <= 0:
        return None
    piece = min(seconds / pieces, total / pieces)
    parts = []
    for k in range(pieces):
        # k-я точка на «склеенной» речи — равномерно по всей записи
        at = total * (k + 0.5) / pieces - piece / 2
        for s, e in regions:
            if at < e - s:
                start = s + max(at, 0.0)
                end = min(e, start + piece)
                parts.append(audio[int(start * sr): int(end * sr)])
                break
            at -= e - s
    parts = [p for p in parts if len(p)]
    return np.concatenate(parts) if parts else None


def detect_language(path: Path, *, model_factory=None, regions=None) -> tuple[str, float] | None:
    """Язык записи по образцу речи (speech_sample) — детектором Whisper, если
    модель Whisper уже скачана. → (язык, уверенность) или None («не знаю»:
    модели нет, речи нет, ошибка). Нужен только при `asr.language = auto`."""
    try:
        import wave

        import numpy as np

        device = resolve_device()
        name = local_whisper_model(device)
        if name is None:
            print("язык записи не определяется: модель Whisper не скачана "
                  "(качать её ради этого не стал) — считаю запись русской")
            return None
        with wave.open(str(path), "rb") as wf:
            sr = wf.getframerate()
            audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        audio = audio.astype(np.float32) / 32768.0
        sample = speech_sample(audio, sr, regions=regions)
        if sample is None:
            print("язык записи не определён: речи не найдено — считаю запись русской")
            return None
        if model_factory is None:
            _add_nvidia_dll_dirs()
            from faster_whisper import WhisperModel

            def model_factory(model_name, device):
                return WhisperModel(model_name, device=device, local_files_only=True,
                                    compute_type=COMPUTE_TYPES[device][-1], **_whisper_kwargs(device))

        model = model_factory(name, device)
        segments = max(1, int(np.ceil(len(sample) / (30 * sr))))
        language, probability, _ = model.detect_language(
            sample, language_detection_segments=segments)
        del model
        print(f"язык записи: {language} ({probability:.2f})")
        return language, float(probability)
    except Exception as e:
        print(f"язык записи не определён ({type(e).__name__}: {e}) — считаю запись русской")
        return None


def choose(path: Path | None = None, *, detect=None) -> Choice:
    """Движок для встречи — один на обе дорожки.

    GigaAM только русский: язык встречи не русский (или `auto` уверенно —
    не меньше DETECT_MIN_PROBABILITY — определил другой по образцу речи) —
    распознаёт Whisper, с пометкой NOT_RUSSIAN. Неуверенный ответ детектора —
    русский. Пакета GigaAM нет в движке (старая установка) — тоже Whisper."""
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
        language = DEFAULT_LANGUAGE
        if found and found[0].lower() != "ru":
            if found[1] >= DETECT_MIN_PROBABILITY:
                language = found[0].lower()
            else:
                print(f"детектор не уверен ({found[0]}, {found[1]:.2f}) — распознаёт GigaAM")
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
    language = whisper_language(language or _asr_settings()[1])
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
        # `auto` — None: Whisper определяет язык окна сам (строку «auto» он
        # не принимает, и живой режим падал бы на каждом окне).
        self.language = whisper_language(language or default_language)

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
