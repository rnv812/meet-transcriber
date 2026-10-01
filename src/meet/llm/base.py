"""Общий контракт провайдеров модели.

Каждый провайдер — async-функция с одной сигнатурой (её уже потребляют
Digester и QAService):

    runner(prompt, *, system_prompt, model, resume, allowed_dirs, cwd,
           timeout_s, max_turns) -> AgentReply

Ошибки не бросаются, а возвращаются в `AgentReply.error`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# Одинаковый текст таймаута у всех провайдеров: по нему вызывающий отличает
# «модель не успела» от прочих ошибок.
TIMEOUT_ERROR = "таймаут вызова модели"
EMPTY_ERROR = "модель вернула пустой ответ"


@dataclass
class AgentReply:
    text: str
    session_id: str | None = None
    error: str | None = None


Runner = Callable[..., Awaitable[AgentReply]]
