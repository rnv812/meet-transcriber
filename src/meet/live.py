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
