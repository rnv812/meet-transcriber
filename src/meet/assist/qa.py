import asyncio
from pathlib import Path

RECAP_QUESTION = ("Что я пропустил? Дай короткую сводку последних минут "
                  "обсуждения: темы, решения, что требует моей реакции.")


class QAService:
    """Вопросы по встрече: дайджест + свежие реплики + память диалога (resume).

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
        self._lock = asyncio.Lock()

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
            return reply.text

    def _build_prompt(self, question: str, new_lines: list[str]) -> str:
        parts = ["Текущий дайджест встречи:", self._digest.render()]
        if new_lines:
            parts += ["", "Свежие реплики (с прошлого вопроса):",
                      "\n".join(new_lines)]
        parts += ["", f"Вопрос: {question}"]
        return "\n".join(parts)
