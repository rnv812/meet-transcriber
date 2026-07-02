import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from meet.asr import Segment, Word

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"


@dataclass
class Diarization:
    """Результат диаризации: интервалы + эмбеддинг-центроид на спикера.

    embeddings может быть None: краевой путь pyannote без центроидов
    или легаси-результат (голая Annotation)."""
    turns: list[tuple[float, float, str]]
    embeddings: dict[str, np.ndarray] | None = None


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
) -> Diarization:
    """Интервалы (start, end, SPEAKER_XX) по записи.

    Берём exclusive-раскладку («в каждый момент говорит ровно один») —
    она сделана именно для сшивки с неточными таймкодами ASR."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit(_TOKEN_HELP)

    import torch
    from pyannote.audio import Pipeline

    print("Диаризация...")
    pipe = Pipeline.from_pretrained(DIARIZATION_MODEL, token=token)
    pipe.to(torch.device("cuda"))
    waveform, rate = _load_wav(path)
    result = pipe(
        {"waveform": waveform, "sample_rate": rate},
        num_speakers=num_speakers,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
    )
    return _to_diarization(result)


def _to_diarization(result) -> Diarization:
    """Достать интервалы и эмбеддинги из результата pyannote (устойчиво к легаси)."""
    annotation = getattr(result, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(result, "speaker_diarization", result)
    turns = [
        (turn.start, turn.end, label)
        for turn, _, label in annotation.itertracks(yield_label=True)
    ]
    embeddings = None
    centroids = getattr(result, "speaker_embeddings", None)
    full = getattr(result, "speaker_diarization", None)
    if centroids is not None and full is not None:
        labels = list(full.labels())
        if labels and len(labels) == len(centroids):
            embeddings = {label: centroids[i] for i, label in enumerate(labels)}
    return Diarization(turns=turns, embeddings=embeddings)


def _word_speaker(word: Word, turns: list[tuple[float, float, str]]) -> str | None:
    """Спикер с максимальным перекрытием; без перекрытия — ближайший интервал."""
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


def split_by_speaker(
    segments: list[Segment], turns: list[tuple[float, float, str]]
) -> list[Segment]:
    """Разрезать сегменты ASR по сменам спикера, назначая спикера пословно.

    Сегмент whisper может захватить смену говорящего — короткая вставка
    («Ага», «Понял») при посегментной привязке растворяется в чужой реплике."""
    if not turns:
        return segments

    out: list[Segment] = []
    for seg in segments:
        if not seg.words:
            whole = Word(seg.start, seg.end, seg.text)
            out.append(Segment(seg.start, seg.end, seg.text, _word_speaker(whole, turns)))
            continue
        run: list[Word] = []
        run_speaker: str | None = None
        for word in seg.words:
            speaker = _word_speaker(word, turns)
            if run and speaker != run_speaker:
                out.append(_run_to_segment(run, run_speaker))
                run = []
            run.append(word)
            run_speaker = speaker
        if run:
            out.append(_run_to_segment(run, run_speaker))
    return out


def _run_to_segment(run: list[Word], speaker: str | None) -> Segment:
    text = "".join(w.text for w in run).strip()
    return Segment(run[0].start, run[-1].end, text, speaker)
