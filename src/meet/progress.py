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
что старое окно продолжает понимать события.

Шаг, который не понадобился (выравнивание после GigaAM, голоса без
диаризации), убирается из плана до начала: `steps` уменьшается, а `fraction`
только растёт — его вес просто перестаёт ждать.
"""

import time
from dataclasses import dataclass

from meet import events

# Веса шагов расшифровки: доли времени по замерам на 6-минутном фрагменте
# (docs/2026-09-30-cpu-profile-bench.md) — распознавание и диаризация основные.
WEIGHTS = {"convert": 5.0, "asr": 55.0, "align": 10.0, "diarize": 25.0, "voices": 3.0, "render": 2.0}


@dataclass
class Step:
    key: str  # уникален в плане: "asr-sys", "asr-mic"
    stage: str  # ступень (events.STAGE_LABELS)
    weight: float
    label: str | None = None
    note: str | None = None
    # Будет ли у шага своя шкала (update) — иначе в окне бегущий блик.
    measured: bool = False


class Stages:
    """План шагов и их ход. `update(part)` внутри шага прореживается: не чаще
    раза в MIN_GAP_S и не мельче MIN_PART доли шага — поток событий идёт в окно
    и в журнал, тысячи строк ему не нужны."""

    MIN_GAP_S = 0.5
    MIN_PART = 0.01

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

    # --- план --------------------------------------------------------------
    def drop(self, key: str) -> None:
        """Шаг не понадобится. Начатый или пройденный не трогаем."""
        if key in self._done or (self.current and self.current.key == key):
            return
        self.steps = [s for s in self.steps if s.key != key]

    def has(self, key: str) -> bool:
        return any(s.key == key for s in self.steps)

    def estimate(self, seconds: float | None) -> None:
        """Ожидаемая длительность всей работы (для «осталось ~N мин»)."""
        if seconds and seconds > 0:
            self.estimate_s = float(seconds)

    # --- ход ---------------------------------------------------------------
    def fraction(self) -> float:
        total = sum(s.weight for s in self.steps) or 1.0
        done = sum(s.weight for s in self.steps if s.key in self._done)
        if self.current is not None:
            done += self.current.weight * self.part
        return max(0.0, min(1.0, done / total))

    def begin(self, key: str, note: str | None = None) -> None:
        """Начался шаг `key` (предыдущий считается пройденным)."""
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
        self._emit()

    def update(self, part: float) -> None:
        """Доля 0…1 внутри текущего шага (не убывает)."""
        if self.current is None:
            return
        part = max(self.part, min(1.0, float(part)))
        self.part = part
        now = self.clock()
        if part >= 1.0 or (part - self._last_part >= self.MIN_PART and now - self._last_at >= self.MIN_GAP_S):
            self._emit()

    def finish(self, note: str | None = None) -> None:
        """Последний шаг пройден: доля 1."""
        if self.current is not None:
            self._done.add(self.current.key)
            if note is not None:
                self.current.note = note
            self.part = 1.0
        self._emit(final=True)

    def note(self, text: str | None) -> None:
        """Пояснение к текущему шагу (например, «диаризация пропущена»)."""
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
        if self.estimate_s:
            data["estimate_s"] = round(self.estimate_s, 1)
        self.bus.progress(
            step.stage,
            label=step.label or events.STAGE_LABELS.get(step.stage, step.stage),
            done=round(self.part, 4) if known else None,
            total=1 if known else None,
            note=step.note,
            **data,
        )
