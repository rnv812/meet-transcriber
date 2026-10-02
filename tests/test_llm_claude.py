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
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(claude, "find_cli", lambda: None)
    assert "не найден" in asyncio.run(claude.check_auth())


def _fake_query(monkeypatch, seen, messages=()):
    def fake_query(*, prompt, options):
        seen["options_env"] = dict(options.env)
        seen["environ_https"] = os.environ.get("HTTPS_PROXY")

        async def gen():
            for m in messages:
                yield m
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")


def _no_env_proxy(monkeypatch):
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)


def test_run_passes_system_proxy_to_cli(monkeypatch):
    from meet import netproxy

    _no_env_proxy(monkeypatch)
    monkeypatch.setattr(netproxy, "read_registry",
                        lambda: {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:3067"})
    seen = {}
    _fake_query(monkeypatch, seen)
    asyncio.run(claude.run("привет", system_prompt="s"))
    env = {k.upper(): v for k, v in seen["options_env"].items()}
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3067"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:3067"
    assert "127.0.0.1" in env["NO_PROXY"]


def test_run_explicit_proxy(monkeypatch):
    _no_env_proxy(monkeypatch)
    seen = {}
    _fake_query(monkeypatch, seen)
    asyncio.run(claude.run("привет", system_prompt="s", proxy="http://10.1.1.1:3128"))
    env = {k.upper(): v for k, v in seen["options_env"].items()}
    assert env["HTTPS_PROXY"] == "http://10.1.1.1:3128"


def test_run_proxy_none_strips_inherited(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://10.1.1.1:3128")
    seen = {}
    _fake_query(monkeypatch, seen)
    asyncio.run(claude.run("привет", system_prompt="s", proxy="none"))
    assert seen["environ_https"] is None
    assert not any(k.upper() == "HTTPS_PROXY" for k in seen["options_env"])


def test_run_blocked_error_gets_proxy_hint(monkeypatch):
    from meet import netproxy

    def boom(*, prompt, options):
        raise RuntimeError("Claude Code returned an error result: Failed to authenticate. "
                           "API Error: 403 Request not allowed")

    monkeypatch.setattr(claude_agent_sdk, "query", boom)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")
    reply = asyncio.run(claude.run("привет", system_prompt="s"))
    assert "403 Request not allowed" in reply.error
    assert reply.error.endswith(netproxy.HINT)


def test_check_auth_passes_proxy(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")
    seen = {}

    async def fake_run(prompt, **kw):
        seen.update(kw)
        return base.AgentReply(text="ок")

    monkeypatch.setattr(claude, "run", fake_run)
    assert asyncio.run(claude.check_auth(proxy="none")) is None
    assert seen["proxy"] == "none"


def test_run_drops_inherited_session_markers_but_keeps_auth(monkeypatch):
    """Резидент запустили из сеанса Claude Code: вызов модели — свой сеанс,
    не «вложенный» (с CLAUDE_CODE_CHILD_SESSION Claude не сохраняет сеанс, и
    `resume` живого ассистента не нашёл бы его). Вход и настройки остаются."""
    for name, value in (("CLAUDE_CODE_CHILD_SESSION", "1"), ("CLAUDECODE", "1"),
                        ("CLAUDE_CODE_SSE_PORT", "45123"), ("CLAUDE_CODE_ENTRYPOINT", "cli"),
                        ("CLAUDE_CODE_MESSAGING_TOKEN", "m9-test-token"),
                        ("ANTHROPIC_BASE_URL", "https://gateway.example.invalid"),
                        ("CLAUDE_CONFIG_DIR", "D:/m9-test/claude")):
        monkeypatch.setenv(name, value)
    seen = {}

    def fake_query(*, prompt, options):
        seen["environ"] = dict(os.environ)
        seen["options_env"] = dict(options.env)

        async def gen():
            if False:
                yield None
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")
    asyncio.run(claude.run("привет", system_prompt="s"))
    upper = {k.upper() for k in seen["environ"]} | {k.upper() for k in seen["options_env"]}
    for marker in base.SESSION_MARKERS:
        assert marker not in upper, marker
    assert seen["environ"]["ANTHROPIC_BASE_URL"] == "https://gateway.example.invalid"
    assert seen["environ"]["CLAUDE_CONFIG_DIR"] == "D:/m9-test/claude"
    # Сохранение сеанса ничем не выключено.
    assert not any("PERSIST" in k or "SKIP_PROMPT_HISTORY" in k for k in upper)
