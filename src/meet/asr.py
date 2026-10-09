import json
import logging
import os
import sys
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


# Маркер установщика в корне venv движка (engine.rs MARKER): {"profile": …}.
ENGINE_MARKER = "installed.json"
# Профили движка без CUDA-библиотек: torch и пакеты — процессорные.
CPU_PROFILES = ("cpu", "mac")
# Без этих библиотек Whisper (ctranslate2) на видеокарте не работает: cuBLAS
# он грузит лениво, на первом же куске звука, cuDNN нужен свёртке энкодера. В
# профиле CUDA их ставят пакеты nvidia-cublas-cu12 / nvidia-cudnn-cu12; в
# профиле CPU их нет, а ctranslate2 карту всё равно видит — и расшифровка
# падала с «cublas64_12.dll is not found» (живая проверка 0.3.0).
CUDA_LIBRARIES = ("cublas64_*.dll", "cudnn64_*.dll")
# Ошибка «нет библиотеки CUDA»: имя библиотеки и слова о загрузке в тексте.
_CUDA_LIBRARY_NAMES = ("cublas", "cudnn", "cudart")
_LOAD_FAILURE_WORDS = ("not found", "cannot be loaded", "cannot load", "could not load",
                       "could not locate", "unable to load", "failed to load", "error loading")
# Пометка транскрипта (`asr_note`): видеокарта не заработала (нет библиотек
# CUDA), встречу распознал процессор.
CUDA_FAILED = "cuda_failed"
# Почему «auto» взял процессор на движке, который умеет видеокарту (пометка
# транскрипта `asr_note` и строка в журнал): карты нет (внешняя видеокарта
# отключена вместе с доком, дискретная выключена) или нет библиотек CUDA.
NO_GPU = "no_gpu"
NO_CUDA_LIBS = "no_cuda_libs"
CPU_REASONS = {
    NO_GPU: "видеокарта NVIDIA не найдена (не подключена или выключена)",
    NO_CUDA_LIBS: "не найдены или не загружаются библиотеки CUDA (cuBLAS, cuDNN)",
    CUDA_FAILED: "видеокарта не заработала (нет библиотек CUDA)",
}

# Итог проверки библиотек CUDA на процесс: {load: годится ли}. Сбой CUDA при
# распознавании (`cuda_failed`) — дальше в этом процессе только процессор.
_cuda_runtime: dict[bool, bool] = {}
_cuda_failure: str | None = None
# Сбой CUDA у torch (диаризация, голоса): дальше torch — на процессоре. Свой
# флаг: у torch свои cuBLAS/cuDNN, распознаванию (ctranslate2) он не помеха.
_torch_failure: str | None = None
# Причины процессора, уже названные в выводе этого процесса: одна строка, а
# не на каждый вызов resolve_device.
_announced: set[str] = set()
_WINDOWS = os.name == "nt"


def engine_profile(prefix: str | Path | None = None) -> str | None:
    """Профиль установленного движка ("cuda", "cpu", "mac") по маркеру
    установщика в корне его venv; None — маркера нет (dev-окружение) или он
    нечитаем."""
    try:
        raw = json.loads((Path(prefix or sys.prefix) / ENGINE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    profile = raw.get("profile") if isinstance(raw, dict) else None
    return profile if isinstance(profile, str) and profile else None


def _nvidia_bin_dirs() -> list[Path]:
    """Папки DLL pip-пакетов nvidia-* (cuBLAS, cuDNN) в site-packages."""
    import site

    dirs: list[Path] = []
    for sp in site.getsitepackages():
        dirs += sorted((Path(sp) / "nvidia").glob("*/bin"))
    return dirs


def _library_dirs() -> list[Path]:
    """Где ctranslate2 найдёт DLL: пакеты nvidia-*, CUDA Toolkit, PATH."""
    dirs = _nvidia_bin_dirs()
    toolkit = os.environ.get("CUDA_PATH")
    if toolkit:
        dirs.append(Path(toolkit) / "bin")
    dirs += [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p.strip()]
    return dirs


def find_cuda_libraries(dirs: list[Path] | None = None) -> list[Path] | None:
    """Пути к библиотекам CUDA_LIBRARIES (первое найденное по порядку папок);
    хоть одной нет — None."""
    found: list[Path] = []
    for pattern in CUDA_LIBRARIES:
        for folder in _library_dirs() if dirs is None else dirs:
            try:
                hits = sorted(folder.glob(pattern))
            except OSError:
                hits = []
            if hits:
                found.append(hits[-1])
                break
        else:
            return None
    return found


def _module_loaded(name: str) -> bool:
    """Загружена ли уже в процесс DLL с таким именем (Windows)."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    return bool(kernel32.GetModuleHandleW(name))


def _libraries_load(libraries: list[Path]) -> bool:
    """Грузятся ли найденные DLL (битая, не той разрядности, без зависимостей
    — «нет»).

    IMPORTANT: библиотека с тем же именем уже в процессе — годится, вторую
    копию не грузим. `import torch` на Windows сам грузит torch/lib/cublas64_12
    и cudnn64_9; копия из пакета nvidia-cublas после этого падает с WinError
    127 (её cublasLt связывается с уже загруженным, более старым), хотя
    ctranslate2 возьмёт загруженную по имени. Без этого «auto» уходил на
    процессор в любом процессе, где torch импортирован раньше проверки
    (например, «Разделить спикера»: segvoices; зонд 05.10.2026)."""
    import ctypes

    for library in libraries:
        try:
            if _module_loaded(library.name):
                continue
        except Exception:
            pass  # не узнали — проверяем загрузкой, как раньше
        try:
            ctypes.WinDLL(str(library))
        except OSError as e:
            print(f"библиотека CUDA не загружается ({library.name}: {e}) — распознаёт процессор")
            return False
    return True


def cuda_runtime_ok(*, load: bool = True) -> bool:
    """Заработает ли распознавание на видеокарте — не «видна ли карта», а есть
    ли чем на ней считать. Движок профиля CPU — нет, без проверок (карту
    ctranslate2 видит и там). Иначе (профиль CUDA или dev-окружение) на Windows
    — найдены ли cuBLAS и cuDNN, а с `load` — ещё и грузятся ли они.
    `load=False` — для состояния движка в резиденте: загруженная DLL мешает
    pip обновить её. Ответ кэшируется на процесс."""
    if _cuda_failure is not None:
        return False
    if load in _cuda_runtime:
        return _cuda_runtime[load]
    if engine_profile() in CPU_PROFILES:
        ok = False
    elif not _WINDOWS:
        ok = True  # не Windows: библиотеки ищет сам ctranslate2, страхует откат
    else:
        libraries = find_cuda_libraries()
        ok = libraries is not None and (not load or _libraries_load(libraries))
    _cuda_runtime[load] = ok
    return ok


def missing_cuda_library(error: BaseException) -> bool:
    """Ошибка — «не найдена / не грузится библиотека CUDA» (cuBLAS, cuDNN,
    cudart), а не нехватка памяти или сбой модели."""
    text = str(error).lower()
    return any(n in text for n in _CUDA_LIBRARY_NAMES) and any(w in text for w in _LOAD_FAILURE_WORDS)


def cuda_failed(error: BaseException) -> None:
    """Распознавание на видеокарте упало без библиотек CUDA: дальше в этом
    процессе — только процессор (`resolve_device` → cpu при любой настройке)."""
    global _cuda_failure
    _cuda_failure = f"{type(error).__name__}: {error}"
    logger.warning("CUDA недоступна: %s — распознаёт процессор", _cuda_failure)
    print(f"видеокарта недоступна: нет библиотек CUDA ({_cuda_failure}) — распознаёт процессор")


def torch_cuda_failed(error: BaseException) -> None:
    """torch на видеокарте упал без своих библиотек CUDA: дальше в этом
    процессе диаризация и голоса — на процессоре. Распознавание (ctranslate2,
    свои cuBLAS/cuDNN) этот сбой не трогает."""
    global _torch_failure
    _torch_failure = f"{type(error).__name__}: {error}"
    logger.warning("torch: CUDA недоступна: %s — процессор", _torch_failure)
    print(f"torch: видеокарта недоступна ({_torch_failure}) — диаризация и голоса на процессоре")


def forget_failures() -> None:
    """Забыть сбои CUDA и выданные пометки (процесс задач между задачами,
    meet.job_worker.serve): следующая задача проверяет карту заново, как в
    новом процессе. Проверка библиотек (`_cuda_runtime`) остаётся."""
    global _cuda_failure, _torch_failure
    _announced.clear()
    _cuda_failure = None
    _torch_failure = None


def _reset_cuda_state() -> None:
    """Забыть проверку библиотек и сбои CUDA (для тестов)."""
    global _cuda_failure, _torch_failure
    _cuda_runtime.clear()
    _announced.clear()
    _cuda_failure = None
    _torch_failure = None


def device_setting(setting: str | None = None) -> str:
    """Настройка `asr.device` (явно переданная — она же); не прочиталась —
    «auto»."""
    if setting is not None:
        return setting
    try:
        from meet import settings

        return settings.load().asr.device
    except Exception:
        return "auto"


def gpu_engine() -> bool:
    """Умеет ли этот движок видеокарту: профиль CUDA — да, CPU и mac — нет;
    dev-окружение (маркера нет) — если в нём есть библиотеки CUDA (пакеты
    nvidia-*). Только на таком движке процессор вместо карты — повод сказать
    почему; на движке для процессора так и задумано."""
    profile = engine_profile()
    if profile in CPU_PROFILES:
        return False
    if profile is not None:
        return True
    return find_cuda_libraries() is not None


def cpu_reason(setting: str | None = None) -> str | None:
    """Почему распознавание идёт на процессоре, хотя движок умеет видеокарту:
    код из CPU_REASONS (NO_GPU, NO_CUDA_LIBS, CUDA_FAILED). None — видеокарта
    работает, процессор выбран в настройках или движок для процессора."""
    if _cuda_failure is not None:
        return CUDA_FAILED
    if device_setting(setting) == "cpu" or not gpu_engine():
        return None
    if not cuda_runtime_ok():
        return NO_CUDA_LIBS
    if not cuda_available():
        return NO_GPU
    return None


def cpu_reason_text(code: str | None) -> str | None:
    """Причина процессора словами (журнал, ход задачи)."""
    return CPU_REASONS.get(code) if code else None


def _announce_cpu(code: str | None) -> None:
    """Причина процессора — одной строкой в вывод процесса (журнал задачи,
    live.log), один раз на процесс."""
    text = cpu_reason_text(code)
    if text is None or code in _announced:
        return
    _announced.add(code)
    logger.warning("распознавание на процессоре: %s", text)
    print(f"распознавание на процессоре: {text}")


def resolve_device(setting: str | None = None) -> str:
    """Устройство распознавания: явный выбор из настроек или автоопределение.
    None — прочитать настройку `asr.device`; опечатка — как «auto».

    «auto» — видеокарта, только если на ней правда есть чем считать
    (`cuda_runtime_ok`): движок профиля CPU на машине с NVIDIA распознаёт
    процессором (GigaAM), а не падает в Whisper на CUDA. CUDA уже упала в
    этом процессе без библиотек — процессор и при явном выборе.

    Движок с CUDA, а «auto» взял процессор (карта не подключена, нет
    библиотек) — причина строкой в вывод процесса (`cpu_reason`), не молча."""
    if _cuda_failure is not None:
        return "cpu"
    setting = device_setting(setting)
    if setting in ("cuda", "cpu"):
        return setting
    if cuda_runtime_ok() and cuda_available():
        return "cuda"
    _announce_cpu(cpu_reason(setting))
    return "cpu"


def torch_device(setting: str | None = None) -> str:
    """Устройство torch (диаризация pyannote, голоса, выравнивание).

    «Процессор» в настройках (`asr.device = cpu`) — всё на процессоре, и torch
    тоже: человек так решил (например, держит видеопамять свободной). Иначе
    («auto», «cuda») — видеокарта, если torch правда её видит, чем бы ни
    распознавался текст: ctranslate2 без библиотек CUDA (GigaAM/Whisper на
    процессоре) — не повод гнать диаризацию часовой встречи процессором, там
    она в десятки раз дольше. Движок профиля CPU (torch процессорный), сбой
    CUDA у torch в этом процессе, torch не импортируется — процессор. MPS на
    macOS решает `diarize.pick_device`."""
    if _torch_failure is not None or engine_profile() in CPU_PROFILES:
        return "cpu"
    if device_setting(setting) == "cpu":
        return "cpu"
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def torch_cpu_reason(setting: str | None = None) -> str | None:
    """Почему шаги torch (диаризация, голоса, выравнивание) идут на
    процессоре, хотя не должны: сбой CUDA у torch в этом процессе или torch
    не видит видеокарту. None — torch на видеокарте, либо процессор так и
    задуман: «Процессор» в настройках, движок для процессора (CPU, mac),
    движок без CUDA (dev без пакетов nvidia-*)."""
    if device_setting(setting) == "cpu" or engine_profile() in CPU_PROFILES or not gpu_engine():
        return None
    if _torch_failure is not None:
        return f"сбой CUDA у torch ({_torch_failure})"
    try:
        import torch

        return None if torch.cuda.is_available() else "torch не видит видеокарту"
    except Exception as e:
        return f"torch не загрузился ({type(e).__name__})"


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


# Файлы модели Whisper, без которых снапшот в кэше — не модель (есть у всех
# репозиториев faster-whisper каталога).
WHISPER_FILES = ("model.bin", "config.json", "tokenizer.json")


def _whisper_model(WhisperModel, model_name: str, device: str, compute_type: str):
    """Модель Whisper. Скачана — из кэша (`local_files_only`): по имени
    faster-whisper на каждой загрузке спрашивает Hugging Face о ревизии, а на
    плохой сети это десятки секунд. Не скачана, кэш неполный или папка своей
    модели — как раньше."""
    kwargs = {"device": device, "compute_type": compute_type, **_whisper_kwargs(device)}
    if _whisper_cached(model_name):
        try:
            return WhisperModel(model_name, local_files_only=True, **kwargs)
        except FileNotFoundError:  # в кэше не всё — докачает сеть
            pass
    return WhisperModel(model_name, **kwargs)


def _whisper_cached(model_name: str) -> bool:
    """Скачана ли модель Whisper (имя репозитория или размер: «medium»)."""
    from pathlib import Path

    from meet import models

    if Path(model_name).is_dir():
        return False  # своя папка: faster-whisper в сеть и так не ходит
    repo = model_name
    if "/" not in repo:
        try:
            from faster_whisper.utils import _MODELS

            repo = _MODELS.get(repo)
        except Exception:
            repo = None
    # Не только веса: снапшот без словаря (кэш старого huggingface_hub без
    # списка файлов) отдался бы как готовый, и ctranslate2 упал бы вместо
    # докачки по сети.
    return bool(repo) and models.local_snapshot(repo, required=WHISPER_FILES) is not None


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


def _tracked(raw_segments, duration: float | None, on_progress=None):
    """Сегменты Whisper приходят лениво, по мере распознавания: конец
    очередного от длительности записи — доля сделанного."""
    for s in raw_segments:
        if on_progress and duration:
            on_progress(min(1.0, max(0.0, s.end / duration)))
        yield s


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
    # Движок умеет видеокарту, а распознаёт процессор — почему (`cpu_reason`).
    cpu_reason: str | None = None


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
        # Зеркало Hugging Face (HF_ENDPOINT) — его и проверяем: туда и пойдёт загрузка.
        request = urllib.request.Request(os.environ.get("HF_ENDPOINT") or HUB_URL, method="HEAD")
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
    reason = cpu_reason() if device == "cpu" else None
    try:
        cfg = settings.load().asr
    except Exception:
        cfg = settings.Asr()
    if cfg.backend_for(device) != "gigaam":
        return Choice("faster-whisper", device, cpu_reason=reason)
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
        return Choice("faster-whisper", device, note=NOT_RUSSIAN, cpu_reason=reason)
    if not gigaam_asr.installed():
        print("GigaAM не установлен в движке — распознаёт Whisper "
              "(переустановите движок в настройках)")
        return Choice("faster-whisper", device, cpu_reason=reason)
    return Choice("gigaam", device, cfg.gigaam_model, cpu_reason=reason)


def transcribe_wav(
    path: Path,
    hotwords: str | None = None,
    *,
    model_name: str | None = None,
    language: str | None = None,
    choice: Choice | None = None,
    on_progress=None,
) -> list[Segment]:
    """Распознать речь; при нехватке видеопамяти — квантованная модель.

    Модель и язык по умолчанию берутся из настроек (русский fine-tune и `ru`),
    но задаются и параметрами: вызывающий может знать лучше.

    Пословные таймкоды нужны для точной привязки спикеров.
    condition_on_previous_text оставлен включённым (по умолчанию): проверка
    на реальной встрече показала, что без него пунктуация и термины заметно
    деградируют, а зацикливаний и так не было благодаря vad_filter.

    `choice` (asr.choose) с движком GigaAM — распознаёт GigaAM (подсказок
    у него нет: `hotwords` не нужны, термины чинит пайплайн после).

    `on_progress(доля)` — ход распознавания 0…1: у Whisper — по концу
    последнего сегмента от длительности записи, у GigaAM — по кускам."""
    if choice is not None and choice.backend == "gigaam":
        from meet import gigaam_asr

        _add_nvidia_dll_dirs()
        extra = {"on_chunk": lambda done, total: on_progress(done / total if total else 1.0)} if on_progress else {}
        return gigaam_asr.transcribe(
            path, model_name=choice.gigaam_model or gigaam_asr.MODEL_NAME, device=choice.device, **extra)
    _add_nvidia_dll_dirs()
    _apply_hf_token()
    from faster_whisper import WhisperModel

    device = choice.device if choice is not None else resolve_device()
    model_name = _model_for(device, model_name)
    language = whisper_language(language or _asr_settings()[1])
    last_error: Exception | None = None
    for compute_type in COMPUTE_TYPES[device]:
        try:
            model = _whisper_model(WhisperModel, model_name, device, compute_type)
            # Спектр блоками (0.5.1): тот же результат, но без 1,5+ ГБ одним куском на
            # длинной записи — на занятой машине она падала с MemoryError.
            from meet import whisper_features

            whisper_features.install(model)
            print(f"Распознавание ({device}, {compute_type})...")
            segments, info = model.transcribe(
                str(path),
                language=language,
                vad_filter=True,
                word_timestamps=True,
                hotwords=hotwords,
            )
            result = _segments_from_whisper(_tracked(segments, getattr(info, "duration", 0), on_progress))
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


# Живой режим: не больше стольких токенов на секунду окна (русская речь —
# 6–8), чтобы зациклившийся декодер не держал окно 20–30 с.
LIVE_TOKENS_PER_S = 12
LIVE_TOKENS_MIN = 24


class Transcriber:
    """Резидентная модель Whisper для живого режима: грузится один раз,
    расшифровывает окна аудио без перезагрузки на каждый вызов.

    Живому режиму нужна скорость, а не точность (точный транскрипт делает
    офлайн-проход): без пословных таймкодов, без повторных проходов с
    другой температурой и с потолком токенов на окно — зациклившийся
    декодер не держит окно десятки секунд."""

    name = "Whisper"
    # Возврат латинских терминов (meet.translit) — только после GigaAM.
    latin_pass = False

    def __init__(self, model_name: str | None = None,
                 language: str | None = None) -> None:
        self._model = None
        self.compute_type: str | None = None
        default_model, default_language = _asr_settings()
        self.device = resolve_device()
        self._explicit_model = model_name
        self.model_name = _model_for(self.device, model_name)
        # `auto` — None: Whisper определяет язык окна сам (строку «auto» он
        # не принимает, и живой режим падал бы на каждом окне).
        self.language = whisper_language(language or default_language)

    def load(self) -> None:
        try:
            self._load()
        except Exception as e:
            if self.device != "cuda" or not missing_cuda_library(e):
                raise
            self._to_cpu(e)

    def _to_cpu(self, error: BaseException) -> None:
        """Видеокарта без библиотек CUDA — та же модель для процессора."""
        cuda_failed(error)
        self._model = None
        self.device = "cpu"
        self.model_name = _model_for("cpu", self._explicit_model)
        print("живой режим: видеокарта недоступна (нет библиотек CUDA) — "
              "распознаёт Whisper на процессоре")
        self._load()

    def _load(self) -> None:
        _add_nvidia_dll_dirs()
        _apply_hf_token()
        from faster_whisper import WhisperModel

        last_error: Exception | None = None
        for compute_type in COMPUTE_TYPES[self.device]:
            try:
                self._model = _whisper_model(WhisperModel, self.model_name, self.device, compute_type)
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
        try:
            return self._transcribe_window(audio, offset_s, hotwords, initial_prompt)
        except Exception as e:
            # cuBLAS ctranslate2 грузит лениво: без библиотек CUDA модель
            # загружается, а падает первое же окно.
            if self.device != "cuda" or not missing_cuda_library(e):
                raise
            self._to_cpu(e)
            return self._transcribe_window(audio, offset_s, hotwords, initial_prompt)

    def _transcribe_window(self, audio, offset_s, hotwords, initial_prompt) -> list[Segment]:
        seconds = len(audio) / 16000
        cap = max(LIVE_TOKENS_MIN, int(seconds * LIVE_TOKENS_PER_S))
        options = dict(language=self.language, vad_filter=True, word_timestamps=False,
                       hotwords=hotwords, initial_prompt=initial_prompt, temperature=0.0)
        try:
            segments, _ = self._model.transcribe(audio, max_new_tokens=cap, **options)
            return _segments_from_whisper(segments, offset_s)
        except ValueError:
            # Длинная подсказка (initial_prompt + hotwords) с потолком не
            # влезает в контекст модели — это окно без потолка.
            segments, _ = self._model.transcribe(audio, **options)
            return _segments_from_whisper(segments, offset_s)

    def unload(self) -> None:
        self._model = None
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
