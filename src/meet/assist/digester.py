"""Тикер живого состояния: когда звать модель и что делать с её ответом.

Две линии, у каждой не больше одного вызова в полёте (их ход не
перекрывается; линии друг друга не ждут):

* **подсказки** — по ритму речи: набралось `hint_gap_s` секунд сказанного
  (в тишине не считается) — «Сдержанно» 20 с, «Активно» 10 с; и сразу, когда
  на встрече задали вопрос или обратились к владельцу (`triggers`) — но не
  чаще EVENT_MIN_GAP_S после прошлого тика и не больше EVENT_CAP таких в
  час (свой бюджет: ложные поводы не съедают ритм). Реплики, пришедшие во
  время тика, уходят следующим. Не больше `HOURLY_CAP` тиков по ритму в
  час. Модель видит весь разговор: у Claude Code это постоянный диалог
  (`llm.claude_stream.Conversation`) — тик шлёт только новые реплики и
  активные подсказки `id · вид · текст` (id выдаёт состояние, без них модель
  не смогла бы править свои подсказки); диалог вырос сверх бюджета (CONTEXT_BUDGET_TOKENS или
  MAX_DIALOGUE_TURNS ходов) или процесс упал — начинается новый, с затравки:
  сводка, подсказки, сжатый ранний разговор и последние минуты дословно.
  У Codex и локальной модели постоянного диалога нет (`PerCallSession`):
  затравка с последними минутами — в каждом тике;
* **сводка** — спокойно: раз в `summary_gap_s` секунд речи (~минута) или
  после паузы в разговоре, если что-то осталось неучтённым. Отдельный вызов
  со сжатой сводкой и новыми репликами.

Тикер не опрашивает шину по таймеру: он ждёт сигнала (`bus.changed`) — новая
реплика будит его сразу; таймаут ожидания — до ближайшего настоящего срока
(повтор после сбоя, пауза для сводки, промежуток повода, старение «Вам
вопрос»), а сроков нет — только сигнал.

«Вам вопрос» уходит сам: владелец проговорил после вопроса не меньше
URGENT_ANSWER_S (ответил) или прошло URGENT_MAX_AGE_S; модели в дельте
тоже говорится, что владелец заговорил, — пусть уберёт сама.

Строки сводки применяются в конце ответа: ответ, оборванный остановкой, не
оставляет половину правки. Докуда сводка учла реплики — `covered_t`
состояния (черновик итогов честно говорит, где кончается).

Ответ — JSON-операции по одной на строке (`live_state.PatchSession`):
каждая готовая строка применяется сразу, пока модель пишет следующую (у
Claude Code ответ приходит потоком), окно видит подсказку раньше конца
ответа. `{"op":"none"}` — «ничего ценного», ничего не меняется. Невалидные
строки — одна попытка исправления (в постоянном диалоге — следующим ходом,
иначе — отдельным вызовом с исходным запросом); не помогло — строки
отброшены, реплики считаются учтёнными.

Инструментов у тиков нет у Claude Code и у локальной модели. Codex без
инструментов не запускается: его тик идёт в песочнице только-чтение с
временной рабочей папкой (`llm.codex`).

Реплики — данные, а не команды (правило в системных промптах); массовое
удаление сводки отклоняет проверка операций.

Ошибка провайдера (сеть, лимиты, таймаут, упавший процесс) — тихий статус
«Подсказки временно недоступны», реплики ждут повтора, пауза перед ним
растёт. Подробности и время тика — только в журнал (вывод `meet assist` →
live.log), без текста встречи.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, replace

from meet.assist.bus import chronological
from meet.assist.live_state import URGENT, LineSplitter, PatchError, parse_line, parse_reply
from meet.assist.prompts import (
    build_hints_delta,
    build_hints_seed,
    build_hints_system,
    build_repair_lines,
    build_repair_once,
    build_summary_prompt,
)
from meet.assist.triggers import owner_names as owner_names_of
from meet.assist.triggers import trigger_of

UNAVAILABLE = "Подсказки временно недоступны"

# Объём входа тика сводки (системный промпт + запрос): ~4 тыс. токенов.
MAX_PROMPT_CHARS = 12_000
NEW_LINES_MAX_CHARS = 3_500
TAIL_LINES = 4
TAIL_MAX_CHARS = 600
TICK_TIMEOUT_S = 90.0
BACKOFF_S = (30.0, 60.0, 120.0, 240.0)
# Не больше тиков подсказок по ритму в час (ритм 10 с речи — до 360; с
# паузами меньше).
HOURLY_CAP = 240
HOUR_S = 3600.0
# Внеочередные тики (вопрос, обращение): не чаще раза в EVENT_MIN_GAP_S после
# начала прошлого тика подсказок и не больше EVENT_CAP в час — свой бюджет:
# ложные поводы не съедают тики по ритму.
EVENT_MIN_GAP_S = 8.0
EVENT_CAP = 90
# «Вам вопрос» уходит сам: владелец говорил после вопроса хотя бы столько
# секунд (ответил) — или вопросу больше URGENT_MAX_AGE_S.
URGENT_ANSWER_S = 5.0
URGENT_MAX_AGE_S = 180.0
# Тик — только если сказано хоть что-то: «ага, угу» — не повод звать модель.
MIN_TICK_WORDS = 4
# Речь без таймкодов конца — по словам (темп деловой речи).
WORDS_PER_S = 2.2
REPLICA_MAX_S = 30.0
# Сводка подтягивает остаток после паузы в разговоре.
SUMMARY_IDLE_S = 20.0
SUMMARY_IDLE_MIN_S = 5.0
# Постоянный диалог подсказок начинается заново, когда вырос.
CONTEXT_BUDGET_TOKENS = 60_000
MAX_DIALOGUE_TURNS = 120
# Затравка нового диалога: последние минуты дословно, раньше — сжато.
RECENT_S = 180.0
SEED_RECENT_MAX_CHARS = 6_000
SEED_EARLIER_MAX_CHARS = 3_000
EARLIER_LINE_MAX = 90

_LANE_LOG = {"hints": "подсказки", "summary": "сводка"}


@dataclass(frozen=True)
class Cadence:
    """Ритм линий: секунды речи между тиками подсказок и сводки; сколько
    подсказок держать; `hints=False` — «Только сводка»; `min_words` — не
    меньше стольких новых слов для тика сводки (0 — MIN_TICK_WORDS)."""

    hint_gap_s: float
    summary_gap_s: float
    max_hints: int
    hints: bool = True
    min_words: int = 0

    def with_min_words(self, words: int) -> "Cadence":
        return replace(self, min_words=max(1, int(words)))

    def with_max_hints(self, hints: int) -> "Cadence":
        return replace(self, max_hints=max(1, int(hints)))


CALM = Cadence(hint_gap_s=20, summary_gap_s=60, max_hints=5)
ACTIVE = Cadence(hint_gap_s=10, summary_gap_s=45, max_hints=8)
SUMMARY_ONLY = Cadence(hint_gap_s=20, summary_gap_s=60, max_hints=5, hints=False)
CADENCES = {"calm": CALM, "active": ACTIVE, "summary": SUMMARY_ONLY}


def cadence_for(activity: str) -> Cadence:
    return CADENCES.get(activity, CALM)


def _live(entries: list[dict]) -> list[dict]:
    """Реплики, сказанные при ассистенте: без догнанного начала встречи
    (`catchup` — ассистента включили посреди записи). Подсказки — про то, что
    идёт сейчас: по началу встречи они не тикают и поводов не ищут."""
    return [e for e in entries if not e.get("catchup")]


def _words(entries: list[dict]) -> int:
    return sum(len(str(e.get("text") or "").split()) for e in entries)


def speech_seconds(entries: list[dict]) -> float:
    """Сколько секунд речи в репликах: по таймкодам начала и конца, без
    них — по словам. Тишина между репликами не считается."""
    total = 0.0
    for e in entries:
        t, end = e.get("t"), e.get("end")
        if isinstance(t, (int, float)) and isinstance(end, (int, float)) and end > t:
            total += min(float(end) - float(t), REPLICA_MAX_S)
        else:
            total += len(str(e.get("text") or "").split()) / WORDS_PER_S
    return total


def _clock_text(seconds) -> str:
    if not isinstance(seconds, (int, float)):
        return "сейчас"
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class PerCallSession:
    """«Диалог» без постоянного процесса: каждый тик — отдельный вызов
    runner (Codex, локальная модель). Модель прежних тиков не помнит
    (`stateful = False`): тикер кладёт контекст в каждый запрос."""

    stateful = False
    alive = True

    def __init__(self, runner, system_prompt: str, call_kwargs: dict | None = None) -> None:
        self._runner = runner
        self._system = system_prompt
        self._kwargs = dict(call_kwargs or {})
        self.turns = 0
        self.context_tokens = 0

    async def send(self, text: str, *, on_text=None, timeout_s: float = TICK_TIMEOUT_S):
        self.turns += 1
        return await self._runner(text, system_prompt=self._system, max_turns=1,
                                  timeout_s=timeout_s, on_text=on_text, purpose="tick", **self._kwargs)

    def close(self) -> None:
        pass


class _Lane:
    def __init__(self, name: str) -> None:
        self.name = name
        self.cursor = 0
        self.failures = 0
        self.retry_at = 0.0
        self.task: asyncio.Future | None = None
        self.starts: deque = deque()        # начала тиков по ритму (лимит в час)
        self.event_starts: deque = deque()  # начала внеочередных тиков (свой лимит)
        self.last_start: float | None = None
        self.last_latency: float | None = None


class _Applier:
    """Строки ответа → операции живого состояния, по мере прихода текста."""

    def __init__(self, patch, on_change, *, deferred: bool = False) -> None:
        self.patch = patch
        self._on_change = on_change
        self._split = LineSplitter()
        self._streamed = False
        # Отложенно (сводка): строки применяются только в finish() — ответ,
        # оборванный остановкой, не оставляет в сводке половину правки.
        self._deferred = deferred
        self._held: list[str] = []
        self.bad: list[tuple[str, str]] = []
        self.ok = 0
        self.changed = False

    def feed(self, text: str | None) -> None:
        if text is None:  # новое сообщение модели (после инструмента): строк не рвём
            return
        self._streamed = True
        for line in self._split.feed(text):
            if self._deferred:
                self._held.append(line)
            else:
                self._line(line)

    def finish(self, full_text: str) -> None:
        """Конец ответа: дописанный хвост (или весь текст, если поток не шёл)."""
        if not self._streamed:
            for line in (full_text or "").split("\n"):
                self._line(line)
        else:
            for line in self._held + self._split.finish():
                self._line(line)
            self._held = []
        if self.ok == 0:
            # Ни одной годной строки: может, это прежний формат — один
            # JSON-объект на несколько строк; иначе — ответ без JSON.
            try:
                legacy = parse_reply(full_text)
            except PatchError:
                if not self.bad and (full_text or "").strip():
                    self.bad.append(((full_text or "").strip()[:300],
                                     "в ответе нет ни одной JSON-строки"))
                return
            if "op" in legacy or "ops" in legacy or "topic" in legacy:
                self.bad = []
                self._apply(legacy, "")

    def _line(self, line: str) -> None:
        try:
            obj = parse_line(line)
        except PatchError as e:
            self.bad.append((line, str(e)))
            return
        if obj is not None:
            self._apply(obj, line)

    def _apply(self, obj: dict, line: str) -> None:
        try:
            changed = self.patch.apply(obj)
        except PatchError as e:
            self.bad.append((line or str(obj)[:300], str(e)))
            return
        self.ok += 1
        if changed:
            self.changed = True
            self._on_change()


class Digester:
    """Ведёт `LiveState` по шине реплик двумя линиями (см. модуль).

    `system_prompt` — системный промпт линии сводки, `hints_system` — линии
    подсказок; `runner` — вызов модели для сводки (и для подсказок без
    постоянного диалога); `hints_session(system_prompt)` — фабрика диалога
    подсказок (по умолчанию `PerCallSession` на том же runner);
    `call_kwargs` — что добавить к вызову модели (уровень «Быстрее»:
    `{"model": "haiku", "thinking": "disabled"}`, `{"effort": "low"}`);
    `kb` — указатель терминов базы знаний (`TermIndex`) или None;
    `owner_speaker` — подпись владельца в ленте; `owner_names` (или
    `owner_name`) — его имена для поиска обращений: из настроек и прежние из
    базы голосов (поводы внеочередной подсказки); `on_update` — после изменения состояния (запись
    live_state.json); `log` — строка в журнал."""

    def __init__(self, bus, state, *, system_prompt: str, runner,
                 cadence: Cadence = CALM, call_kwargs: dict | None = None,
                 hints_system: str | None = None, hints_session=None,
                 kb=None, clock=time.monotonic, on_update=None, log=print,
                 owner_speaker: str = "Вы", owner_name: str | None = None,
                 owner_names: list[str] | None = None,
                 hourly_cap: int = HOURLY_CAP, event_cap: int = EVENT_CAP,
                 context_budget: int = CONTEXT_BUDGET_TOKENS,
                 max_dialogue_turns: int = MAX_DIALOGUE_TURNS) -> None:
        self._bus = bus
        self._state = state
        self._summary_system = system_prompt
        self._hints_system = hints_system or build_hints_system(
            "", "", max_hints=cadence.max_hints, owner=owner_speaker)
        self._runner = runner
        self.cadence = cadence
        self._call_kwargs = dict(call_kwargs or {})
        self._session_factory = hints_session or (
            lambda system: PerCallSession(self._runner, system, self._call_kwargs))
        self._session = None
        self._reset_session = False
        self._kb = kb
        self._clock = clock
        self._on_update = on_update
        self._log = log
        self._owner_speaker = owner_speaker
        self._names = owner_names_of(*(owner_names or [owner_name or owner_speaker]))
        self._hourly_cap = hourly_cap
        self._event_cap = event_cap
        self._budget = context_budget
        self._max_turns = max_dialogue_turns
        self.hints = _Lane("hints")
        self.summary = _Lane("summary")
        self._scanned = 0
        self._trigger: tuple[str, str] | None = None
        self._last_line_at = clock()
        self.dialogue_resets = 0

    # --- состояние ---

    @property
    def status(self) -> str | None:
        return UNAVAILABLE if (self.hints.failures or self.summary.failures) else None

    @property
    def last_latency(self) -> float | None:
        if self.hints.last_latency is not None:
            return self.hints.last_latency
        return self.summary.last_latency

    def set_kb(self, kb) -> None:
        """Указатель терминов базы знаний, построенный в фоне (старт
        ассистента его не ждёт): тики до него — без фрагментов базы."""
        self._kb = kb

    def set_system_prompt(self, text: str, hints: str | None = None) -> None:
        """Сменить системные промпты на лету (смена задачи-контекста). Новый
        промпт подсказок — новый диалог со следующего тика."""
        self._summary_system = text
        if hints is not None and hints != self._hints_system:
            self._hints_system = hints
            self._reset_session = True

    def _lanes(self) -> list[_Lane]:
        return [self.hints, self.summary] if self.cadence.hints else [self.summary]

    def pending_words(self) -> int:
        """Новые слова, ещё не учтённые линией подсказок (в «Только сводка» —
        линией сводки)."""
        lane = self.hints if self.cadence.hints else self.summary
        entries, _ = self._bus.entries_since(lane.cursor)
        return _words(_live(entries) if lane is self.hints else entries)

    def skip_existing(self) -> None:
        """Всё, что уже лежит в шине (лента прошлого включения ассистента в
        этой записи), — только контекст: обе линии начинают после него."""
        size = self._bus.size()
        self.hints.cursor = self.summary.cursor = self._scanned = size

    def retry_in(self) -> float:
        """Сколько ещё ждать повтора после сбоя провайдера (0 — не ждём)."""
        now = self._clock()
        return max([max(0.0, lane.retry_at - now) for lane in self._lanes() if lane.failures],
                   default=0.0)

    # --- когда ---

    def _scan(self) -> None:
        """Новые реплики: время последней и поводы для внеочередной подсказки."""
        entries, size = self._bus.entries_since(self._scanned)
        if not entries:
            return
        self._scanned = size
        entries = _live(entries)
        if not entries:
            return  # догонялка начала встречи: не «разговор идёт сейчас»
        self._last_line_at = self._clock()
        if not self.cadence.hints:
            return
        for entry in entries:
            kind = trigger_of(entry, owner_speaker=self._owner_speaker, names=self._names)
            if kind:
                self._trigger = (kind, _clock_text(entry.get("t")))

    @staticmethod
    def _within_hour(starts: deque, now: float) -> int:
        while starts and now - starts[0] >= HOUR_S:
            starts.popleft()
        return len(starts)

    def _capped(self, lane: _Lane, now: float) -> bool:
        return self._within_hour(lane.starts, now) >= self._hourly_cap

    def _rhythm_due(self, entries: list[dict], now: float) -> bool:
        return (not self._capped(self.hints, now)
                and speech_seconds(entries) >= self.cadence.hint_gap_s
                and _words(entries) >= MIN_TICK_WORDS)

    def _event_wait(self, now: float) -> float:
        """Сколько ещё ждать внеочередному тику (0 — можно сейчас)."""
        last = self.hints.last_start
        return 0.0 if last is None else max(0.0, last + EVENT_MIN_GAP_S - now)

    def hints_due(self, now: float | None = None) -> bool:
        now = self._clock() if now is None else now
        lane = self.hints
        if not self.cadence.hints or lane.task is not None:
            return False
        if lane.failures and now < lane.retry_at:
            return False
        entries = _live(self._bus.entries_since(lane.cursor)[0])
        if not entries:
            return False
        if lane.failures:
            return True  # пауза после сбоя выдержана — повторяем
        if self._trigger is not None:
            if self._within_hour(lane.event_starts, now) >= self._event_cap:
                self._trigger = None  # бюджет поводов исчерпан — дальше по ритму
            elif self._event_wait(now) == 0.0:
                return True
        return self._rhythm_due(entries, now)

    def summary_due(self, now: float | None = None) -> bool:
        now = self._clock() if now is None else now
        lane = self.summary
        if lane.task is not None or (lane.failures and now < lane.retry_at):
            return False
        entries, _ = self._bus.entries_since(lane.cursor)
        if not entries:
            return False
        if lane.failures:
            return True
        if _words(entries) < (self.cadence.min_words or MIN_TICK_WORDS):
            return False
        speech = speech_seconds(entries)
        if speech >= self.cadence.summary_gap_s:
            return True
        return speech >= SUMMARY_IDLE_MIN_S and now - self._last_line_at >= SUMMARY_IDLE_S

    def _wake_in(self, now: float) -> float | None:
        """Через сколько секунд наступит ближайший настоящий срок (повтор после
        сбоя, пауза в разговоре для сводки, промежуток повода, старение «Вам
        вопрос», лимит в час); None — сроков нет, ждём только сигнала."""
        wake: list[float] = []
        for lane in self._lanes():
            pending, _ = self._bus.entries_since(lane.cursor)
            if lane is self.hints:
                pending = _live(pending)
            if lane.failures and lane.task is None and pending and lane.retry_at > now:
                wake.append(lane.retry_at - now)
        summary, _ = self._bus.entries_since(self.summary.cursor)
        if (self.summary.task is None and not self.summary.failures and summary
                and _words(summary) >= (self.cadence.min_words or MIN_TICK_WORDS)
                and speech_seconds(summary) >= SUMMARY_IDLE_MIN_S):
            idle = self._last_line_at + SUMMARY_IDLE_S - now
            if idle > 0:
                wake.append(idle)
        if self.cadence.hints and self.hints.task is None:
            hints = _live(self._bus.entries_since(self.hints.cursor)[0])
            if self._trigger is not None and hints and self._event_wait(now) > 0:
                wake.append(self._event_wait(now))
            if (hints and self._capped(self.hints, now)
                    and speech_seconds(hints) >= self.cadence.hint_gap_s):
                wake.append(self.hints.starts[0] + HOUR_S - now)
        expiry = self._state.urgent_expiry_in(URGENT_MAX_AGE_S)
        if expiry is not None:
            wake.append(expiry)
        wake = [w for w in wake if w > 0]
        return min(wake) if wake else None

    def resolve_urgent(self) -> bool:
        """«Вам вопрос» уходит сам: владелец ответил (говорил после вопроса
        не меньше URGENT_ANSWER_S) или вопрос устарел (URGENT_MAX_AGE_S).
        Закреплённые человек оставил — их не трогаем. True — что-то ушло."""
        urgent = [h for h in self._state.hints() if h["kind"] == URGENT and not h["pinned"]]
        if not urgent:
            return False
        entries, _ = self._bus.entries_since(0)
        changed = False
        for h in urgent:
            answered = speech_seconds([e for e in entries
                                       if e.get("speaker") == self._owner_speaker
                                       and isinstance(e.get("t"), (int, float))
                                       and e["t"] > h["source_t"]])
            if answered >= URGENT_ANSWER_S:
                changed |= self._state.resolve(h["id"])
            elif self._state.age(h) >= URGENT_MAX_AGE_S:
                changed |= self._state.resolve(h["id"])
        if changed:
            self._changed()
        return changed

    def _answered_note(self, entries: list[dict]) -> str:
        """Для дельты: владелец заговорил после «Вам вопрос» — пусть модель
        отметит вопрос отвеченным (remove), если ответ дан."""
        ids = [h["id"] for h in self._state.hints() if h["kind"] == URGENT and any(
            e.get("speaker") == self._owner_speaker and isinstance(e.get("t"), (int, float))
            and e["t"] > h["source_t"] for e in entries)]
        if not ids:
            return ""
        return (f"Владелец заговорил после вопроса {', '.join(ids)}: если ответ дан — "
                "удали эту подсказку (remove).")

    # --- реплики ---

    def _chunk(self, cursor: int, *, live_only: bool = False) -> tuple[list[dict], list[str], int]:
        """Новые реплики в пределах бюджета и позиция шины после них.
        `live_only` — без догнанного начала встречи (линия подсказок): его
        реплики пропускаются, но курсор их проходит."""
        lines, end = self._bus.since(cursor)
        entries, _ = self._bus.entries_since(cursor)
        taken: list[str] = []
        taken_entries: list[dict] = []
        size = 0
        consumed = 0
        for line, entry in zip(lines, entries):
            if live_only and entry.get("catchup"):
                consumed += 1
                continue
            if taken and size + len(line) + 1 > NEW_LINES_MAX_CHARS:
                break
            # Одна огромная реплика (склеенное окно) — обрезаем, но берём.
            line = line if len(line) <= NEW_LINES_MAX_CHARS else line[:NEW_LINES_MAX_CHARS] + "…"
            taken.append(line)
            taken_entries.append(entry)
            size += len(line) + 1
            consumed += 1
        return taken_entries, taken, cursor + consumed if taken else end

    def _tail(self, cursor: int) -> list[str]:
        start = max(0, cursor - TAIL_LINES)
        lines, _ = self._bus.since(start)
        tail = lines[:cursor - start]
        out: list[str] = []
        size = 0
        for line in reversed(tail):
            if size + len(line) > TAIL_MAX_CHARS:
                break
            out.insert(0, line)
            size += len(line) + 1
        return out

    def _history(self, cursor: int) -> tuple[list[str], list[str]]:
        """Уже учтённый разговор для затравки: (раньше — сжато, последние
        RECENT_S секунд — дословно), оба в пределах своих бюджетов."""
        lines, _ = self._bus.since(0)
        entries, _ = self._bus.entries_since(0)
        lines, entries = chronological(lines[:cursor], entries[:cursor])
        if not lines:
            return [], []
        times = [e.get("t") for e in entries if isinstance(e.get("t"), (int, float))]
        last = max(times) if times else None
        split = 0 if last is None else len(lines)
        if last is not None:
            for i, e in enumerate(entries):
                t = e.get("t")
                if isinstance(t, (int, float)) and t >= last - RECENT_S:
                    split = i
                    break
        recent: list[str] = []
        size = 0
        for line in reversed(lines[split:]):
            if recent and size + len(line) > SEED_RECENT_MAX_CHARS:
                break
            recent.insert(0, line)
            size += len(line) + 1
        older = lines[:len(lines) - len(recent)]
        earlier: list[str] = []
        size = 0
        for line in reversed(older):
            short = line if len(line) <= EARLIER_LINE_MAX else line[:EARLIER_LINE_MAX - 1] + "…"
            if size + len(short) > SEED_EARLIER_MAX_CHARS:
                earlier.insert(0, "…")
                break
            earlier.insert(0, short)
            size += len(short) + 1
        return earlier, recent

    # --- тики ---

    def _changed(self) -> None:
        if self._on_update is not None:
            try:
                self._on_update()
            except Exception as e:
                self._log(f"тик: запись состояния не удалась ({type(e).__name__})")
        self._bus.changed.notify()

    def _failed(self, lane: _Lane, reason: str) -> None:
        """Провайдер не ответил: тихий статус, пауза растёт, курсор на месте."""
        lane.failures += 1
        pause = BACKOFF_S[min(lane.failures, len(BACKOFF_S)) - 1]
        lane.retry_at = self._clock() + pause
        self._log(f"{_LANE_LOG[lane.name]}: модель недоступна ({reason}); повтор через {pause:.0f} с")

    async def _session_now(self):
        if self._reset_session and self._session is not None:
            old, self._session = self._session, None
            # Закрытие ждёт выхода процесса (до пары секунд) — не в цикле событий.
            await asyncio.to_thread(old.close)
        self._reset_session = False
        if self._session is None:
            self._session = self._session_factory(self._hints_system)
        return self._session

    async def hints_once(self) -> bool:
        """Тик подсказок; True — состояние изменилось."""
        lane = self.hints
        if not self.cadence.hints:
            return False
        chunk, lines, end = self._chunk(lane.cursor, live_only=True)
        if not lines:
            return False
        trigger, self._trigger = self._trigger, None
        now = self._clock()
        entries = _live(self._bus.entries_since(lane.cursor)[0])
        if trigger is not None and not self._rhythm_due(entries, now):
            lane.event_starts.append(now)  # внеочередной тик — из своего бюджета
        else:
            lane.starts.append(now)
        lane.last_start = now
        excerpts = self._kb.excerpts(lines) if self._kb is not None else []
        refs = {e["ref"] for e in excerpts}
        session = await self._session_now()
        fresh = not session.stateful or not session.alive or session.turns == 0
        if fresh:
            earlier, recent = self._history(lane.cursor)
            message = build_hints_seed(
                summary=self._state.render_compact(hints=False),
                hints=self._state.render_compact(summary=False),
                earlier=earlier, recent=recent, new_lines=lines, excerpts=excerpts,
                trigger=trigger)
        else:
            message = build_hints_delta(lines, self._state.hints_brief(), excerpts, trigger,
                                        note=self._answered_note(chunk))
        applier = _Applier(self._state.session(lane="hints", allowed_refs=refs), self._changed)

        async def send(text: str, on_text):
            return await session.send(text, on_text=on_text, timeout_s=TICK_TIMEOUT_S)

        def repair(bad):
            if session.stateful and session.alive:
                return build_repair_lines(bad)
            return build_repair_once(message, bad)

        latency = await self._tick(lane, send, message, applier, repair, end)
        if latency is None:
            if trigger is not None and self._trigger is None:
                self._trigger = trigger  # повод не пропал: повтор после паузы
            return applier.changed
        note = ""
        if session.stateful:
            note = f", диалог: ход {session.turns}, контекст {session.context_tokens} ток."
            first = getattr(session, "last_first_text_s", None)
            if first is not None:
                note += f", первый текст {first:.1f} с"
            if fresh:
                note += ", затравка"
        if trigger is not None:
            note += f", повод: {trigger[0]}"
        self._log(f"подсказки: {latency:.1f} с, вход {len(message)} симв.{note}, "
                  f"версия {self._state.version}")
        if session.stateful and (session.context_tokens >= self._budget
                                 or session.turns >= self._max_turns):
            self._log(f"диалог подсказок: начат заново (ходов {session.turns}, "
                      f"контекст {session.context_tokens} ток.)")
            self._reset_session = True
            self.dialogue_resets += 1
        return applier.changed

    async def summary_once(self) -> bool:
        """Тик сводки; True — состояние изменилось."""
        lane = self.summary
        chunk, lines, end = self._chunk(lane.cursor)
        if not lines:
            return False
        lane.starts.append(self._clock())
        lane.last_start = self._clock()
        prompt = build_summary_prompt(self._state.render_compact(hints=False), lines,
                                      self._tail(lane.cursor))
        applier = _Applier(self._state.session(lane="summary"), self._changed, deferred=True)

        async def send(text: str, on_text):
            return await self._runner(text, system_prompt=self._summary_system, max_turns=1,
                                      timeout_s=TICK_TIMEOUT_S, on_text=on_text, purpose="tick",
                                      **self._call_kwargs)

        latency = await self._tick(lane, send, prompt, applier,
                                   lambda bad: build_repair_once(prompt, bad), end)
        if latency is not None:
            times = [e["t"] for e in chunk if isinstance(e.get("t"), (int, float))]
            if times:
                self._state.mark_covered(max(times))
            size = len(prompt) + len(self._summary_system)
            self._log(f"сводка: {latency:.1f} с, вход {size} симв., версия {self._state.version}")
        return applier.changed

    async def _tick(self, lane: _Lane, send, message: str, applier: _Applier, repair,
                    end: int) -> float | None:
        """Вызов, построчное применение, одна попытка исправления. → время
        тика в секундах или None (сбой провайдера: курсор на месте)."""
        started = time.monotonic()
        try:
            reply = await send(message, applier.feed)
        except Exception as e:  # сбой раннера не валит процесс
            self._failed(lane, f"{type(e).__name__}: {e}")
            return None
        if reply.error:
            self._failed(lane, reply.error)
            return None
        applier.finish(reply.text)
        if applier.bad:
            fixer = _Applier(applier.patch, self._changed)
            try:
                reply = await send(repair(applier.bad), fixer.feed)
            except Exception as e:
                self._failed(lane, f"{type(e).__name__}: {e}")
                return None
            if reply.error:
                self._failed(lane, reply.error)
                return None
            fixer.finish(reply.text)
            applier.changed |= fixer.changed
            if fixer.bad:
                self._log(f"{_LANE_LOG[lane.name]}: строки отклонены ({fixer.bad[0][1]})")
        latency = time.monotonic() - started
        lane.last_latency = latency
        lane.failures = 0
        lane.retry_at = 0.0
        lane.cursor = end
        return latency

    async def tick_once(self) -> bool:
        """Обе линии по разу (подсказки, затем сводка); True — что-то изменилось."""
        changed = await self.hints_once()
        changed |= await self.summary_once()
        return changed

    async def _run_lane(self, lane: _Lane, tick) -> None:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # страховка: цикл живёт при любом сбое
            self._log(f"{_LANE_LOG[lane.name]}: {type(e).__name__}: {e}")
        finally:
            lane.task = None
            self._bus.changed.notify()  # статус мог смениться; разбудить цикл

    def _start_due(self, now: float) -> None:
        if self.hints_due(now):
            self.hints.task = asyncio.ensure_future(self._run_lane(self.hints, self.hints_once))
        if self.summary_due(now):
            self.summary.task = asyncio.ensure_future(self._run_lane(self.summary, self.summary_once))

    async def run(self, stop: asyncio.Event) -> None:
        signal = self._bus.changed
        signal.bind(asyncio.get_running_loop())
        seen = signal.seq
        stopped = asyncio.ensure_future(stop.wait())
        try:
            while not stop.is_set():
                now = self._clock()
                self._scan()
                self.resolve_urgent()
                self._start_due(now)
                waiter = asyncio.ensure_future(signal.wait(seen, self._wake_in(now)))
                await asyncio.wait({waiter, stopped}, return_when=asyncio.FIRST_COMPLETED)
                if waiter.done():
                    seen = waiter.result()
                else:
                    waiter.cancel()
        finally:
            stopped.cancel()
            running = [lane.task for lane in (self.hints, self.summary) if lane.task is not None]
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            await asyncio.to_thread(self.close)

    def close(self) -> None:
        """Закрыть диалог подсказок (процесс модели)."""
        session, self._session = self._session, None
        if session is not None:
            session.close()
