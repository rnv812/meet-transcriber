"""Общий контракт провайдеров модели.

Каждый провайдер — async-функция с одной сигнатурой (её уже потребляют
Digester и QAService):

    runner(prompt, *, system_prompt, model, resume, allowed_dirs, cwd,
           timeout_s, max_turns) -> AgentReply

Ошибки не бросаются, а возвращаются в `AgentReply.error`.
"""

from collections.abc import Awaitable, Callable, MutableMapping
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


# Метки чужого сеанса Claude Code / Codex, унаследованные процессом (резидент
# запустили из терминала агента или из его команды). Вызовы модели — свежие
# независимые сеансы: с `CLAUDE_CODE_CHILD_SESSION` Claude Code считает себя
# вложенным и не сохраняет сеанс, а живому ассистенту продолжение разговора
# (`resume`) нужно. Тот же список, что у оболочки (`pty.rs`, SESSION_MARKERS):
# имена из строк claude.exe и codex.exe. Вход и настройки (ANTHROPIC_*,
# CLAUDE_CONFIG_DIR, CODEX_HOME, прокси) не трогаются.
SESSION_MARKERS = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_HOST_SESSION_ID",
    "CLAUDE_CODE_EVAL_INTERVIEW_SESSION",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "AI_AGENT",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
)


def drop_session_markers(env: MutableMapping[str, str]) -> list[str]:
    """Убрать из `env` метки чужого сеанса (без учёта регистра); → что убрано."""
    wanted = {name.upper() for name in SESSION_MARKERS}
    gone = [key for key in list(env) if key.upper() in wanted]
    for key in gone:
        env.pop(key, None)
    return gone
