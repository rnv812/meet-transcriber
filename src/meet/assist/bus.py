import secrets
import threading

from meet.assist.notify import Notifier
from meet.live import relabel_line


class TranscriptBus:
    """Потокобезопасный лог строк транскрипта: строки только добавляются, а
    подпись голоса и видимость строки могут смениться задним числом.

    Продюсер — рабочий поток LiveEngine (publish); потребители (дайджестер,
    Q&A, SSE) читают со своей позиции через since() и никого не блокируют.
    Рядом со строкой лежит её структура `{"t", "end", "speaker", "text",
    "voice"?}`: её отдаёт SSE (`event: line`), чтобы клиенту не разбирать
    текст строки. Номер строки — её позиция в шине (`id:` в SSE).

    `voice` — ключ голоса строки (meet.live_voices): имя голоса может прийти
    позже, когда набралось речи. `relabel(voice, speaker)` переподписывает его
    строки, `hide(ids)` прячет строки-дубли; `voices()` — всё это одним
    состоянием (`rev` растёт с каждой переменой) для SSE `event: voices`.
    Записи не правятся на месте — заменяются копией: другой поток может как
    раз сериализовать прежнюю.

    `session` — метка этой шины (этого ассистента): номера строк и спрятанные
    у нового ассистента в той же записи начинаются заново, окно по метке
    отличает их от прежних.

    Каждая новая строка и перемена — сигнал `changed` (`Notifier`): тикер
    подсказок и SSE ждут его, а не опрашивают шину по таймеру. `changed`
    можно передать свой — общий сигнал ассистента (строки, сводка, ответы).
    """

    def __init__(self, changed: Notifier | None = None) -> None:
        self._lines: list[str] = []
        self._entries: list[dict] = []
        self._lock = threading.Lock()
        self._voices: dict[str, str] = {}
        self._hidden: set[int] = set()
        self._voices_rev = 0
        self.session = secrets.token_hex(4)
        self.changed = changed if changed is not None else Notifier()

    def publish(self, line: str, entry: dict | None = None) -> int:
        """Добавить строку. → её номер (по нему её потом можно спрятать)."""
        if entry is None:  # строка без структуры — текстом, без времени
            entry = {"t": None, "speaker": "", "text": line}
        with self._lock:
            voice = entry.get("voice")
            speaker = self._voices.get(voice) if voice else None
            if speaker is not None and speaker != entry.get("speaker"):
                # Голос переименовали, пока строка ждала публикации.
                line = relabel_line(line, str(entry.get("speaker") or ""), speaker)
                entry = {**entry, "speaker": speaker}
            self._lines.append(line)
            self._entries.append(entry)
            index = len(self._lines) - 1
        self.changed.notify()
        return index

    def relabel(self, voice: str, speaker: str) -> int:
        """Голос `voice` теперь подписан `speaker`: его строки — тоже. →
        сколько строк переподписано."""
        changed = 0
        with self._lock:
            if self._voices.get(voice) == speaker:
                return 0
            self._voices[voice] = speaker
            self._voices_rev += 1
            for i, entry in enumerate(self._entries):
                if entry.get("voice") != voice or entry.get("speaker") == speaker:
                    continue
                self._lines[i] = relabel_line(self._lines[i], str(entry.get("speaker") or ""), speaker)
                self._entries[i] = {**entry, "speaker": speaker}
                changed += 1
        self.changed.notify()
        return changed

    def hide(self, ids) -> bool:
        """Спрятать строки (дубли) по номерам. → что-то изменилось."""
        with self._lock:
            fresh = {int(i) for i in ids if 0 <= int(i) < len(self._entries)} - self._hidden
            if not fresh:
                return False
            self._hidden |= fresh
            self._voices_rev += 1
        self.changed.notify()
        return True

    def voices(self) -> tuple[int, dict[str, str], list[int]]:
        """(rev, {голос: подпись}, номера спрятанных строк) — состояние, не дельта."""
        with self._lock:
            return self._voices_rev, dict(self._voices), sorted(self._hidden)

    def hidden(self) -> set[int]:
        with self._lock:
            return set(self._hidden)

    def visible_since(self, index: int) -> list[str]:
        """Строки с номера `index` без спрятанных дублей."""
        with self._lock:
            return [line for i, line in enumerate(self._lines[index:], start=index)
                    if i not in self._hidden]

    def since(self, index: int) -> tuple[list[str], int]:
        with self._lock:
            return self._lines[index:], len(self._lines)

    def entries_since(self, index: int) -> tuple[list[dict], int]:
        with self._lock:
            return self._entries[index:], len(self._entries)

    def size(self) -> int:
        with self._lock:
            return len(self._lines)


def chronological(lines: list[str], entries: list[dict]) -> tuple[list[str], list[dict]]:
    """Реплики по времени (устойчиво): догнанное начало встречи приходит в
    шину позже живых реплик. Реплика без времени идёт за предыдущей."""
    if not any(e.get("catchup") for e in entries):
        return lines, entries
    keyed = []
    last = 0.0
    for i, (line, entry) in enumerate(zip(lines, entries)):
        t = entry.get("t")
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            last = float(t)
        keyed.append((last, i, line, entry))
    keyed.sort(key=lambda item: (item[0], item[1]))
    return [k[2] for k in keyed], [k[3] for k in keyed]
