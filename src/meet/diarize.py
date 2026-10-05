from dataclasses import dataclass
from pathlib import Path

import numpy as np

from meet.asr import Segment, Word

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"

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


def _load_pipeline(token: str):
    """Пайплайн pyannote или None, если Hugging Face не пустил.

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

    Нет токена Hugging Face или нет доступа к гейтед-модели — пустая
    Diarization со `skipped` (причина): вызывающий расшифровывает без
    разделения на спикеров, а не падает.

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

    token = credentials.get_hf_token()
    if not token:
        print(NO_TOKEN_NOTE)
        return Diarization(turns=[], skipped=SKIPPED_NO_TOKEN)

    print("Диаризация...")
    pipe = _load_pipeline(token)
    if pipe is None:
        return Diarization(turns=[], skipped=SKIPPED_NO_ACCESS)
    import torch

    from meet import asr

    # Видеокарта — когда torch её видит, чем бы ни распознавался текст (GigaAM
    # на процессоре — не повод гнать диаризацию часовой встречи процессором).
    # Без NVIDIA (или с CPU-сборкой torch) pyannote идёт на CPU — медленнее,
    # но работает; раньше здесь был жёсткий cuda и падение.
    use_cuda = asr.torch_device() == "cuda"
    device = pick_device(torch, use_cuda)
    if device.type == "cpu" and asr.gpu_engine():
        print("диаризация на процессоре: torch не видит видеокарту")
    pipe.to(device)
    if clustering_threshold is not None:
        params = pipe.parameters(instantiated=True)
        params.setdefault("clustering", {})["threshold"] = float(clustering_threshold)
        pipe.instantiate(params)
    waveform, rate = _load_wav(path)

    extra = {"hook": progress_hook(on_progress)} if on_progress and _takes_hook(pipe) else {}
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
        result = run()
    diar = _to_diarization(result, exclusive=exclusive)
    diar.device = used
    return diar


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
