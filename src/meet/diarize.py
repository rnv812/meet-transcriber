import os
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


_TOKEN_HELP = (
    "Нет токена HuggingFace (нужен для моделей диаризации pyannote).\n"
    "1) Токен: https://hf.co/settings/tokens\n"
    "2) Принять условия: https://hf.co/pyannote/speaker-diarization-community-1\n"
    "3) setx HF_TOKEN hf_... и перезапустить терминал."
)


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
) -> Diarization:
    """Diarization (интервалы + эмбеддинги + регионы нахлёста) по записи.

    По умолчанию — overlap-aware раскладка: turn говорящего непрерывен,
    перебивание лежит поверх, зоны нахлёста возвращаются отдельно.
    exclusive=True — прежняя упрощённая раскладка («в каждый момент говорит
    ровно один»), без регионов нахлёста; путь отката (--no-overlap)."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit(_TOKEN_HELP)

    import torch
    from pyannote.audio import Pipeline

    print("Диаризация...")
    pipe = Pipeline.from_pretrained(DIARIZATION_MODEL, token=token)
    from meet.asr import resolve_device

    # На машине без NVIDIA (или с CPU-сборкой torch) pyannote идёт на CPU —
    # медленнее, но работает; раньше здесь был жёсткий cuda и падение.
    use_cuda = resolve_device() == "cuda" and torch.cuda.is_available()
    pipe.to(torch.device("cuda" if use_cuda else "cpu"))
    waveform, rate = _load_wav(path)
    result = pipe(
        {"waveform": waveform, "sample_rate": rate},
        num_speakers=num_speakers,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
    )
    return _to_diarization(result, exclusive=exclusive)


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
                )
            )
            continue
        run: list[Word] = []
        run_key: tuple[str | None, bool] | None = None
        for word in seg.words:
            key = (_word_speaker(word, turns), _word_uncertain(word, overlaps))
            if run and key != run_key:
                out.append(_run_to_segment(run, *run_key))
                run = []
            run.append(word)
            run_key = key
        if run:
            out.append(_run_to_segment(run, *run_key))
    return out


def _run_to_segment(
    run: list[Word], speaker: str | None, uncertain: bool = False
) -> Segment:
    text = "".join(w.text for w in run).strip()
    return Segment(run[0].start, run[-1].end, text, speaker, uncertain=uncertain)
