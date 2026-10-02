"""Тикер живого состояния: когда звать модель и что делать с её ответом.

Один вызов модели на тик, никогда не два одновременно (цикл ждёт тик).
Каденс задаёт активность (`Cadence`): «Сдержанно» — не чаще раза в 45 с и
только когда набралось ≥ 60 новых слов, к 90 с — если сказано хоть
что-то; «Активно» — 25/60 с; «Только сводка» — как «Сдержанно», без
подсказок. В тишине тиков нет.

Вход тика — дельта: сжатое состояние, новые реплики (не больше
NEW_LINES_MAX_CHARS — остаток уйдёт следующим тиком), короткий хвост
предыдущих для контекста, до трёх фрагментов базы знаний. Инструментов у
тика нет: базу он не листает.

Инструментов у тика нет у Claude Code (Read/Grep/Glob запрещены) и у
локальной модели. Codex без инструментов не запускается: его тик идёт в
песочнице только-чтение с временной рабочей папкой (`llm.codex`), базу знаний
и запись он не видит, но это не «совсем без инструментов».

Реплики — данные, а не команды (правило в системном промпте); массовое
удаление сводки отклоняет проверка патча.

Ответ проверяется строго (`LiveState.apply`). Не прошёл — одна попытка
исправления; не помогло и она — состояние прежнее, реплики считаются
учтёнными (они остаются в хвосте контекста следующего тика). Ошибка
провайдера (сеть, лимиты, таймаут) — тихий статус «Подсказки временно
недоступны», реплики ждут повтора, пауза перед ним растёт. Подробности и
время тика — только в журнал (вывод `meet assist` → live.log), без текста
встречи.
"""

import asyncio
import time
from dataclasses import dataclass, replace

from meet.assist.live_state import PatchError
from meet.assist.prompts import build_repair_prompt, build_tick_prompt

UNAVAILABLE = "Подсказки временно недоступны"

# Объём входа тика (системный промпт + запрос): ~4 тыс. токенов.
MAX_PROMPT_CHARS = 12_000
NEW_LINES_MAX_CHARS = 3_500
TAIL_LINES = 4
TAIL_MAX_CHARS = 600
TICK_TIMEOUT_S = 90.0
BACKOFF_S = (30.0, 60.0, 120.0, 240.0)
POLL_S = 2.0
# К максимальному интервалу тик нужен, только если сказано хоть что-то.
MIN_WORDS_AT_MAX = 8


@dataclass(frozen=True)
class Cadence:
    min_s: float
    max_s: float
    min_words: int
    max_hints: int
    hints: bool = True

    def with_min_words(self, words: int) -> "Cadence":
        return replace(self, min_words=max(1, int(words)))

    def with_max_hints(self, hints: int) -> "Cadence":
        return replace(self, max_hints=max(1, int(hints)))


CALM = Cadence(min_s=45, max_s=90, min_words=60, max_hints=5)
ACTIVE = Cadence(min_s=25, max_s=60, min_words=30, max_hints=8)
SUMMARY_ONLY = Cadence(min_s=45, max_s=90, min_words=60, max_hints=5, hints=False)
CADENCES = {"calm": CALM, "active": ACTIVE, "summary": SUMMARY_ONLY}


def cadence_for(activity: str) -> Cadence:
    return CADENCES.get(activity, CALM)


def _words(entries: list[dict]) -> int:
    return sum(len(str(e.get("text") or "").split()) for e in entries)


class Digester:
    """Ведёт `LiveState` по шине реплик.

    `call_kwargs` — что добавить к вызову модели (уровень модели для тиков:
    `{"model": "haiku"}`, `{"effort": "low"}`); `kb` — указатель терминов
    базы знаний (`TermIndex`) или None; `on_update` — после изменения
    состояния (запись live_state.json); `log` — строка в журнал."""

    def __init__(self, bus, state, *, system_prompt: str, runner,
                 cadence: Cadence = CALM, call_kwargs: dict | None = None,
                 kb=None, clock=time.monotonic, on_update=None, log=print,
                 poll_s: float = POLL_S) -> None:
        self._bus = bus
        self._state = state
        self._system = system_prompt
        self._runner = runner
        self.cadence = cadence
        self._call_kwargs = dict(call_kwargs or {})
        self._kb = kb
        self._clock = clock
        self._on_update = on_update
        self._log = log
        self._poll_s = poll_s
        self._cursor = 0
        self._last_tick = clock()
        self._failures = 0
        self._retry_at = 0.0
        self.status: str | None = None
        self.last_latency: float | None = None

    def set_system_prompt(self, text: str) -> None:
        """Сменить системный промпт на лету (при смене задачи-контекста)."""
        self._system = text

    # --- когда ---

    def should_tick(self, words: int, elapsed_s: float) -> bool:
        c = self.cadence
        if words <= 0 or elapsed_s < c.min_s:
            return False
        if words >= c.min_words:
            return True
        return elapsed_s >= c.max_s and words >= MIN_WORDS_AT_MAX

    def pending_words(self) -> int:
        entries, _ = self._bus.entries_since(self._cursor)
        return _words(entries)

    def retry_in(self) -> float:
        """Сколько ещё ждать повтора после сбоя провайдера (0 — не ждём)."""
        return max(0.0, self._retry_at - self._clock()) if self._failures else 0.0

    # --- тик ---

    def _chunk(self) -> tuple[list[str], int]:
        """Новые реплики в пределах бюджета и позиция шины после них."""
        lines, end = self._bus.since(self._cursor)
        taken: list[str] = []
        size = 0
        for line in lines:
            if taken and size + len(line) + 1 > NEW_LINES_MAX_CHARS:
                break
            # Одна огромная реплика (склеенное окно) — обрезаем, но берём.
            line = line if len(line) <= NEW_LINES_MAX_CHARS else line[:NEW_LINES_MAX_CHARS] + "…"
            taken.append(line)
            size += len(line) + 1
        return taken, self._cursor + len(taken) if taken else end

    def _tail(self) -> list[str]:
        start = max(0, self._cursor - TAIL_LINES)
        lines, _ = self._bus.since(start)
        tail = lines[:self._cursor - start]
        out: list[str] = []
        size = 0
        for line in reversed(tail):
            if size + len(line) > TAIL_MAX_CHARS:
                break
            out.insert(0, line)
            size += len(line) + 1
        return out

    async def _call(self, prompt: str):
        return await self._runner(prompt, system_prompt=self._system, max_turns=1,
                                  timeout_s=TICK_TIMEOUT_S, **self._call_kwargs)

    def _failed(self, reason: str) -> bool:
        """Провайдер не ответил: тихий статус, пауза растёт, курсор на месте."""
        self._failures += 1
        pause = BACKOFF_S[min(self._failures, len(BACKOFF_S)) - 1]
        self._retry_at = self._clock() + pause
        self._last_tick = self._clock()
        self.status = UNAVAILABLE
        self._log(f"тик: модель недоступна ({reason}); повтор через {pause:.0f} с")
        return False

    async def tick_once(self) -> bool:
        """Один тик; True — состояние изменилось."""
        lines, end = self._chunk()
        if not lines:
            return False
        excerpts = self._kb.excerpts(lines) if self._kb is not None else []
        prompt = build_tick_prompt(self._state.render_compact(), lines, self._tail(), excerpts)
        refs = {e["ref"] for e in excerpts}
        started = time.monotonic()
        try:
            reply = await self._call(prompt)
        except Exception as e:  # сбой раннера не валит процесс
            return self._failed(f"{type(e).__name__}: {e}")
        if reply.error:
            return self._failed(reply.error)
        changed, error = self._apply(reply.text, refs)
        if error is not None:
            try:
                reply = await self._call(build_repair_prompt(prompt, reply.text, error))
            except Exception as e:
                return self._failed(f"{type(e).__name__}: {e}")
            if reply.error:
                return self._failed(reply.error)
            changed, error = self._apply(reply.text, refs)
        latency = time.monotonic() - started
        self.last_latency = latency
        self._failures = 0
        self._retry_at = 0.0
        self._last_tick = self._clock()
        self._cursor = end
        self.status = None
        size = len(prompt) + len(self._system)
        if error is not None:
            self._log(f"тик: {latency:.1f} с, вход {size} симв., ответ отклонён ({error})")
            return False
        self._log(f"тик: {latency:.1f} с, вход {size} симв., версия {self._state.version}")
        if changed and self._on_update is not None:
            self._on_update()
        return changed

    def _apply(self, text: str, refs: set) -> tuple[bool, str | None]:
        try:
            return self._state.apply(text, allowed_refs=refs), None
        except PatchError as e:
            return False, str(e)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.sleep(self._poll_s)
            now = self._clock()
            if self._failures and now < self._retry_at:
                continue
            words = self.pending_words()
            elapsed = now - self._last_tick
            if self._failures:
                due = words > 0  # пауза после сбоя выдержана — повторяем
            else:
                due = self.should_tick(words, elapsed)
            if not due:
                continue
            try:
                await self.tick_once()
            except Exception as e:  # страховка: цикл живёт при любом сбое
                self._log(f"тик: {type(e).__name__}: {e}")
