"""Постоянный диалог с Claude Code (`llm.claude_stream.Conversation`) — на
поддельном CLI (`fake_claude_stream.py`, настоящую модель не зовёт)."""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from meet.llm import base, claude_stream
from meet.llm.base import TIMEOUT_ERROR

FAKE = Path(__file__).with_name("fake_claude_stream.py")


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")

    def records():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    return [sys.executable, str(FAKE)], records


def _conv(cli, **kw):
    kw.setdefault("system_prompt", "системный промпт подсказок")
    return claude_stream.Conversation(cli=cli, **kw)


def test_one_process_for_many_turns_streamed_text_and_usage(fake_cli):
    cli, records = fake_cli
    pieces = []

    async def scenario():
        conv = _conv(cli, model="opus")
        try:
            first = await conv.send("первые реплики", on_text=pieces.append)
            pid = conv.pid
            second = await conv.send("новые реплики")
            return first, second, pid, conv.pid, conv.turns, conv.context_tokens
        finally:
            conv.close()

    first, second, pid1, pid2, turns, tokens = asyncio.run(scenario())
    assert first.error is None and first.text == '{"op":"none"}'
    assert pieces == ['{"op":"', 'none"}\n']           # текст приходит кусками
    assert second.error is None and pid1 == pid2         # тот же процесс
    assert turns == 2 and tokens == 10 + 2000 + 200 + 30
    messages = [r["message"]["message"]["content"] for r in records() if "message" in r]
    assert messages == ["первые реплики", "новые реплики"]


def test_command_line_no_tools_no_persistence_model_and_thinking(fake_cli, monkeypatch):
    cli, records = fake_cli
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")

    async def scenario():
        conv = _conv(cli, model="haiku", thinking="disabled")
        try:
            await conv.send("x")
        finally:
            conv.close()

    asyncio.run(scenario())
    start = records()[0]
    argv = start["argv"]
    for flag in ("--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands",
                 "--include-partial-messages"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--model") + 1] == "haiku"
    assert argv[argv.index("--thinking") + 1] == "disabled"
    assert argv[argv.index("--system-prompt") + 1] == "системный промпт подсказок"
    # Рабочая папка — служебная, не папка встречи (вкладка «Агент» её не продолжит).
    assert Path(start["cwd"]).name == claude_stream.WORKDIR
    env = {k.upper() for k in start["env"]}
    assert "ANTHROPIC_API_KEY" not in env
    assert not env & {m.upper() for m in base.SESSION_MARKERS}


def test_agent_tier_does_not_lower_thinking(fake_cli):
    cmd = claude_stream.build_command(["claude"], system_prompt="s", model="sonnet")
    assert "--thinking" not in cmd and "--effort" not in cmd


def test_crash_is_an_error_and_next_turn_starts_a_new_process(fake_cli, monkeypatch):
    cli, records = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "crash")

    async def scenario():
        conv = _conv(cli)
        try:
            ok = await conv.send("раз")
            pid = conv.pid
            broken = await conv.send("два")
            alive_after = conv.alive
            again = await conv.send("три")
            return ok, broken, alive_after, again, pid, conv.pid, conv.spawns, conv.turns
        finally:
            conv.close()

    ok, broken, alive_after, again, pid1, pid2, spawns, turns = asyncio.run(scenario())
    assert ok.error is None
    assert broken.error and "something broke" in broken.error and alive_after is False
    assert again.error is None and pid1 != pid2 and spawns == 2
    assert turns == 1                                   # новый процесс — новый счёт ходов


def test_turn_timeout_kills_the_process(fake_cli, monkeypatch):
    cli, _ = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")

    async def scenario():
        conv = _conv(cli)
        try:
            reply = await conv.send("ответь", timeout_s=1.0)
            return reply, conv.alive
        finally:
            conv.close()

    reply, alive = asyncio.run(scenario())
    assert reply.error == TIMEOUT_ERROR and alive is False


def test_close_leaves_no_process_behind(fake_cli):
    cli, _ = fake_cli

    async def scenario():
        conv = _conv(cli)
        await conv.send("x")
        proc = conv._proc
        conv.close()
        conv.close()  # повторно — безопасно
        return proc

    proc = asyncio.run(scenario())
    deadline = time.monotonic() + 5
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert proc.poll() is not None


def test_missing_cli_is_a_quiet_error(monkeypatch):
    monkeypatch.setattr(claude_stream, "default_cli", lambda: None)

    async def scenario():
        return await claude_stream.Conversation(system_prompt="s").send("x")

    reply = asyncio.run(scenario())
    assert reply.error and "Claude Code" in reply.error


@pytest.mark.skipif(sys.platform != "win32", reason="job object — только Windows")
def test_process_is_bound_to_the_assistant_job(fake_cli, monkeypatch):
    cli, _ = fake_cli
    bound = []
    real = claude_stream.procjob.bind_to_this_process
    monkeypatch.setattr(claude_stream.procjob, "bind_to_this_process",
                        lambda pid: bound.append(real(pid)) or bound[-1])

    async def scenario():
        conv = _conv(cli)
        try:
            await conv.send("x")
        finally:
            conv.close()

    asyncio.run(scenario())
    assert bound == [True]
