import os
from pathlib import Path

from meet.asr import Segment

_TOKEN_HELP = (
    "Нет токена HuggingFace (нужен для моделей диаризации pyannote).\n"
    "1) Токен: https://hf.co/settings/tokens\n"
    "2) Принять условия: https://hf.co/pyannote/speaker-diarization-3.1\n"
    "   и https://hf.co/pyannote/segmentation-3.0\n"
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


def diarize_wav(path: Path) -> list[tuple[float, float, str]]:
    """Интервалы (start, end, SPEAKER_XX) по записи."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit(_TOKEN_HELP)

    import torch
    from pyannote.audio import Pipeline

    print("Диаризация...")
    pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=token)
    pipe.to(torch.device("cuda"))
    waveform, rate = _load_wav(path)
    result = pipe({"waveform": waveform, "sample_rate": rate})
    annotation = getattr(result, "speaker_diarization", result)
    return [
        (turn.start, turn.end, label)
        for turn, _, label in annotation.itertracks(yield_label=True)
    ]


def assign_speakers(
    segments: list[Segment], turns: list[tuple[float, float, str]]
) -> None:
    """Каждому сегменту — спикер с максимальным перекрытием по времени (in-place)."""
    for seg in segments:
        best, best_overlap = None, 0.0
        for start, end, label in turns:
            overlap = min(seg.end, end) - max(seg.start, start)
            if overlap > best_overlap:
                best, best_overlap = label, overlap
        seg.speaker = best
