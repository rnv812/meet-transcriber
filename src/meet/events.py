"""События записи и расшифровки как данные, а не как print в stdout.

Зачем: под треем stdout'а нет (ровно поэтому существует `record.log`), а UI
обязан показывать ступень расшифровки, уровни дорожек и то, что вотчдог сделал
с записью. Печать остаётся на месте — она документированный интерфейс CLI, — а
рядом появляется поток структурированных событий: его читают панель, экран
диагностики и `events.jsonl` рядом с записью.

Три сущности:

* `Event` — что случилось: вид, стенное время, произвольные данные;
* `EventBus` — раздача подписчикам; сбой подписчика не мешает издателю;
* `JsonlSink` — построчный JSON в файл, best effort (как `_RecordLog`).

Время — стенное (`time.time`), а не монотонное: события сопоставляются со
строками `record.log` и с именем папки записи, то есть с часами человека.

IMPORTANT: `publish()` зовут поток вотчдога записи, поток расшифровки и
веб-слой, но **никогда** аудио-callback: там нельзя ни блокироваться, ни
аллоцировать лишнего. Уровни дорожек поэтому не публикуются из callback'а, а
снимаются опросом (`recorder._Track.take_level`).
"""

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# Виды событий. Строки, а не enum: они уходят в JSON и в SSE, где всё равно
# станут строками, а лишний слой конвертации только мешает читать логи.
RECORD_STARTED = "record.started"
RECORD_STOPPED = "record.stopped"
RECORD_DISCARDED = "record.discarded"  # запись отменена и папка удалена
RECORD_DEVICE = "record.device"  # дорожка открыта/переоткрыта/миграция
RECORD_WAITING = "record.waiting"  # устройства нет, пауза уйдёт в тишину
# выбранного в настройках устройства нет — дорожка пишет с системного
RECORD_DEVICE_FALLBACK = "record.device_fallback"
# выбранное устройство снова доступно — дорожка вернулась на него
RECORD_DEVICE_PINNED = "record.device_pinned"
RECORD_SILENCE = "record.silence"  # долив тишины по стенным часам
# macOS: звук собеседников не пишется (state "missing": нет разрешения «Запись
# экрана» или помощника; запись идёт с микрофона) или снова пишется ("restored")
RECORD_SYSTEM_AUDIO = "record.system_audio"
RECORD_LEVEL = "record.level"  # уровни дорожек, только для живого UI
LOG = "log"  # строка журнала записи (дубль record.log)
PROGRESS = "progress"  # ступень расшифровки
# Текст расшифровки записан до спикеров (transcript.json, `phase: "text"`):
# {"path": папка записи}. Окно перечитывает карточку, не дожидаясь конца задачи.
TRANSCRIPT_TEXT = "transcript.text"
ERROR = "error"

# Ступени расшифровки в порядке прохождения. Ключ — машинный, ярлык — для UI.
STAGE_LABELS = {
    "convert": "конвертация дорожек",
    "asr": "распознавание",
    "align": "выравнивание по словам",
    "diarize": "диаризация",
    "voices": "сопоставление голосов",
    "render": "сборка транскрипта",
}
STAGES = tuple(STAGE_LABELS)


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "at": self.at, **self.data}

    def to_json(self) -> str:
        # ensure_ascii=False — не экранировать кириллицу (как в assist/web.py)
        return json.dumps(self.to_dict(), ensure_ascii=False)


class EventBus:
    """Раздаёт события подписчикам. Подписчик — любой вызываемый объект.

    Исключение подписчика не должно ломать издателя: запись и расшифровка не
    падают из-за того, что кто-то закрыл вкладку или сломался файл журнала.
    Счётчик `failures` — диагностический; он инкрементируется без лока, потому
    что точность здесь не нужна, а лок на горячем пути — нужен ещё меньше.
    """

    def __init__(self) -> None:
        self._subs: list = []
        self._lock = threading.Lock()
        self.failures = 0

    def subscribe(self, callback):
        """Подписать; возвращает функцию отписки (её зовёт SSE при разрыве)."""
        with self._lock:
            self._subs.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subs:
                    self._subs.remove(callback)

        return unsubscribe

    def subscribers(self) -> int:
        """Сколько подписчиков сейчас: панель, окно настроек, файловый журнал.
        Нужно и диагностике, и тестам — чтобы видеть, что отписка сработала."""
        with self._lock:
            return len(self._subs)

    def publish(self, event: Event) -> Event:
        # Снимок под локом, вызовы — вне: подписчик пишет в файл или в сокет, и
        # держать на этом лок значило бы блокировать вотчдог записи.
        with self._lock:
            subs = tuple(self._subs)
        for callback in subs:
            try:
                callback(event)
            except Exception:
                self.failures += 1
        return event

    def emit(self, kind: str, **data) -> Event:
        return self.publish(Event(kind, data))

    def progress(
        self,
        stage: str,
        *,
        label: str | None = None,
        done: float | None = None,
        total: float | None = None,
        note: str | None = None,
        **data,
    ) -> Event:
        """Ступень работы. `done`/`total` необязательны: у части ступеней
        (диаризация) прогресса внутри нет, и честнее не показывать шкалу, чем
        показывать выдуманную. `label` — для ступеней вне пайплайна расшифровки
        (например, установки движка), у которых своего ярлыка в STAGE_LABELS
        нет и быть не должно."""
        return self.emit(
            PROGRESS,
            stage=stage,
            label=label or STAGE_LABELS.get(stage, stage),
            done=done,
            total=total,
            note=note,
            **data,
        )


class JsonlSink:
    """Подписчик, пишущий события построчным JSON.

    Уровни дорожек по умолчанию не пишутся: они идут дважды в секунду и за
    двухчасовую встречу дали бы десятки тысяч строк ни о чём — это данные для
    живого метра, а не для истории.

    Ошибки записи глотаются: журнал не должен ронять запись (тот же принцип,
    что у `_RecordLog`).
    """

    def __init__(self, path: Path, skip: tuple[str, ...] = (RECORD_LEVEL,)) -> None:
        self.skip = skip
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # buffering=1: строка оказывается на диске сразу, при жёстком
            # убийстве процесса теряется максимум последняя
            self._f = open(path, "a", encoding="utf-8", buffering=1)
        except OSError:
            self._f = None

    def __call__(self, event: Event) -> None:
        if self._f is None or event.kind in self.skip:
            return
        try:
            self._f.write(event.to_json() + "\n")
        except (OSError, ValueError):  # ValueError — запись в закрытый файл
            pass

    def close(self) -> None:
        f, self._f = self._f, None
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
