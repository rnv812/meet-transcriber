import threading
import time
from collections import deque
from pathlib import Path


def fmt_hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def format_live_line(start_s: float, speaker: str, text: str) -> str:
    return f"[{fmt_hms(start_s)}] {speaker}: {text}"


class TrackBuffer:
    """Очередь аудио-чанков: callback докидывает push(), рабочий поток забирает
    drain(). Потокобезопасность — за счёт атомарности append/popleft у deque в
    CPython; явных локов нет, чтобы не блокировать аудио-callback."""

    def __init__(self) -> None:
        self._chunks: "deque[bytes]" = deque()

    def push(self, data: bytes) -> None:
        self._chunks.append(data)

    def drain(self) -> bytes:
        out: list[bytes] = []
        try:
            while True:
                out.append(self._chunks.popleft())
        except IndexError:
            pass
        return b"".join(out)


class LiveEngine:
    """Движок живого режима: callback пишет дорожки и копит аудио в буферы,
    рабочий поток окнами расшифровывает и дописывает live_transcript.md."""

    SPEAKERS = {"sys.wav": "Собеседник", "mic.wav": "Вы"}

    def __init__(self, out_dir, transcriber, window_seconds: float = 20.0,
                 hotwords: str | None = None) -> None:
        self.out_dir = Path(out_dir)
        self._transcriber = transcriber
        self.window_seconds = window_seconds
        self.hotwords = hotwords
        self._tracks: dict[str, dict] = {}
        self._transcript = self.out_dir / "live_transcript.md"
        self._out = None
        self._stop = threading.Event()
        self._worker: "threading.Thread | None" = None
        self._streams: list = []
        self._p = None

    def register_track(self, fname: str, rate: int, channels: int,
                       normalize: bool = False) -> None:
        self._tracks[fname] = {
            "buffer": TrackBuffer(),
            "rate": rate,
            "channels": channels,
            "elapsed": 0.0,
            "normalize": normalize,
            "gain": None,
        }

    def process_window(self) -> None:
        from meet.asr import drop_hallucinations
        from meet.audio import (
            apply_gain,
            compute_gain,
            pcm16_to_float32_mono,
            resample_to_16k,
        )

        for fname, tr in self._tracks.items():
            raw = tr["buffer"].drain()
            if not raw:
                continue
            mono = pcm16_to_float32_mono(raw, tr["channels"])
            audio = resample_to_16k(mono, tr["rate"])
            duration = len(audio) / 16000.0
            if tr["normalize"]:
                if tr["gain"] is None:
                    tr["gain"] = compute_gain(audio)
                audio = apply_gain(audio, tr["gain"])
            offset = tr["elapsed"]
            tr["elapsed"] = offset + duration  # двигаем по длительности захвата
            segs = drop_hallucinations(
                self._transcriber.transcribe_window(
                    audio, offset_s=offset, hotwords=self.hotwords
                )
            )
            speaker = self.SPEAKERS.get(fname, fname)
            for s in segs:
                self._write_line(format_live_line(s.start, speaker, s.text))

    def _write_line(self, line: str) -> None:
        if self._out is None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self._out = open(self._transcript, "a", encoding="utf-8")
        self._out.write(line + "\n")
        self._out.flush()
