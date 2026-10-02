"""Вопросы по идущей встрече: история диалога и быстрые действия.

История живёт здесь, в ассистенте, а не в окне: её видят и панель, и
карточка в главном окне (поток `state`, поле `qa`), и она переживает
переподключение. Вопрос попадает в историю сразу (`pending`) — окна
показывают «Модель думает…», пока модель отвечает.

Быстрые действия — готовые вопросы со своим контекстом:
«Что я пропустил?» (реплики с момента, когда человек последний раз смотрел
на панель, — его присылает панель как `since_t`; не прислала — последние
5 минут), «Какие решения уже приняты?», «Что мне ответить?» (на последний
вопрос, обращённый к владельцу), «Кратко за 1 минуту».

Ответ идёт в окно по мере генерации (у Claude Code): куски текста копятся в
`partials()`, сигнал окну — не чаще PARTIAL_EVERY_S (≤ 10 раз в секунду);
готовый ответ приходит в историю целиком.

Перед вопросом ассистент дорасшифровывает только ещё не распознанный хвост
речи (`on_fresh_audio`) и не ждёт окна, которое распознаётся прямо сейчас:
ответ берёт уже готовые реплики.
"""

import asyncio
import time
import uuid
from pathlib import Path

# Сколько последних пар «вопрос-ответ» класть в промпт провайдеру без сессий.
HISTORY_PAIRS = 6
HISTORY_KEEP = 50          # сколько вопросов помнить для окна
LINES_MAX_CHARS = 8_000    # реплик в промпт вопроса — не больше (свежие важнее)
# «Какие решения уже приняты?» — про всю встречу, а не про последние минуты:
# сводка (в ней решения уже собраны) идёт первой, реплик — вдвое больше.
DECISIONS_LINES_MAX_CHARS = 16_000
MISSED_DEFAULT_S = 300.0
# Частичный ответ — окну не чаще 10 раз в секунду.
PARTIAL_EVERY_S = 0.1

QUICK = {
    "missed": {
        "label": "Что я пропустил?",
        "prompt": ("Что я пропустил{since}? Кратко: о чём говорили, что решили и что "
                   "требует моей реакции. Ссылайся на таймкоды."),
    },
    "decisions": {
        "label": "Какие решения уже приняты?",
        "prompt": ("Какие решения уже приняты на встрече? Перечисли списком; у каждого — "
                   "таймкод реплики, где оно прозвучало. Нерешённое не включай."),
    },
    "reply": {
        "label": "Что мне ответить?",
        "prompt": ("Найди последний вопрос или просьбу, обращённые ко мне ({owner}), и "
                   "предложи 1–2 коротких варианта ответа от первого лица. Укажи таймкод "
                   "вопроса. Если такого вопроса не было — так и скажи."),
    },
    "brief": {
        "label": "Кратко за 1 минуту",
        "prompt": ("Кратко за 1 минуту: суть встречи на сейчас в 3–5 пунктах, которые "
                   "читаются за минуту, — решения, открытые вопросы и что ждут от меня."),
    },
}


def _clock_text(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _keep_latest(lines: list[str], limit: int) -> tuple[list[str], bool]:
    """Последние реплики в пределах limit символов; True — что-то отброшено."""
    out: list[str] = []
    size = 0
    for line in reversed(lines):
        if out and size + len(line) + 1 > limit:
            return out[::-1], True
        out.append(line)
        size += len(line) + 1
    return out[::-1], False


class QAService:
    """Вопросы по встрече: живая сводка + реплики + память диалога.

    Память диалога — свой сеанс провайдера: первый вопрос открывает его с
    нашим id (`session_id`, новый UUID), следующие продолжают (`resume`) — в
    нём остаются и прежние вопросы, и реплики, что к ним прилагались. Держит
    сеанс только Claude Code (у него это единственный сохраняемый фоновый
    сеанс; id живёт здесь, в памяти ассистента, а не в метке вкладки «Агент»).

    Сеанса нет (Codex, локальная модель, первый вопрос после сбоя
    продолжения) — в промпт кладутся последние HISTORY_PAIRS пар вопрос-ответ
    и последние реплики встречи в пределах LINES_MAX_CHARS, как у быстрых
    действий, а не только реплики с прошлого вопроса.

    Перед вопросом дёргает внеочередную дотранскрибацию (on_fresh_audio),
    чтобы ответ учитывал последние секунды речи. `model` — модель агента из
    настроек (`llm.model`, у Claude Code); None — раннер решает сам; `owner` —
    имя владельца в ленте.
    """

    def __init__(self, bus, live, *, system_prompt: str,
                 allowed_dirs: tuple[Path, ...], cwd, runner,
                 on_fresh_audio=None, model: str | None = None,
                 owner: str = "Вы", clock=time.time, changed=None) -> None:
        self._bus = bus
        self._live = live
        self._system = system_prompt
        self._allowed = allowed_dirs
        self._cwd = cwd
        self._runner = runner
        self._on_fresh_audio = on_fresh_audio
        self._model = model
        self._owner = owner or "Вы"
        self._clock = clock
        self._cursor = 0
        self._session_id: str | None = None
        self._items: list[dict] = []
        self._next_id = 1
        self.version = 0
        self._lock = asyncio.Lock()
        # Сигнал окнам (`Notifier`): новая история или кусок ответа.
        self._changed = changed
        self._partials: dict[int, str] = {}
        self.partial_version = 0
        self._partial_at = 0.0
        self._partial_timer = None

    def set_system_prompt(self, text: str) -> None:
        """Сменить системный промпт на лету (при смене задачи-контекста)."""
        self._system = text

    def _notify(self) -> None:
        if self._changed is not None:
            self._changed.notify()

    def partials(self) -> list[dict]:
        """Ответы, которые ещё пишутся: [{"id", "a"}] (текст на сейчас)."""
        return [{"id": i, "a": text} for i, text in self._partials.items()]

    def _partial(self, item: dict):
        """on_text для вызова модели: копит текст ответа и будит окна не
        чаще PARTIAL_EVERY_S (последний кусок — по таймеру)."""
        item_id = item["id"]

        def emit() -> None:
            self._partial_timer = None
            self._partial_at = time.monotonic()
            self.partial_version += 1
            self._notify()

        def on_text(chunk: str) -> None:
            if not chunk or item_id not in self._partials:
                return
            self._partials[item_id] += chunk
            wait = self._partial_at + PARTIAL_EVERY_S - time.monotonic()
            if wait <= 0:
                emit()
            elif self._partial_timer is None:
                try:
                    self._partial_timer = asyncio.get_running_loop().call_later(wait, emit)
                except RuntimeError:
                    emit()

        return on_text

    def history(self) -> list[dict]:
        """Вопросы для окна: {"id", "q", "a", "error", "pending", "at", "quick"}."""
        return [dict(item) for item in self._items]

    def _add(self, label: str, quick: str | None) -> dict:
        item = {"id": self._next_id, "q": label, "a": None, "error": None,
                "pending": True, "at": self._clock(), "quick": quick}
        self._next_id += 1
        self._items.append(item)
        del self._items[:-HISTORY_KEEP]
        self.version += 1
        self._partials[item["id"]] = ""
        self._notify()
        return item

    def _finish(self, item: dict, *, answer: str | None = None, error: str | None = None) -> None:
        item.update(a=answer, error=error, pending=False)
        self._partials.pop(item["id"], None)
        self.version += 1
        self._notify()

    async def ask(self, question: str | None = None, *, quick: str | None = None,
                  since_t: float | None = None) -> str:
        """Ответ текстом (ошибка — «⚠ …»). `quick` — быстрое действие из QUICK."""
        if quick is not None and quick not in QUICK:
            raise ValueError(f"неизвестное быстрое действие: {quick}")
        label = QUICK[quick]["label"] if quick else (question or "").strip()
        if not label:
            raise ValueError("пустой вопрос")
        item = self._add(label, quick)
        try:
            async with self._lock:
                if self._on_fresh_audio is not None:
                    await asyncio.to_thread(self._on_fresh_audio)
                prompt, cursor = self._build(label, quick, since_t)
                kwargs = {"model": self._model} if self._model else {}
                resume = self._session_id
                if resume is None:
                    # Новый свой сеанс; провайдер без сессий id просто не вернёт.
                    kwargs["session_id"] = str(uuid.uuid4())
                reply = await self._runner(
                    prompt, system_prompt=self._system, resume=resume,
                    allowed_dirs=self._allowed, cwd=self._cwd,
                    on_text=self._partial(item), **kwargs)
        except Exception as e:
            self._finish(item, error=f"внутренняя ошибка: {e}")
            raise
        if reply.error:
            if resume is not None:
                # Продолжить не вышло — следующий вопрос начнёт сеанс заново,
                # с памятью диалога и последними репликами в промпте.
                self._session_id = None
            self._finish(item, error=reply.error)
            return f"⚠ {reply.error}"
        self._cursor = cursor
        if reply.session_id:
            self._session_id = reply.session_id
        self._finish(item, answer=reply.text)
        return reply.text

    def _build(self, label: str, quick: str | None, since_t: float | None) -> tuple[str, int]:
        parts = ["Текущая сводка встречи:", self._live.render_markdown()]
        if self._session_id is None:
            done = [it for it in self._items if not it["pending"] and it["a"]]
            if done:
                parts += ["", "Предыдущие вопросы и ответы (память диалога):"]
                for it in done[-HISTORY_PAIRS:]:
                    parts += [f"Ранее спросили: {it['q']}", f"Ты ответил: {it['a']}"]
        entries, size = self._bus.entries_since(0)
        lines, _ = self._bus.since(0)
        lines = lines[:size]
        if quick == "missed":
            last = max((e.get("t") or 0.0 for e in entries), default=0.0)
            start = since_t if since_t is not None else max(0.0, last - MISSED_DEFAULT_S)
            picked = [line for line, e in zip(lines, entries)
                      if e.get("t") is None or e["t"] >= start]
            title = f"Реплики с [{_clock_text(start)}]:"
            ask = QUICK["missed"]["prompt"].format(
                since=f" с [{_clock_text(start)}]" if since_t is not None else " за последние минуты")
        elif quick is not None:
            picked, title = lines, "Реплики встречи (последние):"
            ask = QUICK[quick]["prompt"].format(owner=self._owner)
        elif self._session_id is not None:
            # Прежние реплики уже в сеансе — добавляем только новые.
            picked, title = lines[self._cursor:], "Свежие реплики (с прошлого вопроса):"
            ask = label
        else:
            picked, title = lines, "Реплики встречи (последние):"
            ask = label
        budget = DECISIONS_LINES_MAX_CHARS if quick == "decisions" else LINES_MAX_CHARS
        picked, cut = _keep_latest(picked, budget)
        if picked:
            if cut:
                title += " (начало опущено)"
            parts += ["", title, "\n".join(picked)]
        parts += ["", f"Вопрос: {ask}"]
        return "\n".join(parts), size
