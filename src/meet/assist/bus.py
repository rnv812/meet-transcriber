import threading


class TranscriptBus:
    """Потокобезопасный append-only лог строк транскрипта.

    Продюсер — рабочий поток LiveEngine (publish); потребители (дайджестер,
    Q&A, SSE) читают со своей позиции через since() и никого не блокируют.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def publish(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)

    def since(self, index: int) -> tuple[list[str], int]:
        with self._lock:
            return self._lines[index:], len(self._lines)

    def size(self) -> int:
        with self._lock:
            return len(self._lines)
