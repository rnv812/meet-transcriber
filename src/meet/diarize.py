import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from meet.asr import Segment, Word

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"


def quiet_pyannote() -> None:
    """Телеметрия pyannote 4 выключена (до первого импорта pyannote: он читает
    переменную при загрузке): иначе каждая задача шлёт данные на
    otel.pyannote.ai из фонового потока. Явное значение в окружении — его."""
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")


# Куда, кроме вывода процесса, уходят строки диаризации (время стадий, откат
# ускорения голосов): подпроцесс задачи (meet.job_worker) отдаёт их событием
# `log` с source="timing" — голый print очередь выбрасывает, а искать их будут
# в resident.log. Диаризация шины задачи не знает, поэтому — приёмник модуля.
_log_sink = None


def set_log_sink(sink) -> None:
    """Приёмник строк диаризации (`sink(text)`); None — только вывод процесса."""
    global _log_sink
    _log_sink = sink


def _log(text: str) -> None:
    print(text)
    sink = _log_sink
    if sink is not None:
        try:
            sink(text)
        except Exception:
            pass  # журнал — подсказка, а не повод уронить диаризацию


# Минимальная длительность региона нахлёста (сек): короче — поддакивания
# («ага» на фоне) и дребезг на стыках, а не осмысленное перебивание; такие
# регионы не помечаем. Калибровка на встрече 02.07.2026 (8 человек, 75 мин):
# при 0.3 — 204 блока «(нахлёст)», при 1.0 — 75, реальная переатрибуция
# почти не меняется (124 -> 114 слов).
MIN_OVERLAP = 1.0


@dataclass
class Diarization:
    """Результат диаризации: интервалы + эмбеддинг-центроид на спикера.

    embeddings может быть None: краевой путь pyannote без центроидов
    или легаси-результат (голая Annotation).
    overlaps — регионы нахлёста (сек), где звучат >= 2 спикеров; None в
    exclusive-режиме и на легаси-результатах без get_overlap()."""
    turns: list[tuple[float, float, str]]
    embeddings: dict[str, np.ndarray] | None = None
    overlaps: list[tuple[float, float]] | None = None
    # Диаризация пропущена — почему (SKIPPED_NO_TOKEN / SKIPPED_NO_ACCESS).
    # None — диаризация была. Пропущенная не несёт ни интервалов, ни голосов.
    skipped: str | None = None
    # На чём шла диаризация ("cuda", "mps", "cpu"); None — не шла.
    device: str | None = None
    # Время стадий, секунд (load, segmentation, embeddings, clustering, total
    # и части загрузки: token, import, model, device); None — не шла.
    timings: dict | None = None


# Диаризации нет, но расшифровка идёт: реплики подписываются по дорожкам
# («Собеседник» / «Вы»), а транскрипт получает пометку с причиной.
SKIPPED_NO_TOKEN = "skipped_no_token"
SKIPPED_NO_ACCESS = "skipped_no_access"

# Печатается в консоль: только ASCII-пунктуация, cp866 не кодирует тире.
NO_TOKEN_NOTE = (
    "Нет токена Hugging Face: расшифровка без разделения на спикеров. Токен: "
    "https://hf.co/settings/tokens, условия модели: "
    "https://hf.co/pyannote/speaker-diarization-community-1"
)
NO_ACCESS_NOTE = (
    "Нет доступа к модели диаризации ({reason}): расшифровка без разделения на "
    "спикеров. Проверьте токен и условия модели: "
    "https://hf.co/pyannote/speaker-diarization-community-1"
)


def _access_reason(error: Exception) -> str:
    """Причина отказа HF без текста исключения: только класс и HTTP-код."""
    status = getattr(getattr(error, "response", None), "status_code", None)
    return f"{type(error).__name__}, HTTP {status}" if status else type(error).__name__


LOCAL_FAILED_NOTE = (
    "Модель диаризации из кэша не загрузилась ({reason}): загружаю с Hugging Face"
)


def _local_snapshot() -> Path | None:
    """Скачанная модель диаризации: папка снапшота `refs/main` в кэше Hugging
    Face (`models.cache_root`, туда же качает окно «Модели»), если в ней есть
    config.yaml. Нет модели или загрузка оборвалась — None."""
    from meet import models

    folder = models.cache_root() / ("models--" + DIARIZATION_MODEL.replace("/", "--"))
    try:
        ref = (folder / "refs" / "main").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    snapshot = folder / "snapshots" / ref
    return snapshot if ref and (snapshot / "config.yaml").is_file() else None


def _load_local():
    """Пайплайн из скачанной модели — по пути к папке снапшота: так pyannote не
    делает ни одного запроса к Hugging Face (по имени репозитория — пять HEAD
    с таймаутом 10 с каждый, а зависший прокси или DNS растягивал загрузку на
    Mac до 160 с). Токен не нужен: доступ к гейтед-модели проверили, когда её
    скачивали. Модели нет — None; копия битая — None и строка об этом
    (вызывающий идёт в сеть)."""
    snapshot = _local_snapshot()
    if snapshot is None:
        return None
    from pyannote.audio import Pipeline

    try:
        pipe = Pipeline.from_pretrained(snapshot)
    except Exception as e:  # битый кэш — не повод терять спикеров: есть сеть
        print(LOCAL_FAILED_NOTE.format(reason=type(e).__name__))
        return None
    if pipe is None:
        print(LOCAL_FAILED_NOTE.format(reason="пустой пайплайн"))
    return pipe


def _load_pipeline(token: str):
    """Пайплайн pyannote с Hugging Face (модели нет в кэше или копия битая)
    или None, если Hugging Face не пустил. Прокси — из окружения задачи
    (netproxy.settings_env).

    Неверный, отозванный токен, непринятые условия, fine-grained токен без
    доступа к гейтед-репозиториям: pyannote 4.x ловит HfHubHTTPError сам и
    возвращает None (а `pipe.to` потом падал AttributeError — уже после
    распознавания); часть путей бросает ошибку hub наружу. И то и другое —
    «нет доступа», а не повод терять расшифровку."""
    from pyannote.audio import Pipeline

    try:
        from huggingface_hub.errors import HfHubHTTPError, LocalEntryNotFoundError

        access_errors: tuple = (HfHubHTTPError, LocalEntryNotFoundError)
    except ImportError:
        access_errors = ()
    try:
        pipe = Pipeline.from_pretrained(DIARIZATION_MODEL, token=token)
    except access_errors as e:
        print(NO_ACCESS_NOTE.format(reason=_access_reason(e)))
        return None
    if pipe is None:
        print(NO_ACCESS_NOTE.format(reason="модель не загрузилась"))
    return pipe


# --- голоса за один проход на окно (pyannote-audio#2050) ---------------------
#
# pyannote считает голос (эмбеддинг WeSpeaker ResNet34) на каждую пару «окно
# 10 с × слот спикера»: три полных прогона сети на окно, и на тихие слоты тоже,
# хотя маска спикера нужна только последнему слою (статистическое усреднение).
# Сеть умеет маски всех слотов сразу (`weights` формы (batch, speakers,
# frames)): один прогон на окно и три усреднения дают те же числа втрое
# быстрее (замер 0.3.3: DER 0.00 % к штатному, 2,6–3,3× на процессоре). Это
# правка внутренностей pyannote — поэтому только на проверенной версии и
# классе, а любая ошибка — откат на штатный способ. Убрать, когда #2050 примут.

FAST_EMBEDDINGS_VERSION = "4.0."
FAST_FAILED_NOTE = "голоса за один проход на окно не сработали ({reason}): штатный способ pyannote"


def _fast_embeddings_ok(pipe) -> bool:
    """Ставить ли ускорение: pyannote.audio 4.0.x, пайплайн — SpeakerDiarization
    со штатным get_embeddings, голоса — PyannoteAudioPretrainedSpeakerEmbedding."""
    try:
        import pyannote.audio as pa
        from pyannote.audio.pipelines.speaker_diarization import SpeakerDiarization
    except Exception:
        return False
    return (str(getattr(pa, "__version__", "")).startswith(FAST_EMBEDDINGS_VERSION)
            and isinstance(pipe, SpeakerDiarization)
            and type(pipe).get_embeddings is SpeakerDiarization.get_embeddings
            and type(getattr(pipe, "_embedding", None)).__name__ == "PyannoteAudioPretrainedSpeakerEmbedding")


def _install_fast_embeddings(pipe) -> bool:
    """Подменить `pipe.get_embeddings` проходом на окно (`_shared_embeddings`).
    Ошибка прохода — штатный способ и строка в журнал; если и он не смог
    (MPS без операции), ускорение не виновато и остаётся. → поставлено ли."""
    if not _fast_embeddings_ok(pipe):
        return False
    stock = pipe.get_embeddings
    state = {"on": True}

    def get_embeddings(file, binary_segmentations, exclude_overlap=False, hook=None):
        if not state["on"] or getattr(pipe, "training", False):
            return stock(file, binary_segmentations, exclude_overlap=exclude_overlap, hook=hook)
        try:
            return _shared_embeddings(pipe, file, binary_segmentations, exclude_overlap, hook)
        except Exception as e:
            result = stock(file, binary_segmentations, exclude_overlap=exclude_overlap, hook=hook)
            state["on"] = False
            _log(FAST_FAILED_NOTE.format(reason=f"{type(e).__name__}: {str(e)[:200]}"))
            return result

    pipe.get_embeddings = get_embeddings
    pipe._meet_fast_embeddings = state
    return True


def _shared_embeddings(self, file, binary_segmentations, exclude_overlap=False, hook=None):
    """То же, что `SpeakerDiarization.get_embeddings` pyannote 4.0 (тот же
    выбор маски: без нахлёста, если чистых кадров хватает; иначе полная), но
    одна сеть на окно — с масками всех слотов разом. Окна, где все слоты
    молчат, не считаются: их голоса — заглушка (такие слоты pyannote всё равно
    отправляет в выброшенный кластер -2).

    → (окна, слоты, размерность), float32."""
    import math

    import torch

    duration = binary_segmentations.sliding_window.duration
    num_chunks, num_frames, num_speakers = binary_segmentations.data.shape
    raw = binary_segmentations.data
    if exclude_overlap:
        min_num_samples = self._embedding.min_num_samples
        num_samples = duration * self._embedding.sample_rate
        min_num_frames = math.ceil(num_frames * min_num_samples / num_samples)
        # Как у pyannote: кадр с NaN не «чистый» (сумма NaN < 2 — ложь).
        clean = raw * (np.sum(raw, axis=2, keepdims=True) < 2)
    else:
        min_num_frames = -1
        clean = raw
    masks = np.nan_to_num(raw, nan=0.0).astype(np.float32)
    clean = np.nan_to_num(clean, nan=0.0).astype(np.float32)
    use_clean = np.sum(clean, axis=1) > min_num_frames  # (окна, слоты)
    used = np.where(use_clean[:, None, :], clean, masks).transpose(0, 2, 1).copy()  # (окна, слоты, кадры)

    chunks = [chunk for chunk, _ in binary_segmentations]
    todo = [c for c in range(num_chunks) if used[c].any()]
    batch_size = self.embedding_batch_size
    total = math.ceil(len(todo) / batch_size)
    out = np.full((num_chunks, num_speakers, self._embedding.dimension), np.nan, dtype=np.float32)
    if hook is not None:
        hook("embeddings", None, total=total, completed=0)
    for i, start in enumerate(range(0, len(todo), batch_size), 1):
        idx = todo[start:start + batch_size]
        waveforms = torch.vstack([self._audio.crop(file, chunks[c], mode="pad")[0][None] for c in idx])
        batch = self._embedding(waveforms, masks=torch.from_numpy(used[idx]))
        out[idx] = batch
        if hook is not None:
            hook("embeddings", batch, total=total, completed=i)
    valid = ~np.isnan(out).any(axis=2)
    out[~valid] = out[valid][0] if valid.any() else 0.0
    return out


def _load_wav(path: Path):
    """mono 16k PCM16 wav (выход to_wav16k) → тензор (1, time) и частота.
    Читаем сами: torchcodec, на который полагается pyannote 4.x,
    не загружается на Windows."""
    import wave

    import numpy as np
    import torch

    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    waveform = torch.from_numpy(data.astype(np.float32) / 32768.0).unsqueeze(0)
    return waveform, rate


def diarize_wav(
    path: Path,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    exclusive: bool = False,
    clustering_threshold: float | None = None,
    on_progress=None,
) -> Diarization:
    """Diarization (интервалы + эмбеддинги + регионы нахлёста) по записи.

    Модель скачана — грузится с диска, без сети и без токена (`_load_local`).
    Не скачана (или копия битая), а токена Hugging Face нет или нет доступа к
    гейтед-модели — пустая Diarization со `skipped` (причина): вызывающий
    расшифровывает без разделения на спикеров, а не падает.

    По умолчанию — overlap-aware раскладка: turn говорящего непрерывен,
    перебивание лежит поверх, зоны нахлёста возвращаются отдельно.
    exclusive=True — прежняя упрощённая раскладка («в каждый момент говорит
    ровно один»), без регионов нахлёста; путь отката (--no-overlap).

    clustering_threshold — порог кластеризации голосов пайплайна (у
    community-1 по умолчанию 0.6): ниже — людей различается больше, выше —
    меньше («Переразделить на спикеров», чувствительность).

    `on_progress(доля)` — ход 0…1 по шагам пайплайна pyannote (сегментация,
    голоса), если пайплайн умеет сообщать его (`hook`); не умеет —
    `on_progress(None)`: «своей шкалы не будет», вызывающий ведёт ход по
    времени (meet.progress)."""
    from meet import credentials

    quiet_pyannote()
    clock = _StageClock()
    # Токен — только для сети: модель в кэше его не просит (и связку ключей
    # macOS второй раз за задачу не трогаем).
    cached = _local_snapshot() is not None
    token = None if cached else clock.timed("token", credentials.get_hf_token)
    if not cached and not token:
        print(NO_TOKEN_NOTE)
        return Diarization(turns=[], skipped=SKIPPED_NO_TOKEN)

    print("Диаризация...")
    clock.timed("import", _import_pyannote)
    pipe = clock.timed("model", _load_local) if cached else None
    clock.source = "из кэша" if pipe is not None else "из сети"
    if pipe is None:
        token = token or clock.timed("token", credentials.get_hf_token)
        if not token:
            print(NO_TOKEN_NOTE)
            return Diarization(turns=[], skipped=SKIPPED_NO_TOKEN)
        pipe = clock.timed("model", lambda: _load_pipeline(token))
        if pipe is None:
            return Diarization(turns=[], skipped=SKIPPED_NO_ACCESS)
    _install_fast_embeddings(pipe)
    import torch

    from meet import asr

    # Видеокарта — когда torch её видит, чем бы ни распознавался текст (GigaAM
    # на процессоре — не повод гнать диаризацию часовой встречи процессором);
    # «Процессор» в настройках — процессор и здесь (`asr.torch_device`).
    # Без NVIDIA (или с CPU-сборкой torch) pyannote идёт на CPU — медленнее,
    # но работает; раньше здесь был жёсткий cuda и падение.
    use_cuda = asr.torch_device() == "cuda"
    device = pick_device(torch, use_cuda)
    reason = asr.torch_cpu_reason() if device.type == "cpu" else None
    if reason:
        print(f"диаризация на процессоре: {reason}")
    clock.timed("device", lambda: pipe.to(device))
    if clustering_threshold is not None:
        params = pipe.parameters(instantiated=True)
        params.setdefault("clustering", {})["threshold"] = float(clustering_threshold)
        pipe.instantiate(params)
    waveform, rate = _load_wav(path)

    # Отметки шагов pyannote — всегда (время стадий), ход — если его ждут.
    extra = {"hook": clock.hook(progress_hook(on_progress) if on_progress else None)} if _takes_hook(pipe) else {}
    if on_progress and not extra:
        on_progress(None)

    def run():
        return pipe(
            {"waveform": waveform, "sample_rate": rate},
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            **extra,
        )

    used = device.type
    try:
        result = run()
    except Exception as e:
        if device.type != "mps":
            raise
        # MPS (Apple Silicon) поддерживает не все операции: тогда — процессор.
        print(f"Диаризация на MPS не прошла ({type(e).__name__}) — повторяю на процессоре")
        pipe.to(torch.device("cpu"))
        used = "cpu"
        clock.retry("после сбоя MPS")
        result = run()
    clock.stop()
    diar = _to_diarization(result, exclusive=exclusive)
    diar.device = used
    diar.timings = clock.result()
    fast = getattr(pipe, "_meet_fast_embeddings", None)
    _log(clock.line(used) + ("; голоса за один проход на окно" if fast and fast["on"] else ""))
    return diar


def _import_pyannote() -> None:
    """Импорт pyannote отдельно от загрузки модели — для времени стадий (на
    свежем движке он сам по себе секунды). Нет пакета — скажет загрузка."""
    import importlib

    try:
        importlib.import_module("pyannote.audio")
    except ImportError:
        pass


class _StageClock:
    """Время стадий диаризации: загрузка (всё до первого отчёта сегментации:
    токен, импорт pyannote, модель, перенос на устройство, звук) и шаги
    pyannote по отметкам `hook` — сегментация, голоса, кластеризация."""

    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.end: float | None = None
        self.parts: dict[str, float] = {}
        self.marks: list[tuple[str, float]] = []
        self.source: str | None = None
        self.note: str | None = None

    def timed(self, name: str, call):
        since = time.perf_counter()
        try:
            return call()
        finally:
            self.parts[name] = self.parts.get(name, 0.0) + time.perf_counter() - since

    def hook(self, forward=None):
        def hook(step_name, step_artifact=None, file=None, total=None, completed=None):
            self.marks.append((step_name, time.perf_counter()))
            if forward is not None:
                forward(step_name, step_artifact, file=file, total=total, completed=completed)
        return hook

    def retry(self, note: str) -> None:
        """Повтор прогона (MPS → процессор): шаги — второго прогона, а первый
        уходит в загрузку."""
        self.marks.clear()
        self.note = note

    def stop(self) -> None:
        self.end = time.perf_counter()

    def result(self) -> dict:
        end = self.end if self.end is not None else time.perf_counter()

        def first(name):
            return next((t for step, t in self.marks if step == name), None)

        def last(name):
            return next((t for step, t in reversed(self.marks) if step == name), None)

        seg, emb, emb_end = first("segmentation"), first("embeddings"), last("embeddings")
        out = {"load": (seg if seg is not None else end) - self.start,
               "segmentation": ((emb if emb is not None else end) - seg) if seg is not None else 0.0,
               "embeddings": emb_end - emb if emb is not None else 0.0,
               "clustering": end - emb_end if emb is not None else 0.0,
               "total": end - self.start,
               "source": self.source}
        out.update(self.parts)
        return out

    def line(self, device: str) -> str:
        """Строка для журнала: где ушло время (ASCII-пунктуация: печатается и в
        консоль cp866)."""
        t = self.result()
        names = (("token", "токен"), ("import", "импорт"), ("model", "модель"), ("device", "устройство"))
        parts = [f"{label} {t[key]:.1f} с" + (f" {self.source}" if key == "model" and self.source else "")
                 for key, label in names if key in t]
        load = f"загрузка {t['load']:.1f} с" + (f" ({', '.join(parts)})" if parts else "")
        head = ", ".join([device] + ([self.note] if self.note else []))
        return (f"время диаризации ({head}): {load}, сегментация {t['segmentation']:.1f} с, "
                f"голоса {t['embeddings']:.1f} с, кластеризация {t['clustering']:.1f} с, "
                f"всего {t['total']:.1f} с")


def report_cpu(bus, stages=None, what: str = "диаризация") -> bool:
    """Шаги torch идут на процессоре не по выбору человека (`asr.torch_cpu_reason`)
    — причина строкой в журнал резидента (событие `log`, source="device":
    голый print подпроцесса задачи очередь выбрасывает) и, если есть ход
    задачи (`stages`), предупреждением под полоской. → сказано ли."""
    from meet import asr, events

    reason = asr.torch_cpu_reason()
    if reason is None:
        return False
    bus.emit(events.LOG, text=f"{what} на процессоре: {reason}", source="device")
    if stages is not None:
        stages.add_warning(f"{what[0].upper()}{what[1:]} на процессоре: {reason}")
    return True


# Доли шагов pyannote в общем ходе диаризации: сегментация и голоса (эмбеддинги)
# — почти всё время; остальное — короткие шаги между ними.
_HOOK_SPANS = {"segmentation": (0.0, 0.3), "embeddings": (0.3, 0.95)}
_HOOK_AFTER = {"speaker_counting": 0.3, "discrete_diarization": 0.97}


def progress_hook(on_progress):
    """`hook` пайплайна pyannote → доля 0…1. Шаг с `total/completed` идёт
    внутри своего промежутка, шаг без них — отметка его начала."""
    def hook(step_name, step_artifact=None, file=None, total=None, completed=None):
        try:
            if step_name in _HOOK_SPANS and total and completed is not None:
                lo, hi = _HOOK_SPANS[step_name]
                on_progress(lo + (hi - lo) * min(1.0, completed / total))
            elif step_name in _HOOK_AFTER:
                on_progress(_HOOK_AFTER[step_name])
        except Exception:
            pass  # ход — подсказка, а не повод уронить диаризацию
    return hook


def _takes_hook(pipe) -> bool:
    """Принимает ли пайплайн `hook` (pyannote 3.1+)."""
    import inspect

    try:
        return "hook" in inspect.signature(pipe.apply).parameters
    except (AttributeError, TypeError, ValueError):
        return False


def pick_device(torch, use_cuda: bool):
    """Устройство pyannote: CUDA, если выбрана и есть; на macOS — MPS (Apple
    Silicon), если torch его видит; иначе процессор. Недостающие на MPS
    операции torch выполняет на процессоре (PYTORCH_ENABLE_MPS_FALLBACK)."""
    if use_cuda:
        return torch.device("cuda")
    from meet import plat

    mps = getattr(getattr(torch, "backends", None), "mps", None)
    if plat.is_macos() and mps is not None and mps.is_available():
        import os

        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        return torch.device("mps")
    return torch.device("cpu")


def _to_diarization(result, exclusive: bool = False) -> Diarization:
    """Достать интервалы, эмбеддинги и нахлёсты из результата pyannote.

    По умолчанию turns — из overlap-aware speaker_diarization; exclusive=True —
    из exclusive_speaker_diarization (прежнее поведение, без overlaps).
    Устойчиво к легаси-результату (голая Annotation)."""
    full = getattr(result, "speaker_diarization", None)
    if exclusive:
        annotation = getattr(result, "exclusive_speaker_diarization", None)
        if annotation is None:
            annotation = full if full is not None else result
    else:
        annotation = full if full is not None else result
    turns = [
        (turn.start, turn.end, label)
        for turn, _, label in annotation.itertracks(yield_label=True)
    ]
    overlaps = None if exclusive else _overlap_regions(annotation)
    embeddings = None
    centroids = getattr(result, "speaker_embeddings", None)
    if centroids is not None and full is not None:
        labels = list(full.labels())
        if labels and len(labels) == len(centroids):
            embeddings = {label: centroids[i] for i, label in enumerate(labels)}
    return Diarization(turns=turns, embeddings=embeddings, overlaps=overlaps)


def _overlap_regions(annotation) -> list[tuple[float, float]] | None:
    """Регионы, где звучат >= 2 разных спикеров, длительностью от MIN_OVERLAP.

    Annotation без get_overlap() (легаси/синтетика) -> None."""
    get_overlap = getattr(annotation, "get_overlap", None)
    if get_overlap is None:
        return None
    return [
        (seg.start, seg.end)
        for seg in get_overlap()
        if seg.end - seg.start >= MIN_OVERLAP
    ]


def _word_speaker(word: Word, turns: list[tuple[float, float, str]]) -> str | None:
    """Спикер с максимальным перекрытием; без перекрытия — ближайший интервал.

    При равном перекрытии (overlap-aware turns: слово целиком накрыто двумя
    спикерами) строгий `>` оставляет более ранний turn — обычно это длинная
    фраза, поверх которой легло перебивание; first-wins здесь осознанный."""
    best, best_overlap = None, 0.0
    for start, end, label in turns:
        overlap = min(word.end, end) - max(word.start, start)
        if overlap > best_overlap:
            best, best_overlap = label, overlap
    if best is not None:
        return best
    nearest = min(
        turns,
        key=lambda t: max(t[0] - word.end, word.start - t[1], 0.0),
        default=None,
    )
    return nearest[2] if nearest else None


def _word_uncertain(word: Word, overlaps: list[tuple[float, float]] | None) -> bool:
    """Слово в зоне нахлёста: строгое пересечение (длина > 0) с любым регионом."""
    if not overlaps:
        return False
    return any(min(word.end, e) - max(word.start, s) > 0 for s, e in overlaps)


def split_by_speaker(
    segments: list[Segment],
    turns: list[tuple[float, float, str]],
    overlaps: list[tuple[float, float]] | None = None,
) -> list[Segment]:
    """Разрезать сегменты ASR по сменам спикера, назначая спикера пословно.

    Сегмент whisper может захватить смену говорящего — короткая вставка
    («Ага», «Понял») при посегментной привязке растворяется в чужой реплике.
    overlaps — регионы нахлёста: прогон режется по паре (спикер, uncertain),
    зона нахлёста становится отдельным блоком с uncertain=True."""
    if not turns:
        return segments

    out: list[Segment] = []
    for seg in segments:
        if not seg.words:
            whole = Word(seg.start, seg.end, seg.text)
            out.append(
                Segment(
                    seg.start,
                    seg.end,
                    seg.text,
                    _word_speaker(whole, turns),
                    uncertain=_word_uncertain(whole, overlaps),
                    track=seg.track,
                )
            )
            continue
        run: list[Word] = []
        run_key: tuple[str | None, bool] | None = None
        for word in seg.words:
            key = (_word_speaker(word, turns), _word_uncertain(word, overlaps))
            if run and key != run_key:
                out.append(_run_to_segment(run, *run_key, track=seg.track))
                run = []
            run.append(word)
            run_key = key
        if run:
            out.append(_run_to_segment(run, *run_key, track=seg.track))
    return out


def _run_to_segment(
    run: list[Word], speaker: str | None, uncertain: bool = False, track: str | None = None
) -> Segment:
    """Слова одного спикера — сегмент; слова остаются при нём: по ним правка
    спикеров режет реплику на границе слова (транскрипт хранит их)."""
    text = "".join(w.text for w in run).strip()
    return Segment(run[0].start, run[-1].end, text, speaker, words=list(run),
                   uncertain=uncertain, track=track)
