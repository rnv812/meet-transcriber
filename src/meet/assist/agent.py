"""Единая точка вызова Claude через claude-agent-sdk.

Обёртка для дайджестера и Q&A live-ассистента: один per-call вызов модели
(свежая сессия или resume), строгий системный промпт, без настроек проекта.
Изменяющие/сетевые/шелл-инструменты запрещены всегда; чтение хранилища
разрешает колбэк по списку допустимых папок.
"""

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

# Инструменты, запрещённые всегда (в обеих линиях): ассистент ничего не меняет
# и не ходит в сеть/шелл. Read/Grep/Glob сюда не входят — их судьбу решает
# колбэк разрешений (Q&A с хранилищем vs дайджестер совсем без тулзов).
ALL_TOOLS_DENIED = [
    "Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch",
    "Task", "TodoWrite",
]
READ_TOOLS = ("Read", "Grep", "Glob")


@dataclass
class AgentReply:
    text: str
    session_id: str | None = None
    error: str | None = None


def find_cli() -> str | None:
    """Явный путь к CLI: shutil.which('claude') на Windows находит
    bash-скрипт, который CreateProcess не исполняет (agent-sdk issue #252)."""
    for name in ("claude.cmd", "claude.exe", "claude"):
        p = shutil.which(name)
        if p:
            return p
    return None


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


async def run_agent_query(
    prompt: str,
    *,
    system_prompt: str,
    model: str = "sonnet",
    resume: str | None = None,
    allowed_dirs: tuple[Path, ...] = (),
    cwd: str | Path | None = None,
    timeout_s: float = 180.0,
    max_turns: int = 8,
) -> AgentReply:
    """Один вызов Claude через Agent SDK: свежая сессия (или resume), строгий
    системный промпт, без настроек проекта; ошибки — в AgentReply.error."""
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query,
    )

    options = ClaudeAgentOptions(
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
    )

    # can_use_tool в этой версии SDK требует streaming-режима ввода: строка-prompt
    # даёт ValueError. Отдаём prompt как AsyncIterable из одного user-сообщения —
    # это включает streaming input и сохраняет per-call семантику (см. Task 1).
    async def _single_message():
        yield {"type": "user", "message": {"role": "user", "content": prompt}}

    text_parts: list[str] = []
    result_text: str | None = None
    session_id: str | None = None
    error: str | None = None

    async def _consume() -> None:
        nonlocal result_text, session_id, error
        async for msg in query(prompt=_single_message(), options=options):
            if isinstance(msg, AssistantMessage):
                if getattr(msg, "error", None):
                    error = str(msg.error)
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
            elif isinstance(msg, ResultMessage):
                session_id = msg.session_id
                if getattr(msg, "result", None):
                    result_text = msg.result
                if msg.is_error and not error:
                    error = msg.subtype

    try:
        await asyncio.wait_for(_consume(), timeout=timeout_s)
    except asyncio.TimeoutError:
        error = "таймаут вызова модели"
    except Exception as e:  # ProcessError, CLIConnectionError, JSONDecode...
        error = f"{type(e).__name__}: {e}"
    return AgentReply(
        text=(result_text or "".join(text_parts)).strip(),
        session_id=session_id,
        error=error,
    )


async def check_auth() -> str | None:
    """Проверка авторизации при старте. None = ок, иначе текст проблемы."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ("выставлен ANTHROPIC_API_KEY — он перебивает подписку Claude; "
                "уберите его из окружения")
    if find_cli() is None:
        return "не найден Claude Code CLI (claude.cmd) в PATH"
    reply = await run_agent_query(
        "Ответь одним словом: ок",
        system_prompt="Отвечай одним словом.",
        model="haiku", max_turns=1, timeout_s=60.0,
    )
    return reply.error
