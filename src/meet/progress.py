"""Ход многоэтапной работы одной шкалой: расшифровка, переразделение на спикеров.

Раньше каждая ступень сообщала свой `done/total` (по дорожкам), а у части
ступеней (выравнивание, диаризация) шкалы не было вовсе: полоска в окне
прыгала 0→100→0, а на «Выравнивании» стояла полной, будто всё готово.

Здесь работа — план шагов с весами (доля времени на типичной встрече). Каждое
событие `progress` несёт, кроме прежних полей:

* `step`/`steps` — номер шага с 1 и их число («Этап 2 из 5»);
* `fraction` — общая доля 0…1 с весами шагов, не убывает;
* `estimate_s` — ожидаемая длительность всей работы, секунд (по
  `engine.SPEED_FACTOR` и длительности записи), если известна.

`done/total` внутри шага остаются (0…1 или None — у шага нет своей шкалы), так
что старое окно продолжает понимать события. Ещё (0.3.1):

* `cap` — доля в конце текущего шага: следующая известная отметка. Окно
  продлевает полоску между событиями по скорости хода, но не дальше неё;
* `unit` — в чём меряется ход шага («audio_s» — секунды звука, «time» —
  оценка по времени: у шага своей шкалы нет).

Шаг без своей шкалы (конвертация, сопоставление голосов, диаризация без
`hook`) идёт по времени (`tick`, поток `ticking()`): ожидаемая длительность
шага — `estimate_s` × его доля веса; полоска подходит к ~90 % шага и полной
не становится, пока шаг не кончился (`soft`).

План решается до первого события: выравнивание после GigaAM в него не входит
(`transcribe._align_planned`). Шаг, ненужность которого выяснилась уже по ходу
(голоса без диаризации, выравнивание при языке «auto»), пропускается
(`skip`): число этапов во время работы не меняется никогда, а `fraction`
только растёт.
"""

import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from meet import events

# Веса шагов расшифровки: доли времени по замерам на 6-минутном фрагменте
# (docs/2026-09-30-cpu-profile-bench.md) — распознавание и диаризация основные.
WEIGHTS = {"convert": 5.0, "asr": 55.0, "align": 10.0, "diarize": 25.0, "voices": 3.0, "render": 2.0}

# Диаризация (pyannote) отдельно, «время / длительность звука» — для хода по
# времени, когда пайплайн не сообщает своего (`hook`), и для оценки
# «Переразделить на спикеров». Оценка с запасом: на CPU сегментация и голоса
# идут порядка десятой доли реального времени.
DIARIZE_FACTOR = {"cuda": 0.04, "cpu": 0.15}


def diarize_estimate(seconds: float, device: str | None) -> float:
    """Ожидаемое время диаризации звука длительностью `seconds`."""
    return max(0.0, seconds) * DIARIZE_FACTOR.get(device or "cpu", DIARIZE_FACTOR["cpu"])


def soft(x: float, edge: float = 0.9, top: float = 0.95) -> float:
    """Доля по оценке: `x` — сколько прошло от ожидаемого (время или объём).

    До ожидаемого — ровно (`edge` × x): ход честный, без рывков; дальше —
    медленно к `top` и никогда не 1: оценка не знает, когда работа кончится,
    «готово» скажет только сама работа. Монотонна и непрерывна."""
    if x <= 0:
        return 0.0
    if x <= 1:
        return edge * x
    return edge + (top - edge) * (1 - math.exp(-(x - 1)))


@dataclass
class Step:
    key: str  # уникален в плане: "asr-sys", "asr-mic"
    stage: str  # ступень (events.STAGE_LABELS)
    weight: float
    label: str | None = None
    note: str | None = None
    # Будет ли у шага своя шкала (update) — иначе ход по времени (tick) или,
    # без оценки длительности, бегущий блик в окне.
    measured: bool = False
    # В чём меряется своя шкала шага (для окна): «audio_s», «segments»…
    unit: str | None = None


class Stages:
    """План шагов и их ход. `update(part)` внутри шага прореживается: не чаще
    раза в MIN_GAP_S (не больше 4 событий в секунду) и не мельче MIN_PART доли
    шага — поток событий идёт в окно и в журнал, тысячи строк ему не нужны.

    Методы зовут и основной поток, и поток часов (`ticking`) — под замком."""

    MIN_GAP_S = 0.25
    MIN_PART = 0.005
    # Как часто поток часов продлевает шаг без своей шкалы.
    TICK_S = 0.5

    def __init__(self, bus, steps: list[Step], clock=time.monotonic) -> None:
        self.bus = bus
        self.steps = list(steps)
        self.clock = clock
        self.current: Step | None = None
        self.part = 0.0
        self.estimate_s: float | None = None
        self._done: set[str] = set()
        self._last_at = 0.0
        self._last_part = 0.0
        self._lock = threading.RLock()
        self._begun_at = 0.0
        # Шаг сообщил свой ход (update) — по времени его больше не продлеваем.
        self._real = False
        # Шаги из плана «со шкалой», сказавшие, что шкалы не будет (update(None)).
        self._timed: set[str] = set()
        self._finished = False
        self._ticker = None

    # --- план --------------------------------------------------------------
    def drop(self, key: str) -> None:
        """Шаг не понадобится. Начатый или пройденный не трогаем."""
        if key in self._done or (self.current and self.current.key == key):
            return
        self.steps = [s for s in self.steps if s.key != key]

    def has(self, key: str) -> bool:
        return any(s.key == key for s in self.steps)

    def skip(self, key: str) -> None:
        """Шаг оказался не нужен, когда план уже показан: он считается
        пройденным, число этапов не меняется (следующий этап просто идёт под
        своим номером). События нет — его отметит следующий `begin`."""
        if key in self._done or (self.current and self.current.key == key) or not self.has(key):
            return
        self._done.add(key)

    def estimate(self, seconds: float | None) -> None:
        """Ожидаемая длительность всей работы (для «осталось ~N мин»)."""
        if seconds and seconds > 0:
            self.estimate_s = float(seconds)

    def reweight(self, weights: dict[str, float]) -> None:
        """Перераспределить вес между ещё не начатыми шагами (распознавание
        двух дорожек — по их длительности). Сумма весов этих шагов не
        меняется — значит, не меняется и пройденная доля (`fraction`)."""
        with self._lock:
            pending = [s for s in self.steps if s.key in weights and s.key not in self._done
                       and (self.current is None or s.key != self.current.key)]
            new = sum(max(0.0, float(weights[s.key])) for s in pending)
            old = sum(s.weight for s in pending)
            if not pending or new <= 0 or old <= 0:
                return
            for s in pending:
                s.weight = old * max(0.0, float(weights[s.key])) / new

    # --- ход ---------------------------------------------------------------
    def fraction(self, part: float | None = None) -> float:
        """Общая доля; `part` — доля текущего шага (по умолчанию достигнутая)."""
        total = sum(s.weight for s in self.steps) or 1.0
        done = sum(s.weight for s in self.steps if s.key in self._done)
        if self.current is not None:
            done += self.current.weight * (self.part if part is None else part)
        return max(0.0, min(1.0, done / total))

    def expected_s(self, step: Step | None = None) -> float | None:
        """Ожидаемая длительность шага: доля его веса в оценке всей работы."""
        step = step or self.current
        if step is None or not self.estimate_s:
            return None
        total = sum(s.weight for s in self.steps) or 1.0
        return self.estimate_s * step.weight / total

    def begin(self, key: str, note: str | None = None) -> None:
        """Начался шаг `key` (предыдущий считается пройденным)."""
        with self._lock:
            if self.current is not None:
                self._done.add(self.current.key)
            step = next((s for s in self.steps if s.key == key), None)
            if step is None:  # шага нет в плане — добавляем в конец, не теряя событие
                step = Step(key, key, 0.0)
                self.steps.append(step)
            if note is not None:
                step.note = note
            self.current = step
            self.part = 0.0
            self._real = False
            self._begun_at = self.clock()
            self._emit()

    def update(self, part: float | None) -> None:
        """Доля 0…1 внутри текущего шага (не убывает). None — «своей шкалы у
        шага не будет» (диаризация без `hook`): дальше он идёт по времени."""
        with self._lock:
            if self.current is None:
                return
            if part is None:
                self._timed.add(self.current.key)
                return
            self._real = True
            part = max(self.part, min(1.0, float(part)))
            self.part = part
            now = self.clock()
            if part >= 1.0 or (part - self._last_part >= self.MIN_PART and now - self._last_at >= self.MIN_GAP_S):
                self._emit()

    def _by_time(self, step: Step) -> bool:
        return (not step.measured or step.key in self._timed) and not self._real

    def tick(self) -> None:
        """Продлить по времени шаг без своей шкалы: доля `soft(прошло /
        ожидаемое)` — к ~90 % за ожидаемое время, дальше медленно и не до
        конца. Оценки длительности нет — ничего (в окне бегущий блик)."""
        with self._lock:
            step = self.current
            if step is None or self._finished or not self._by_time(step):
                return
            expect = self.expected_s(step)
            if not expect:
                return
            part = soft((self.clock() - self._begun_at) / expect)
            if part - self.part < self.MIN_PART:
                return
            self.part = part
            self._emit()

    def start_ticking(self, interval: float | None = None) -> None:
        """Поток часов: шаги без своей шкалы идут по времени (`tick`)."""
        if self._ticker is not None:
            return
        stop = threading.Event()
        gap = self.TICK_S if interval is None else interval

        def loop() -> None:
            while not stop.wait(gap):
                try:
                    self.tick()
                except Exception:
                    pass  # ход — подсказка: сбой часов работу не роняет

        thread = threading.Thread(target=loop, name="meet-progress", daemon=True)
        self._ticker = (stop, thread)
        thread.start()

    def stop_ticking(self) -> None:
        ticker, self._ticker = self._ticker, None
        if ticker is not None:
            ticker[0].set()
            ticker[1].join(timeout=2)

    @contextmanager
    def ticking(self, interval: float | None = None):
        """Поток часов на время работы (см. `start_ticking`)."""
        self.start_ticking(interval)
        try:
            yield self
        finally:
            self.stop_ticking()

    def finish(self, note: str | None = None) -> None:
        """Последний шаг пройден: доля 1."""
        with self._lock:
            if self.current is not None:
                self._done.add(self.current.key)
                if note is not None:
                    self.current.note = note
                self.part = 1.0
            self._finished = True
            self._emit(final=True)

    def note(self, text: str | None) -> None:
        """Пояснение к текущему шагу (например, «диаризация пропущена»)."""
        with self._lock:
            if self.current is not None:
                self.current.note = text
                self._emit()

    def _emit(self, final: bool = False) -> None:
        step = self.current
        if step is None:
            return
        index = next((i for i, s in enumerate(self.steps) if s.key == step.key), len(self.steps) - 1)
        known = step.measured or final or self.part > 0
        self._last_at = self.clock()
        self._last_part = self.part
        data = {"step": index + 1, "steps": len(self.steps), "fraction": round(self.fraction(), 4)}
        if not final:
            # Следующая известная отметка — конец шага: дальше неё окно полоску не продлевает.
            data["cap"] = round(self.fraction(1.0), 4)
            unit = "time" if self._by_time(step) and self.part > 0 else step.unit
            if unit:
                data["unit"] = unit
        if self.estimate_s:
            data["estimate_s"] = round(self.estimate_s, 1)
        if final:
            data["final"] = True  # конец работы: консоль печатает эту строку (путь результата)
        self.bus.progress(
            step.stage,
            label=step.label or events.STAGE_LABELS.get(step.stage, step.stage),
            done=round(self.part, 4) if known else None,
            total=1 if known else None,
            note=step.note,
            **data,
        )
