import threading


class TranscriptBus:
    """Потокобезопасный append-only лог строк транскрипта.

    Продюсер — рабочий поток LiveEngine (publish); потребители (дайджестер,
    Q&A, SSE) читают со своей позиции через since() и никого не блокируют.
    Рядом со строкой лежит её структура `{"t", "speaker", "text"}`: её отдаёт
    SSE (`event: line`), чтобы клиенту не разбирать текст строки.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._entries: list[dict] = []
        self._lock = threading.Lock()

    def publish(self, line: str, entry: dict | None = None) -> None:
        if entry is None:  # строка без структуры — текстом, без времени
            entry = {"t": None, "speaker": "", "text": line}
        with self._lock:
            self._lines.append(line)
            self._entries.append(entry)

    def since(self, index: int) -> tuple[list[str], int]:
        with self._lock:
            return self._lines[index:], len(self._lines)

    def entries_since(self, index: int) -> tuple[list[dict], int]:
        with self._lock:
            return self._entries[index:], len(self._entries)

    def size(self) -> int:
        with self._lock:
            return len(self._lines)
