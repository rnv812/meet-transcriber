"""Smoke-проверка окружения meet assist: CLI, авторизация, can_use_tool.

Запуск: .venv/Scripts/python scripts/check_assist_env.py
"""
import asyncio
import os
import shutil


async def main() -> None:
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, PermissionResultDeny,
        ResultMessage, TextBlock, query,
    )

    if os.environ.get("ANTHROPIC_API_KEY"):
        print("ВНИМАНИЕ: выставлен ANTHROPIC_API_KEY — перебьёт подписку")
    cli = shutil.which("claude.cmd") or shutil.which("claude")
    print(f"CLI: {cli}")

    async def deny_all(tool_name, input_data, context):
        return PermissionResultDeny(message="tools disabled")

    for label, extra in (
        ("query без тулзов", {}),
        ("query + can_use_tool", {"can_use_tool": deny_all}),
    ):
        opts = ClaudeAgentOptions(
            system_prompt="Отвечай одним словом.", model="haiku",
            setting_sources=[], max_turns=1, cli_path=cli, **extra,
        )
        try:
            text, err = "", None
            async for msg in query(prompt="Скажи: ок", options=opts):
                if isinstance(msg, AssistantMessage):
                    err = getattr(msg, "error", None) or err
                    for b in msg.content:
                        if isinstance(b, TextBlock):
                            text += b.text
                elif isinstance(msg, ResultMessage):
                    err = msg.subtype if msg.is_error else err
            print(f"{label}: text={text!r} error={err}")
        except Exception as e:
            print(f"{label}: EXCEPTION {type(e).__name__}: {e}")


asyncio.run(main())
