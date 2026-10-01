import asyncio
import os

import claude_agent_sdk

from meet.assist import agent
from meet.llm import base, claude


def test_reexports_compatible():
    assert agent.AgentReply is base.AgentReply
    assert agent.run_agent_query is claude.run
    assert agent.find_cli is claude.find_cli
    assert agent.check_auth is claude.check_auth
    assert agent.make_permission_callback is claude.make_permission_callback


def test_run_removes_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen = {}

    def fake_query(*, prompt, options):
        seen["env_key"] = os.environ.get("ANTHROPIC_API_KEY")
        seen["options_env"] = dict(options.env)

        async def gen():
            if False:
                yield None
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")
    reply = asyncio.run(claude.run("привет", system_prompt="s"))
    assert seen["env_key"] is None
    assert not seen["options_env"].get("ANTHROPIC_API_KEY")
    assert reply.text == ""


def test_check_auth_tolerates_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")

    async def fake_run(prompt, **kw):
        return base.AgentReply(text="ок")

    monkeypatch.setattr(claude, "run", fake_run)
    assert asyncio.run(claude.check_auth()) is None
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_check_auth_without_cli(monkeypatch):
    monkeypatch.setattr(claude, "find_cli", lambda: None)
    assert "не найден" in asyncio.run(claude.check_auth())
