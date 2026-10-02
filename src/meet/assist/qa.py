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
"""

import asyncio
import time
from pathlib import Path

# Сколько последних пар «вопрос-ответ» класть в промпт провайдеру без сессий.
HISTORY_PAIRS = 6
HISTORY_KEEP = 50          # сколько вопросов помнить для окна
LINES_MAX_CHARS = 8_000    # реплик в промпт вопроса — не больше (свежие важнее)
MISSED_DEFAULT_S = 300.0

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

    Память диалога — сессия провайдера (resume), если раннер вернул
    session_id (Claude Code). Провайдеры без сессий (Codex, локальная модель)
    его не возвращают — тогда последние HISTORY_PAIRS пар вопрос-ответ
    кладутся прямо в промпт.

    Перед вопросом дёргает внеочередную дотранскрибацию (on_fresh_audio),
    чтобы ответ учитывал последние секунды речи. `model` — None: модель
    агента по умолчанию (раннер решает сам); `owner` — имя владельца в ленте.
    """

    def __init__(self, bus, live, *, system_prompt: str,
                 allowed_dirs: tuple[Path, ...], cwd, runner,
                 on_fresh_audio=None, model: str | None = None,
                 owner: str = "Вы", clock=time.time) -> None:
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

    def set_system_prompt(self, text: str) -> None:
        """Сменить системный промпт на лету (при смене задачи-контекста)."""
        self._system = text

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
        return item

    def _finish(self, item: dict, *, answer: str | None = None, error: str | None = None) -> None:
        item.update(a=answer, error=error, pending=False)
        self.version += 1

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
                reply = await self._runner(
                    prompt, system_prompt=self._system, resume=self._session_id,
                    allowed_dirs=self._allowed, cwd=self._cwd, **kwargs)
        except Exception as e:
            self._finish(item, error=f"внутренняя ошибка: {e}")
            raise
        if reply.error:
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
        else:
            picked, title = lines[self._cursor:], "Свежие реплики (с прошлого вопроса):"
            ask = label
        picked, cut = _keep_latest(picked, LINES_MAX_CHARS)
        if picked:
            if cut:
                title += " (начало опущено)"
            parts += ["", title, "\n".join(picked)]
        parts += ["", f"Вопрос: {ask}"]
        return "\n".join(parts), size
