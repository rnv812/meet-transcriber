"""Провайдер Claude Code через claude-agent-sdk.

Один per-call вызов модели (свежая сессия или resume), строгий системный
промпт, без настроек проекта. Изменяющие/сетевые/шелл-инструменты запрещены
всегда; чтение папок разрешает колбэк по списку допустимых папок.
claude_agent_sdk импортируется только внутри функций: модуль можно
импортировать в резиденте, вызывать — только в подпроцессе.
"""

import asyncio
import logging
import os
from pathlib import Path

from meet import netproxy
from meet.llm.base import TIMEOUT_ERROR, AgentReply, drop_session_markers
from meet.llm.detect import find_claude

log = logging.getLogger(__name__)

# Инструменты, запрещённые всегда (в обеих линиях): ассистент ничего не меняет
# и не ходит в сеть/шелл. Read/Grep/Glob сюда не входят — их судьбу решает
# колбэк разрешений (Q&A с хранилищем vs дайджестер совсем без тулзов).
ALL_TOOLS_DENIED = [
    "Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch",
    "Task", "TodoWrite",
]
READ_TOOLS = ("Read", "Grep", "Glob")

# Флаг CLI «не сохранять сеанс» (claude --help 2.1.287: «only works with
# --print»; CLI считает режим --print и тогда, когда stdout не терминал, — так
# его и запускает SDK). ClaudeAgentOptions.extra_args: None — флаг без значения.
NO_PERSISTENCE = {"no-session-persistence": None}


def find_cli() -> str | None:
    """Путь к Claude Code CLI (см. `meet.llm.detect.find_claude`)."""
    return find_claude()


def drop_api_key() -> bool:
    """Убрать ANTHROPIC_API_KEY из окружения этого процесса.

    Ключ перебил бы подписку Claude в CLI. SDK умеет только добавлять
    переменные (`ClaudeAgentOptions.env`), но не удалять их, поэтому чистим
    собственное окружение: вызовы модели идут в отдельном процессе
    (job_worker, meet assist, meet.llm.check), резидента это не касается.
    """
    if os.environ.pop("ANTHROPIC_API_KEY", None) is None:
        return False
    log.warning("ANTHROPIC_API_KEY убран из окружения: Claude Code работает по подписке")
    return True


def make_permission_callback(allowed_dirs: tuple[Path, ...]):
    async def can_use_tool(tool_name, input_data, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if tool_name in READ_TOOLS and allowed_dirs:
            raw = (input_data or {}).get("file_path") or (input_data or {}).get("path")
            if raw is None:
                # Grep/Glob без path работают от cwd (папка записи) — ок.
                # Read без file_path не имеет смысла и не должен проходить.
                if tool_name in ("Grep", "Glob"):
                    return PermissionResultAllow()
                return PermissionResultDeny(message="Read без file_path")
            try:
                target = Path(raw).resolve()
            except (OSError, ValueError):
                return PermissionResultDeny(message="некорректный путь")
            for base in allowed_dirs:
                if target.is_relative_to(Path(base).resolve()):
                    return PermissionResultAllow()
            return PermissionResultDeny(
                message=f"путь вне разрешённых папок: {target}"
            )
        return PermissionResultDeny(message=f"инструмент {tool_name} отключён")

    return can_use_tool


async def run(
    prompt: str,
    *,
    system_prompt: str,
    model: str = "sonnet",
    resume: str | None = None,
    session_id: str | None = None,
    allowed_dirs: tuple[Path, ...] = (),
    cwd: str | Path | None = None,
    timeout_s: float = 180.0,
    max_turns: int = 8,
    proxy: str | None = None,
) -> AgentReply:
    """Один вызов Claude через Agent SDK: свежая сессия (или resume), строгий
    системный промпт, без настроек проекта; ошибки — в AgentReply.error.

    По умолчанию сеанс на диск не сохраняется (`--no-session-persistence`,
    NO_PERSISTENCE): фоновые вызовы — итоги, анализ, названия, профили, тики
    живого ассистента — не засоряют историю Claude Code человека, и
    `session_id` не возвращается (такой сеанс не продолжить).

    Свой сохраняемый сеанс — только по явной просьбе: `session_id` (новый
    сеанс с этим UUID, `--session-id`) или `resume` (продолжить его). Так
    живёт память вопросов живого ассистента (QAService); в ответе — id
    сеанса. Вкладку «Агент» он не задевает: она продолжает свой сеанс по
    своему id.

    `proxy` — `llm.proxy` (по умолчанию «как в системе»): Claude Code сам
    системный прокси Windows не видит, его передаём переменными."""
    import claude_agent_sdk
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock,
    )

    drop_api_key()
    # Как и ключ — из окружения своего процесса (SDK переменные только добавляет).
    drop_session_markers(os.environ)
    persist = bool(resume or session_id)
    options = ClaudeAgentOptions(
        env=netproxy.prepare(proxy),
        system_prompt=system_prompt,
        model=model,
        resume=resume,
        cwd=str(cwd) if cwd else None,
        cli_path=find_cli(),
        setting_sources=[],
        disallowed_tools=ALL_TOOLS_DENIED + (
            [] if allowed_dirs else list(READ_TOOLS)
        ),
        can_use_tool=make_permission_callback(allowed_dirs),
        max_turns=max_turns,
        session_id=None if resume else session_id,
        extra_args={} if persist else dict(NO_PERSISTENCE),
    )

    # can_use_tool в этой версии SDK требует streaming-режима ввода: строка-prompt
    # даёт ValueError. Отдаём prompt как AsyncIterable из одного user-сообщения —
    # это включает streaming input и сохраняет per-call семантику.
    async def _single_message():
        yield {"type": "user", "message": {"role": "user", "content": prompt}}

    text_parts: list[str] = []
    result_text: str | None = None
    reported: str | None = None
    error: str | None = None

    async def _consume() -> None:
        nonlocal result_text, reported, error
        async for msg in claude_agent_sdk.query(prompt=_single_message(), options=options):
            if isinstance(msg, AssistantMessage):
                if getattr(msg, "error", None):
                    error = str(msg.error)
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
            elif isinstance(msg, ResultMessage):
                reported = msg.session_id
                if getattr(msg, "result", None):
                    result_text = msg.result
                if msg.is_error and not error:
                    error = msg.subtype

    try:
        await asyncio.wait_for(_consume(), timeout=timeout_s)
    except asyncio.TimeoutError:
        error = TIMEOUT_ERROR
    except Exception as e:  # ProcessError, CLIConnectionError, JSONDecode...
        error = f"{type(e).__name__}: {e}"
    return AgentReply(
        text=(result_text or "".join(text_parts)).strip(),
        session_id=(reported or resume or session_id) if persist else None,
        error=netproxy.with_hint(error),
    )


async def check_auth(proxy: str | None = None, model: str = "haiku") -> str | None:
    """Проверка авторизации коротким вызовом. None = ок, иначе текст проблемы.
    `model` — чем проверять: кнопка «Проверить» передаёт `llm.model`, чтобы
    опечатка в имени модели была видна сразу, а не в фоновых задачах.

    ANTHROPIC_API_KEY не ошибка: он убирается из окружения (подписка важнее)."""
    drop_api_key()
    if find_cli() is None:
        return ("не найден Claude Code CLI (claude.exe); npm-шим claude.cmd "
                "не подходит — нужна родная установка Claude Code")
    reply = await run(
        "Ответь одним словом: ок",
        system_prompt="Отвечай одним словом.",
        model=model, max_turns=1, timeout_s=60.0, proxy=proxy,
    )
    return reply.error
