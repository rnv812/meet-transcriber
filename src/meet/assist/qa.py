import asyncio
from collections import deque
from pathlib import Path

RECAP_QUESTION = ("Что я пропустил? Дай короткую сводку последних минут "
                  "обсуждения: темы, решения, что требует моей реакции.")

# Сколько последних пар «вопрос-ответ» класть в промпт провайдеру без сессий.
HISTORY_PAIRS = 6


class QAService:
    """Вопросы по встрече: дайджест + свежие реплики + память диалога.

    Память диалога — сессия провайдера (resume), если раннер вернул
    session_id (Claude Code). Провайдеры без сессий (Codex, локальная модель)
    его не возвращают — тогда последние HISTORY_PAIRS пар вопрос-ответ
    кладутся прямо в промпт.

    Перед вопросом дёргает внеочередную дотранскрибацию (on_fresh_audio),
    чтобы ответ учитывал последние секунды речи.
    """

    def __init__(self, bus, digest, *, system_prompt: str,
                 allowed_dirs: tuple[Path, ...], cwd, runner,
                 on_fresh_audio=None, model: str = "sonnet") -> None:
        self._bus = bus
        self._digest = digest
        self._system = system_prompt
        self._allowed = allowed_dirs
        self._cwd = cwd
        self._runner = runner
        self._on_fresh_audio = on_fresh_audio
        self._model = model
        self._cursor = 0
        self._session_id: str | None = None
        self._history: deque[tuple[str, str]] = deque(maxlen=HISTORY_PAIRS)
        self._lock = asyncio.Lock()

    def set_system_prompt(self, text: str) -> None:
        """Сменить системный промпт на лету (при смене задачи-контекста)."""
        self._system = text

    async def ask(self, question: str) -> str:
        async with self._lock:
            if self._on_fresh_audio is not None:
                await asyncio.to_thread(self._on_fresh_audio)
            new_lines, cursor = self._bus.since(self._cursor)
            reply = await self._runner(
                self._build_prompt(question, new_lines),
                system_prompt=self._system, model=self._model,
                resume=self._session_id, allowed_dirs=self._allowed,
                cwd=self._cwd,
            )
            if reply.error:
                return f"⚠ {reply.error}"
            self._cursor = cursor
            if reply.session_id:
                self._session_id = reply.session_id
            self._history.append((question, reply.text))
            return reply.text

    def _build_prompt(self, question: str, new_lines: list[str]) -> str:
        parts = ["Текущий дайджест встречи:", self._digest.render()]
        if self._session_id is None and self._history:
            parts += ["", "Предыдущие вопросы и ответы (память диалога):"]
            for q, a in self._history:
                parts += [f"Ранее спросили: {q}", f"Ты ответил: {a}"]
        if new_lines:
            parts += ["", "Свежие реплики (с прошлого вопроса):",
                      "\n".join(new_lines)]
        parts += ["", f"Вопрос: {question}"]
        return "\n".join(parts)
