"""Пилот live-имён: симуляция живого пути на существующей записи.

Гонит sys-дорожку записи 20-секундными окнами через Transcriber + VoiceMatcher
(тот же путь, что LiveEngine.process_window, без аудиозахвата) и печатает
строки с именами, cos-скоры всех кандидатов, время эмбеддинга и пик VRAM.

Запуск (нужен HF_TOKEN и GPU):
    .venv/Scripts/python scripts/pilot_live_voices.py recordings/<папка>
"""

import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from meet.asr import Transcriber, drop_hallucinations
from meet.voice_id import SAMPLE_RATE, VoiceMatcher
from meet.voices import best_match

WINDOW_S = 20.0


def load_track_16k(folder: Path) -> np.ndarray:
    """sys-дорожка записи -> float32 mono 16k (ffmpeg декодирует opus/wav)."""
    src = next(p for ext in ("opus", "wav") for p in folder.glob(f"sys.{ext}"))
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-f", "f32le", "-ac", "1",
         "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def main() -> None:
    folder = Path(sys.argv[1])
    audio = load_track_16k(folder)
    print(f"дорожка: {len(audio) / SAMPLE_RATE:.0f} c")

    tr = Transcriber()
    tr.load()
    matcher = VoiceMatcher()
    matcher.load()
    if not matcher.enabled:
        raise SystemExit("база голосов пуста - пилоту нечего опознавать")

    import torch

    torch.cuda.reset_peak_memory_stats()
    embed_times: list[float] = []
    matched = unmatched = 0
    step = int(WINDOW_S * SAMPLE_RATE)
    last_text = None
    for w0 in range(0, len(audio), step):
        window = audio[w0:w0 + step]
        offset = w0 / SAMPLE_RATE
        segs = drop_hallucinations(
            tr.transcribe_window(window, offset_s=offset, initial_prompt=last_text)
        )
        if segs:
            last_text = segs[-1].text
        for s in segs:
            lo = max(0, int((s.start - offset) * SAMPLE_RATE))
            hi = min(len(window), int((s.end - offset) * SAMPLE_RATE))
            t0 = time.perf_counter()
            name = matcher.name_for(window[lo:hi])
            embed_times.append(time.perf_counter() - t0)
            # для диагностики — полные скоры (не только уверенный матч)
            detail = ""
            if hi - lo >= matcher.min_seconds * SAMPLE_RATE:
                emb = matcher._embed(window[lo:hi])
                if emb is not None and np.isfinite(emb).all():
                    score, cand, second = best_match(emb, matcher.base)
                    detail = f" [best {cand} {score:.2f}, second {second if second is None else round(second, 2)}]"
            matched += bool(name)
            unmatched += not name
            print(f"[{int(s.start) // 60:02d}:{int(s.start) % 60:02d}] "
                  f"{name or 'Собеседник'}: {s.text}{detail}")

    print(f"\nсегментов: {matched + unmatched}, с именем: {matched}")
    if embed_times:
        print(f"эмбеддинг: медиана {sorted(embed_times)[len(embed_times) // 2] * 1000:.0f} мс, "
              f"максимум {max(embed_times) * 1000:.0f} мс")
    print(f"пик VRAM (torch): {torch.cuda.max_memory_allocated() / 2**30:.2f} ГиБ")


if __name__ == "__main__":
    main()
