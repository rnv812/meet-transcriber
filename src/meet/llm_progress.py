"""Ход задач модели одной шкалой: анализ встречи (по окнам), итоги, улучшение
расшифровки, профиль человека с проверкой, вопрос по записи.

Раньше эти задачи не сообщали ничего: в окне минутами бежал блик, и было не
понять, идёт ли работа и сколько ждать. Здесь задача — план частей (окна
анализа и итоговый вызов; профиль и его проверка), а внутри части — вызов
модели с подшагами:

* `request` — запрос отправлен, ответа ещё нет;
* `generating` — модель пишет ответ (поток кусков текста: Claude Code);
* `validating` — ответ пришёл, разбирается;
* `repair` — ответ битый, модель просят исправить.

Сколько это займёт — по прошлым вызовам (`Stats`, файл `llm_stats.json` в
папке данных: размер входа → длительность и размер ответа, скользящая медиана
отношения к затравке `SEEDS`). Есть поток текста — ход по полученным символам
против ожидаемого размера ответа; нет (Codex, локальная модель) — по времени
против ожидаемой длительности. Оценка не говорит «готово» никогда (`soft`:
к ~90 % за ожидаемое, дальше медленно до 95 %); вышло дольше ожидаемого —
`slow` («Дольше обычного…» в окне), полоска не откатывается.

Событие `progress` (как у расшифровки, meet.progress) несёт: `fraction` —
общую долю, `cap` — дальше неё окно полоску между событиями не продлевает,
`part`/`parts` и `note` («окно 2 из 4»), `phase`, `slow`, `eta_s` — сколько
осталось, секунд (только когда оценка уверенная), `unit` — «chars» (поток)
или «time» (по времени).

Связь с кодом задач — через шину: job_worker вешает `Tracker` на шину
(`attach`) и оборачивает runner (`Tracker.wrap`); модули задач зовут `plan` и
`part` с шиной. Без трекера (CLI, тесты) `part` шлёт прежнее грубое событие.
В журнал и события — только числа: ни текста встречи, ни ответа модели.
"""

from __future__ import annotations

import inspect
import json
import os
import threading
import time
from pathlib import Path
from statistics import median

from meet.progress import soft

STATS_NAME = "llm_stats.json"
# Сколько последних вызовов помнить на вид задачи и провайдера.
KEEP = 20
# С какого числа замеров оценка «уверенная» (без потока текста): показываем
# «осталось ~N с».
CONFIDENT_RUNS = 3

# Затравка до первых замеров, по виду вызова: (секунды на старт, секунды на
# 1000 символов входа, символов ответа на старт, символов ответа на 1000
# символов входа). Порядок величин — Claude Code sonnet на встречах ~1 ч.
SEEDS: dict[str, tuple[float, float, float, float]] = {
    "analyze": (20.0, 1.6, 600.0, 140.0),
    "analyze-final": (10.0, 1.0, 300.0, 20.0),
    "improve": (15.0, 1.0, 300.0, 30.0),
    "summary": (25.0, 0.6, 2500.0, 15.0),
    "ask": (10.0, 0.4, 600.0, 5.0),
    "profile": (30.0, 1.0, 1500.0, 60.0),
    "profile-check": (10.0, 1.0, 200.0, 150.0),
}
_DEFAULT_SEED = (20.0, 1.0, 1000.0, 50.0)

# Доля части на первый вызов; разбор ответа — до VALIDATED; исправление —
# от VALIDATED до REPAIRED. Конец части (1) — только когда она кончилась.
VALIDATED = 0.9
REPAIRED = 0.98

PHASES = ("request", "generating", "validating", "repair")


def seed(key: str, chars: int) -> tuple[float, float]:
    """(секунды, символов ответа) по затравке."""
    base_s, per_s, base_out, per_out = SEEDS.get(key, _DEFAULT_SEED)
    k = max(0, chars) / 1000
    return base_s + per_s * k, base_out + per_out * k


class Stats:
    """Прошлые вызовы модели: `{ключ: [{"in", "s", "out"}, …]}` в
    `llm_stats.json`. Ожидание — затравка × медиана отношений «факт /
    затравка» последних KEEP вызовов: и скорость провайдера, и привычный
    размер ответа учатся сами. Файл битый или не пишется — работаем на
    затравке, задачу это не роняет."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            from meet import paths

            path = paths.data_dir() / STATS_NAME
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
    def key(kind: str, provider: str | None) -> str:
        return f"{kind}:{provider or 'модель'}"

    def runs(self, key: str) -> list[dict]:
        items = self._load().get(key)
        return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []

    def expect(self, kind: str, provider: str | None, chars: int) -> tuple[float, float, int]:
        """(ожидаемые секунды, ожидаемый размер ответа, число замеров)."""
        base_s, base_out = seed(kind, chars)
        ratios_s, ratios_out = [], []
        for r in self.runs(self.key(kind, provider)):
            try:
                s0, o0 = seed(kind, int(r["in"]))
                ratios_s.append(float(r["s"]) / s0)
                ratios_out.append(float(r["out"]) / o0)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
        n = len(ratios_s)
        if not n:
            return base_s, base_out, 0
        return base_s * median(ratios_s), max(1.0, base_out * median(ratios_out)), n

    def record(self, kind: str, provider: str | None, chars: int, seconds: float, out: int) -> None:
        if seconds <= 0:
            return
        data = self._load()
        key = self.key(kind, provider)
        runs = self.runs(key)
        runs.append({"in": int(chars), "s": round(float(seconds), 2), "out": int(out)})
        data[key] = runs[-KEEP:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass


# --- шина ------------------------------------------------------------------------


def attach(bus, tracker: "Tracker") -> None:
    bus.llm_progress = tracker


def tracker_of(bus) -> "Tracker | None":
    return getattr(bus, "llm_progress", None) if bus is not None else None


def plan(bus, items: list[tuple[str, int]]) -> None:
    """План частей задачи: [(вид вызова, символов входа), …] — веса частей и
    оценка всей работы. Без трекера — ничего."""
    tracker = tracker_of(bus)
    if tracker is not None:
        tracker.plan(items)


def part(bus, n: int, total: int, *, stage: str, label: str, key: str | None = None,
         note: str | None = None) -> None:
    """Началась часть `n` из `total` (окно анализа, проверка профиля). Без
    трекера — прежнее событие «часть n−1 из total сделана»."""
    tracker = tracker_of(bus)
    if tracker is not None:
        tracker.part(n, total, key=key, note=note)
    elif bus is not None:
        bus.progress(stage, label=label, done=n - 1, total=total)


# --- ход ------------------------------------------------------------------------


class _Call:
    def __init__(self, key: str, chars: int, expect_s: float, expect_out: float, runs: int,
                 started: float, repair: bool) -> None:
        self.key, self.chars, self.expect_s, self.expect_out = key, chars, expect_s, expect_out
        self.runs, self.started, self.repair = runs, started, repair
        self.out = 0  # символов ответа получено потоком
        self.first_at: float | None = None
        self.p0 = 0.0  # доля вызова к первому куску текста
        self.p = 0.0


class Tracker:
    """Ход одной задачи модели (см. модуль). Методы зовут основной поток
    (вызов модели, поток текста) и поток часов — под замком."""

    TICK_S = 0.5
    MIN_GAP_S = 0.25

    def __init__(self, bus, kind: str, label: str, *, stage: str | None = None,
                 provider: str | None = None, stats: Stats | None = None, clock=time.monotonic) -> None:
        # `kind` — вид вызова для оценок (SEEDS, Stats), `stage` — имя ступени в событии.
        self.bus, self.kind, self.label, self.provider = bus, kind, label, provider
        self.stage = stage or kind
        self.stats = stats if stats is not None else Stats()
        self.clock = clock
        self.started = clock()
        self._lock = threading.RLock()
        self.weights: list[float] | None = None
        self.n, self.total = 1, 1
        self.key: str = kind
        self.note: str | None = None
        self.phase = "request"
        self.part_p = 0.0  # доля текущей части
        self.calls = 0  # вызовов в текущей части
        self.call: _Call | None = None
        self._fraction = 0.0
        self._last_at = -1e9
        self._last_sent: tuple | None = None
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None

    # --- план ----------------------------------------------------------------
    def plan(self, items: list[tuple[str, int]]) -> None:
        with self._lock:
            expect = [self.stats.expect(key, self.provider, chars)[0] for key, chars in items]
            if expect and all(x > 0 for x in expect):
                self.weights = expect  # вес части — её ожидаемые секунды
                self.total = len(expect)

    def part(self, n: int, total: int, *, key: str | None = None, note: str | None = None) -> None:
        with self._lock:
            self.n, self.total = max(1, n), max(1, total)
            if self.weights is not None and len(self.weights) != self.total:
                self.weights = None
            self.key = key or self.kind
            self.note = note
            self.phase = "request"
            self.part_p = 0.0
            self.calls = 0
            self.call = None
            self._emit(force=True)

    # --- доли ----------------------------------------------------------------
    def _weights(self) -> list[float]:
        return self.weights if self.weights is not None else [1.0] * self.total

    def _overall(self, part_p: float) -> float:
        w = self._weights()
        done = sum(w[: self.n - 1])
        cur = w[self.n - 1] if self.n - 1 < len(w) else 0.0
        return max(0.0, min(1.0, (done + cur * part_p) / (sum(w) or 1.0)))

    def _call_p(self, now: float) -> tuple[float, bool]:
        """Доля текущего вызова 0…<1 и «дольше обычного»."""
        call = self.call
        if call is None:
            return 0.0, False
        elapsed = now - call.started
        if call.first_at is None:
            p = soft(elapsed / call.expect_s) if call.expect_s > 0 else 0.0
            slow = elapsed > call.expect_s
        else:
            p = call.p0 + (1 - call.p0) * soft(call.out / call.expect_out)
            slow = call.out > call.expect_out or elapsed > 1.5 * call.expect_s
        call.p = max(call.p, p)
        return call.p, slow

    def _part_from_call(self, p: float) -> float:
        call = self.call
        if call is not None and call.repair:
            return VALIDATED + (REPAIRED - VALIDATED) * p
        return VALIDATED * p

    def _eta(self, now: float, slow: bool) -> float | None:
        call = self.call
        if call is None or slow:
            return None
        if call.first_at is not None and now - call.first_at >= 2 and call.out > 0:
            rate = call.out / max(0.1, now - call.first_at)
            left = max(0.0, call.expect_out - call.out) / rate
        elif call.runs >= CONFIDENT_RUNS:
            left = max(0.0, call.expect_s - (now - call.started))
        else:
            return None
        # Части после текущей: по плану — их ожидаемые секунды, без плана —
        # такие же, как эта.
        w = self._weights()
        per_weight = 1.0 if self.weights is not None else call.expect_s
        return left + per_weight * sum(w[self.n:])

    # --- вызов модели ----------------------------------------------------------
    def wrap(self, runner):
        """Runner, сообщающий ход: начало и конец вызова, поток текста."""
        takes_text = _takes_on_text(runner)

        async def run(prompt, **kwargs):
            outer = kwargs.pop("on_text", None)
            chars = len(prompt or "") + len(kwargs.get("system_prompt") or "")
            self.started_call(chars)
            if takes_text:
                def on_text(piece):
                    self.text(piece)
                    if outer is not None:
                        outer(piece)

                kwargs["on_text"] = on_text
            elif outer is not None:
                kwargs["on_text"] = outer
            reply = None
            try:
                reply = runner(prompt, **kwargs)
                if inspect.isawaitable(reply):
                    reply = await reply
                return reply
            finally:
                text = getattr(reply, "text", None) or ""
                self.finished_call(len(text), ok=reply is not None and not getattr(reply, "error", None))

        return run

    def started_call(self, chars: int) -> None:
        with self._lock:
            now = self.clock()
            repair = self.calls > 0
            self.calls += 1
            key = f"{self.key}-repair" if repair else self.key
            expect_s, expect_out, runs = self.stats.expect(self.key, self.provider, chars)
            if repair:  # исправление: ответ того же размера, вход больше
                expect_s *= 1.2
            self.call = _Call(key, chars, expect_s, expect_out, runs, now, repair)
            self.phase = "repair" if repair else "request"
            self._emit(force=True)

    def text(self, piece: str | None) -> None:
        """Кусок ответа из потока (None — новое сообщение модели: считаем
        всё полученное, полоска не откатывается)."""
        with self._lock:
            call = self.call
            if call is None or not piece:
                return
            now = self.clock()
            first = call.first_at is None
            if first:
                call.p0, _ = self._call_p(now)
                call.first_at = now
                if not call.repair:
                    self.phase = "generating"
            call.out += len(piece)
            self._emit(force=first)  # смена подшага — событие сразу

    def finished_call(self, out_chars: int, ok: bool) -> None:
        with self._lock:
            call = self.call
            now = self.clock()
            if call is not None and ok and not call.repair:
                out = max(out_chars, call.out)
                self.stats.record(self.key, self.provider, call.chars, now - call.started, out)
            self.part_p = max(self.part_p, REPAIRED if call is not None and call.repair else VALIDATED)
            self.call = None
            self.phase = "validating"
            self._emit(force=True)

    # --- часы и события ---------------------------------------------------------
    def tick(self) -> None:
        with self._lock:
            if self.call is not None:
                self._emit()

    def __enter__(self):
        self._stop = threading.Event()

        def loop() -> None:
            while not self._stop.wait(self.TICK_S):
                try:
                    self.tick()
                except Exception:
                    pass  # ход — подсказка: сбой часов работу не роняет

        self._thread = threading.Thread(target=loop, name="meet-llm-progress", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _emit(self, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self._last_at < self.MIN_GAP_S:
            return
        call_p, slow = self._call_p(now)
        if self.call is not None:
            self.part_p = max(self.part_p, self._part_from_call(call_p))
        fraction = max(self._fraction, self._overall(self.part_p))
        self._fraction = fraction
        repairing = self.call is not None and self.call.repair
        cap = self._overall(REPAIRED if repairing or self.phase == "validating" else VALIDATED)
        streaming = self.call is not None and self.call.first_at is not None
        data = {
            "fraction": round(fraction, 4),
            "cap": round(max(cap, fraction), 4),
            "part": self.n,
            "parts": self.total,
            "phase": self.phase,
            "slow": bool(slow),
            "elapsed_s": round(now - self.started, 1),
        }
        eta = self._eta(now, slow)
        if eta is not None:
            data["eta_s"] = round(eta, 1)
        sent = (data["fraction"], data["phase"], data["part"], data["slow"], data.get("eta_s") is None)
        if not force and sent == self._last_sent and self.call is None:
            return
        self._last_at = now
        self._last_sent = sent
        self.bus.progress(self.stage, label=self.label, done=round(call_p, 4), total=1,
                          note=self.note, unit="chars" if streaming else "time", **data)


def _takes_on_text(runner) -> bool:
    """Примет ли runner `on_text` (провайдеры — да; простые заглушки — нет)."""
    try:
        params = inspect.signature(runner).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.kind is inspect.Parameter.VAR_KEYWORD or p.name == "on_text" for p in params)
