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

import json
import math
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from meet import events

# Веса шагов расшифровки: доли времени по замерам на 6-минутном фрагменте
# (docs/2026-09-30-cpu-profile-bench.md) — распознавание и диаризация основные.
# "mic-voices" — голоса микрофона (meet.mic_split): эмбеддинг окон ~1 мин на час CPU (T0).
WEIGHTS = {"convert": 5.0, "asr": 55.0, "align": 10.0, "diarize": 25.0, "voices": 3.0, "render": 2.0,
           "mic-voices": 2.0}

# Время шагов, секунды: (постоянная часть — загрузка модели и пайплайна,
# доля от длительности звука шага). Когда движок известен, веса шагов — их
# ожидаемое время (`Stages.plan_times`): полоска идёт ровно по времени, а не
# пролетает быстрое распознавание GigaAM и не стоит на диаризации. Замеры CPU
# GigaAM: 60 с звука — распознавание 18 с на две дорожки; 6 мин — 30 с
# (docs/2026-09-30-cpu-profile-bench.md, проверка 0.3.1); Whisper — оттуда же.
# Диаризация на процессоре с 0.3.3 (модель с диска без сети, голоса за один
# проход на окно, 8 потоков): ~10 с загрузки и 0,12 с на секунду звука вместо
# 50 с и 0,27 (замер 0.3.3, .superpowers/sdd/v033/diar-speed-report.md).
STEP_TIMES: dict[tuple[str, str], dict[str, tuple[float, float]]] = {
    ("cpu", "gigaam"): {"convert": (0.5, 0.005), "asr": (8.0, 0.035), "align": (20.0, 0.3),
                        "diarize": (10.0, 0.12), "voices": (0.5, 0.0), "render": (0.5, 0.0)},
    ("cpu", "faster-whisper"): {"convert": (0.5, 0.005), "asr": (15.0, 0.45), "align": (10.0, 0.15),
                                "diarize": (10.0, 0.12), "voices": (0.5, 0.0), "render": (0.5, 0.0)},
    ("cuda", "gigaam"): {"convert": (0.5, 0.005), "asr": (4.0, 0.012), "align": (4.0, 0.03),
                         "diarize": (8.0, 0.04), "voices": (0.5, 0.0), "render": (0.5, 0.0)},
    ("cuda", "faster-whisper"): {"convert": (0.5, 0.005), "asr": (6.0, 0.06), "align": (4.0, 0.03),
                                 "diarize": (8.0, 0.04), "voices": (0.5, 0.0), "render": (0.5, 0.0)},
}
# Apple Silicon (MPS) — свой профиль: текст, выравнивание и голоса там на
# процессоре (как у cpu), а диаризация pyannote — на MPS, в разы быстрее
# процессора. Оценка 0.3.3 по чужим замерам (M1 Pro: 15 мин за ~57 с штатным
# pyannote, голоса втрое легче) — первые расшифровки поправит StepStats.
MPS_DIARIZE = (10.0, 0.05)
for _backend in ("gigaam", "faster-whisper"):
    STEP_TIMES[("mps", _backend)] = {**STEP_TIMES[("cpu", _backend)], "diarize": MPS_DIARIZE}


def _profile(device: str | None, backend: str | None, stage: str | None = None,
             guess: bool = False) -> tuple[str, str]:
    """Ключ профиля: устройство (cpu, cuda, mps) и движок. `guess` — устройство
    угадано до шага (оценка): на Mac диаризация идёт на MPS
    (diarize.pick_device), а расшифровка до неё знает только cuda/cpu, так что
    «cpu» у шага diarize там — профиль mps. Замер (`record`) знает настоящее
    устройство и пишет под ним: повтор на процессоре после сбоя MPS — в cpu."""
    if guess and device == "cpu" and stage == "diarize" and _on_mac():
        device = "mps"
    return (device if device in ("cpu", "cuda", "mps") else "cpu", "gigaam" if backend == "gigaam" else "faster-whisper")


# Версии ожиданий шага (ключ поправок StepStats): 0.3.3 — диаризация на
# процессоре втрое быстрее (STEP_TIMES (50, 0.27) → (10, 0.12)).
STATS_VERSIONS = {("cpu", "diarize"): 2}


def _on_mac() -> bool:
    from meet import plat

    return plat.is_macos()


def step_time(device: str | None, backend: str | None, stage: str, seconds: float,
              stats: "StepStats | None" = None) -> tuple[float, float]:
    """(загрузка, работа) шага `stage` над звуком длительностью `seconds`;
    `stats` — поправка по прошлым расшифровкам этой машины."""
    device, backend = _profile(device, backend, stage, guess=True)
    load, per_s = STEP_TIMES[(device, backend)].get(stage, (0.5, 0.0))
    load, work = load, per_s * max(0.0, seconds)
    if stats is not None:
        k_load, k_work = stats.factors(device, backend, stage)
        load, work = load * k_load, work * k_work
    return load, work


class StepStats:
    """Поправки времени шагов по прошлым работам на этой машине
    (`progress_stats.json` в папке данных): на ключ «устройство:движок:шаг» —
    последние KEEP отношений «факт / ожидание» для загрузки и для работы;
    поправка — их медиана (в пределах 0,2…5). Диск медленный или модель уже в
    памяти — следующая расшифровка идёт ровнее. Файл не читается или не
    пишется — работаем на таблице STEP_TIMES."""

    NAME = "progress_stats.json"
    KEEP = 12
    LIMITS = (0.2, 5.0)

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from meet import paths

            path = paths.data_dir() / self.NAME
        self.path = Path(path)
        self._data: dict | None = None

    def _load(self) -> dict:
        if self._data is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = raw if isinstance(raw, dict) else {}
            except (OSError, ValueError):
                self._data = {}
        return self._data

    @staticmethod
    def key(device: str, backend: str, stage: str) -> str:
        # Диаризация и прочие шаги не зависят от движка распознавания. Таблица
        # STEP_TIMES шага сменилась — новый ключ (`@версия`): старые отношения
        # считались от прежнего ожидания и сдвигали бы оценку ещё KEEP работ.
        version = STATS_VERSIONS.get((device, stage))
        return f"{device}:{backend if stage in ('asr', 'align') else 'any'}:{stage}" + (
            f"@{version}" if version else "")

    def factors(self, device: str, backend: str, stage: str) -> tuple[float, float]:
        runs = self._load().get(self.key(device, backend, stage))
        if not isinstance(runs, list):
            return 1.0, 1.0
        loads = [r[0] for r in runs if isinstance(r, list) and len(r) == 2 and r[0] is not None]
        works = [r[1] for r in runs if isinstance(r, list) and len(r) == 2 and r[1] is not None]
        lo, hi = self.LIMITS
        pick = (lambda xs: min(hi, max(lo, median(xs))) if xs else 1.0)
        return pick(loads), pick(works)

    def record(self, device: str | None, backend: str | None, stage: str,
               expected: tuple[float, float], actual: tuple[float | None, float]) -> None:
        """Замер шага: ожидалось (загрузка, работа) — вышло. Отношения — только
        у частей, ожидание которых не меньше секунды (иначе шум)."""
        device, backend = _profile(device, backend, stage)
        ratio = [a / e if a is not None and e >= 1.0 else None for e, a in zip(expected, actual)]
        if ratio == [None, None]:
            return
        lo, hi = self.LIMITS
        ratio = [None if r is None else round(min(hi * 2, max(lo / 2, r)), 3) for r in ratio]
        data = self._load()
        key = self.key(device, backend, stage)
        runs = data.get(key) if isinstance(data.get(key), list) else []
        data[key] = [*runs, ratio][-self.KEEP:]

    def save(self) -> None:
        if not self._data:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass


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
    # Ожидаемое время шага (`plan_times`): загрузка (до первого отчёта своей
    # шкалы — по времени) и работа (по своей шкале).
    load_s: float = 0.0
    work_s: float = 0.0


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
        self._started = clock()
        # Замер шагов: ключ → [начало, первый отчёт своей шкалы, конец].
        self._marks: dict[str, list[float | None]] = {}
        # Предупреждение на всю работу (`warn`): уходит с каждым событием хода.
        self.warning: str | None = None

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

    def measured(self) -> dict[str, tuple[Step, tuple[float | None, float]]]:
        """Пройденные шаги с планом времени: {ключ: (шаг, (загрузка, работа))}.
        Загрузка — до первого отчёта своей шкалы (None, если её нет)."""
        out = {}
        for step in self.steps:
            mark = self._marks.get(step.key)
            if not mark or mark[2] is None or step.load_s + step.work_s <= 0:
                continue
            begun, first, ended = mark
            if step.measured and first is not None:
                out[step.key] = (step, (first - begun, ended - first))
            else:
                out[step.key] = (step, (None, ended - begun))
        return out

    def plan_times(self, times: dict[str, tuple[float, float]]) -> None:
        """Ожидаемое время шагов {ключ: (загрузка, работа)}: вес ещё не
        начатых шагов (и текущего, пока он без хода) — по их времени, сумма
        их весов прежняя (пройденная доля не меняется); оценка всей работы —
        прошедшее время плюс ожидаемое оставшихся шагов."""
        with self._lock:
            for step in self.steps:
                if step.key in times:
                    step.load_s, step.work_s = (max(0.0, float(x)) for x in times[step.key])
            fresh = self.current is not None and self.part == 0 and not self._real
            pending = [s for s in self.steps if s.key in times and s.key not in self._done
                       and (self.current is None or s.key != self.current.key or fresh)]
            self.reweight({s.key: s.load_s + s.work_s for s in pending}, include_current=fresh)
            left = sum(s.load_s + s.work_s for s in pending)
            self.estimate(max(0.0, self.clock() - self._started) + left)

    def reweight(self, weights: dict[str, float], include_current: bool = False) -> None:
        """Перераспределить вес между ещё не начатыми шагами (распознавание
        двух дорожек — по их длительности). Сумма весов этих шагов не
        меняется — значит, не меняется и пройденная доля (`fraction`)."""
        with self._lock:
            pending = [s for s in self.steps if s.key in weights and s.key not in self._done
                       and (self.current is None or s.key != self.current.key or include_current)]
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
        """Ожидаемая длительность шага: по плану времени (`plan_times`), иначе
        доля его веса в оценке всей работы."""
        step = step or self.current
        if step is not None and step.load_s + step.work_s > 0:
            return step.load_s + step.work_s
        if step is None or not self.estimate_s:
            return None
        total = sum(s.weight for s in self.steps) or 1.0
        return self.estimate_s * step.weight / total

    def begin(self, key: str, note: str | None = None) -> None:
        """Начался шаг `key` (предыдущий считается пройденным)."""
        with self._lock:
            now = self.clock()
            if self.current is not None:
                self._done.add(self.current.key)
                self._marks.get(self.current.key, [None, None, None])[2] = now
            step = next((s for s in self.steps if s.key == key), None)
            if step is None:  # шага нет в плане — добавляем в конец, не теряя событие
                step = Step(key, key, 0.0)
                self.steps.append(step)
            if note is not None:
                step.note = note
            self.current = step
            self.part = 0.0
            self._real = False
            self._begun_at = now
            self._marks[key] = [now, None, None]
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
            if not self._real and self.current.key in self._marks:
                self._marks[self.current.key][1] = self.clock()
            self._real = True
            # Своя шкала — работа после загрузки: 0 своей шкалы = конец загрузки.
            lead = self._lead(self.current)
            part = max(self.part, min(1.0, lead + (1 - lead) * float(part)))
            self.part = part
            now = self.clock()
            if part >= 1.0 or (part - self._last_part >= self.MIN_PART and now - self._last_at >= self.MIN_GAP_S):
                self._emit()

    def _by_time(self, step: Step) -> bool:
        return (not step.measured or step.key in self._timed) and not self._real

    @staticmethod
    def _lead(step: Step) -> float:
        """Доля загрузки в шаге со своей шкалой (до первого её отчёта)."""
        total = step.load_s + step.work_s
        return step.load_s / total if step.measured and total > 0 else 0.0

    def tick(self) -> None:
        """Продлить по времени шаг без своей шкалы: доля `soft(прошло /
        ожидаемое)` — к ~90 % за ожидаемое время, дальше медленно и не до
        конца. Оценки длительности нет — ничего (в окне бегущий блик)."""
        with self._lock:
            step = self.current
            if step is None or self._finished:
                return
            elapsed = self.clock() - self._begun_at
            if self._by_time(step):
                expect = self.expected_s(step)
                if not expect:
                    return
                part = soft(elapsed / expect)
            elif not self._real and self._lead(step) > 0:
                # Шаг со своей шкалой, а она ещё молчит (грузится модель): по
                # времени загрузки, но не дальше её доли в шаге.
                part = self._lead(step) * soft(elapsed / step.load_s)
            else:
                return
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
                self._marks.get(self.current.key, [None, None, None])[2] = self.clock()
                if note is not None:
                    self.current.note = note
                self.part = 1.0
            self._finished = True
            self._emit(final=True)

    def warn(self, text: str | None) -> None:
        """Предупреждение на всю работу, а не на шаг (например, «Распознаётся
        на процессоре: видеокарта NVIDIA не найдена»): поле `warning` каждого
        следующего события хода — окно показывает его под полоской."""
        with self._lock:
            self.warning = text
            self._emit()

    def add_warning(self, text: str) -> None:
        """Ещё одно предупреждение к уже сказанному (через « · »): например,
        текст на процессоре, и диаризация тоже."""
        with self._lock:
            if self.warning and text in self.warning:
                return
            self.warning = f"{self.warning} · {text}" if self.warning else text
            self._emit()

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
            timed = self.part > 0 and (self._by_time(step) or (not self._real and self._lead(step) > 0))
            unit = "time" if timed else step.unit
            if unit:
                data["unit"] = unit
        if self.estimate_s:
            data["estimate_s"] = round(self.estimate_s, 1)
        if final:
            data["final"] = True  # конец работы: консоль печатает эту строку (путь результата)
        if self.warning:
            data["warning"] = self.warning
        self.bus.progress(
            step.stage,
            label=step.label or events.STAGE_LABELS.get(step.stage, step.stage),
            done=round(self.part, 4) if known else None,
            total=1 if known else None,
            note=step.note,
            **data,
        )
