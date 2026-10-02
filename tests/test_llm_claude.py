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
    не «вложенный» в чужой. Вход и настройки остаются."""
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
    # Сохранение выключает флаг CLI (см. ниже), а не переменные окружения.
    assert not any("PERSIST" in k or "SKIP_PROMPT_HISTORY" in k for k in upper)


def test_background_calls_do_not_persist_sessions(monkeypatch):
    """Фоновые вызовы (итоги, анализ, названия, профили, тики и вопросы
    живого ассистента — все идут через claude.run) не сохраняют сеанс: в
    командной строке CLI, которую собирает SDK, есть --no-session-persistence,
    а session_id не возвращается (такой сеанс не продолжить)."""
    from claude_agent_sdk import ResultMessage
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    seen = {}
    result = ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                           num_turns=1, session_id="sid-1", result="ответ")

    def fake_query(*, prompt, options):
        seen["cmd"] = SubprocessCLITransport(prompt=prompt, options=options)._build_command()

        async def gen():
            yield result
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")
    reply = asyncio.run(claude.run("привет", system_prompt="s", model="opus"))
    assert "--no-session-persistence" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "opus"
    assert reply.text == "ответ" and reply.session_id is None


def _command_and_reply(monkeypatch, **kw):
    from claude_agent_sdk import ResultMessage
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    seen = {}

    def fake_query(*, prompt, options):
        seen["cmd"] = SubprocessCLITransport(prompt=prompt, options=options)._build_command()

        async def gen():
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id=kw.get("resume") or kw.get("session_id") or "x",
                                result="ответ")
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")
    reply = asyncio.run(claude.run("привет", system_prompt="s", **kw))
    return seen["cmd"], reply


def test_live_qa_session_is_saved_under_our_id_and_resumed(monkeypatch):
    """Вопросы живого ассистента — единственный сохраняемый фоновый сеанс:
    новый — `--session-id=<наш UUID>`, дальше — `--resume=<тот же>`."""
    sid = "0b6f8a52-3c1d-4e2f-9a7b-1c2d3e4f5a6b"
    cmd, reply = _command_and_reply(monkeypatch, session_id=sid)
    assert f"--session-id={sid}" in cmd and "--no-session-persistence" not in cmd
    assert reply.session_id == sid
    cmd, reply = _command_and_reply(monkeypatch, resume=sid)
    assert f"--resume={sid}" in cmd and not any(a.startswith("--session-id") for a in cmd)
    assert "--no-session-persistence" not in cmd and reply.session_id == sid
    cmd, reply = _command_and_reply(monkeypatch)
    assert "--no-session-persistence" in cmd and reply.session_id is None


def test_on_text_streams_answer_pieces_and_asks_for_partial_messages(monkeypatch):
    """Ответ вопроса виден по мере генерации: куски текста (text_delta) —
    в on_text; размышления и прочие события — нет."""
    from claude_agent_sdk import ResultMessage, StreamEvent
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    seen = {}

    def delta(kind, **kw):
        return StreamEvent(uuid="u", session_id="s", event={
            "type": "content_block_delta", "delta": {"type": kind, **kw}})

    def fake_query(*, prompt, options):
        seen["cmd"] = SubprocessCLITransport(prompt=prompt, options=options)._build_command()

        async def gen():
            yield delta("thinking_delta", thinking="думаю")
            yield delta("text_delta", text="Пере")
            yield delta("text_delta", text="нести.")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id="x", result="Перенести.")
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")
    pieces = []
    reply = asyncio.run(claude.run("вопрос", system_prompt="s", on_text=pieces.append))
    assert pieces == ["Пере", "нести."] and reply.text == "Перенести."
    assert "--include-partial-messages" in seen["cmd"]


def test_fast_tier_turns_thinking_off_and_default_keeps_it(monkeypatch):
    cmd, _ = _command_and_reply(monkeypatch, model="haiku", thinking="disabled")
    assert cmd[cmd.index("--thinking") + 1] == "disabled"
    cmd, _ = _command_and_reply(monkeypatch, model="sonnet")
    assert "--thinking" not in cmd and "--include-partial-messages" not in cmd


def test_new_assistant_message_restarts_streamed_text(monkeypatch):
    from claude_agent_sdk import ResultMessage, StreamEvent

    def ev(event):
        return StreamEvent(uuid="u", session_id="s", event=event)

    def fake_query(*, prompt, options):
        async def gen():
            yield ev({"type": "message_start"})
            yield ev({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Посмотрю"}})
            yield ev({"type": "message_start"})              # после инструмента — новое сообщение
            yield ev({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Ответ."}})
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=2, session_id="x", result="Ответ.")
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")
    pieces = []
    asyncio.run(claude.run("вопрос", system_prompt="s", on_text=pieces.append))
    assert pieces == ["Посмотрю", None, "Ответ."]
