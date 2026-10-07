"""Claude Code со свободой по согласию (0.3.7, A1, fix round 1): командная
строка, `initialize` с хуком PreToolUse, ответы ворот на хук и `can_use_tool`
(карточка — в своём потоке, ответ ждёт решения), хуки пользователя
выключены, вызовы мимо хука видны. Всё — на поддельном CLI
(`fake_claude_stream.py`, режим gate)."""

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from meet.llm import claude_stream, consent
from meet.llm.base import RESUME_ERROR
from meet.llm.consent import NONE, READ, ConsentGate

FAKE = Path(__file__).with_name("fake_claude_stream.py")


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "gate")
    monkeypatch.setenv("FAKE_CLAUDE_MCP", "team-jira,team-gitlab")
    monkeypatch.delenv("FAKE_CLAUDE_KNOWN", raising=False)
    monkeypatch.delenv("FAKE_CLAUDE_NO_INIT", raising=False)

    def records():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    return [sys.executable, str(FAKE)], records


@pytest.fixture
def folders(tmp_path):
    meeting, kb, downloads, cwd = tmp_path / "meeting", tmp_path / "kb", tmp_path / "Downloads", tmp_path / "cwd"
    for d in (meeting, kb / "Личное", downloads, cwd):
        d.mkdir(parents=True)
    return meeting, kb, downloads, cwd


def _gate(folders, **kw):
    meeting, kb, _downloads, cwd = folders
    return ConsentGate(own_dirs=[meeting], deny_paths=[kb / "Личное"], sensitive=[], cwd=cwd, **kw)


def _conv(cli, gate, folders, **kw):
    meeting, _kb, _downloads, cwd = folders
    return claude_stream.Conversation(system_prompt="s", cli=cli, responder=True, add_dirs=[meeting],
                                      gate=gate, cwd=cwd, **kw)


def _tools(*calls) -> str:
    return "\n".join("TOOL " + json.dumps(c, ensure_ascii=False) for c in calls)


async def _send(conv, gate, level, text):
    gate.begin(level)
    try:
        return await conv.send(text, timeout_s=20)
    finally:
        gate.end()


# --- командная строка ----------------------------------------------------------------


def test_free_command_drops_isolation_routes_permissions_and_silences_user_hooks(tmp_path):
    meeting, kb = tmp_path / "m", tmp_path / "kb"
    cmd = claude_stream.build_command(["claude"], system_prompt="s", responder=True, freedom=True,
                                      add_dirs=[meeting], deny_paths=[kb / "Личное"], session_id="u-1",
                                      settings_file="C:/w/meet-agent-settings.json")
    for gone in ("--restricted", "--tools", "--allowedTools", "--setting-sources", "--strict-mcp-config",
                 "--disable-slash-commands", "--no-session-persistence"):
        assert gone not in cmd, gone
    assert cmd[cmd.index("--permission-mode") + 1] == "default"
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "stdio"
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--add-dir"] == [str(meeting)]
    denied = cmd[cmd.index("--disallowedTools") + 1:cmd.index("--settings")]
    assert {"AskUserQuestion", "Agent", "Task", "BashOutput", "KillShell", "CronCreate"} <= set(denied)
    assert any(r.startswith("Read(//") for r in denied)
    assert cmd[cmd.index("--settings") + 1] == "C:/w/meet-agent-settings.json"
    assert cmd[cmd.index("--max-turns") + 1] == str(claude_stream.FREE_MAX_TURNS)


def test_restricted_command_is_unchanged_without_freedom(tmp_path):
    """Выключатель «Расширенные возможности» выкл. — строка 0.3.6, слово в слово,
    плюс `--model=` (0.3.7, model-pick: модель всегда явно, пустая — sonnet)."""
    cmd = claude_stream.build_command(["claude"], system_prompt="s", responder=True,
                                      add_dirs=[tmp_path], freedom=False, settings_file="x.json")
    assert cmd == ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                   "--verbose", "--include-partial-messages", "--system-prompt", "s",
                   "--restricted", "--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob",
                   "--permission-mode", "dontAsk", "--add-dir", str(tmp_path),
                   "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
                   "--disable-slash-commands", "--max-turns", "12", "--model=sonnet"]
    listener = claude_stream.build_command(["claude"], system_prompt="s", freedom=True)
    assert "--permission-prompt-tool" not in listener and "--settings" not in listener


def test_conversation_writes_the_hooks_off_settings_and_sends_initialize(fake_cli, folders):
    cli, records = fake_cli
    gate = _gate(folders)
    conv = _conv(cli, gate, folders)

    async def go():
        try:
            return await _send(conv, gate, NONE, "привет")
        finally:
            conv.close()

    reply = asyncio.run(go())
    assert reply.error is None and json.loads(reply.text) == []
    argv = next(r["argv"] for r in records() if "argv" in r)
    settings = Path(argv[argv.index("--settings") + 1])
    assert settings.parent == folders[3]
    assert json.loads(settings.read_text(encoding="utf-8")) == {"disableAllHooks": True}
    first = next(r["message"] for r in records() if "message" in r)
    assert first["request"]["subtype"] == "initialize"
    assert first["request"]["hooks"]["PreToolUse"] == [{"matcher": None,
                                                        "hookCallbackIds": [claude_stream.GATE_HOOK_ID]}]
    assert conv.mcp_servers == [{"name": "team-jira", "status": "connected"},
                                {"name": "team-gitlab", "status": "connected"}]


# --- решения по вызовам ---------------------------------------------------------------


def test_reads_by_level_and_actions_by_card(fake_cli, folders):
    cli, records = fake_cli
    meeting, kb, downloads, _cwd = folders
    cards = []

    def confirmer(card):
        cards.append(card)
        return "allow" if "echo" in card["args"] else "deny"

    gate = _gate(folders, confirmer=confirmer)
    conv = _conv(cli, gate, folders)
    spec = str(downloads / "spec.pdf")
    turn = _tools(
        {"name": "Read", "input": {"file_path": str(meeting / "transcript.md")}},
        {"name": "Read", "input": {"file_path": spec}},
        {"name": "mcp__team-jira__jira_get_issue", "input": {"issue_key": "ABC-123"}, "ask": True},
        {"name": "Bash", "input": {"command": "echo hi > out.txt"}, "ask": True},
        {"name": "Write", "input": {"file_path": spec, "content": "x"}, "ask": True},
        {"name": "Read", "input": {"file_path": str(kb / "Личное" / "x.md")}},
        {"name": "Agent", "input": {"prompt": "x"}},
    )

    async def go():
        try:
            out = {}
            for level in (NONE, READ):
                reply = await _send(conv, gate, level, turn + f"\n# {level}")
                out[level] = [(x["name"], x["result"]) for x in json.loads(reply.text)]
                out[level + ":why"] = [x.get("reason") or "" for x in json.loads(reply.text)]
            return out
        finally:
            conv.close()

    out = asyncio.run(go())
    assert out[NONE] == [("Read", "allowed"), ("Read", "denied"), ("mcp__team-jira__jira_get_issue", "denied"),
                         ("Bash", "denied"), ("Write", "denied"), ("Read", "denied"), ("Agent", "denied")]
    assert out[READ] == [("Read", "allowed"), ("Read", "allowed"), ("mcp__team-jira__jira_get_issue", "allowed"),
                         ("Bash", "allowed"), ("Write", "denied"), ("Read", "denied"), ("Agent", "denied")]
    assert "Спроси пользователя с кнопками" in out[NONE + ":why"][1]
    assert "Пользователь отклонил" in out[READ + ":why"][4]
    assert "закрыта настройками" in out[READ + ":why"][5]
    assert "фоновое выполнение" in out[READ + ":why"][6]
    # Карточки — только в ходе по просьбе и только на действия, с точным вызовом.
    assert [c["title"] for c in cards] == ["команду", "запись в файл"]
    assert cards[0]["args"] == "echo hi > out.txt"
    answers = [r["message"]["response"]["response"] for r in records()
               if "message" in r and r["message"].get("type") == "control_response"]
    assert {"behavior": "allow", "updatedInput": {"command": "echo hi > out.txt"}} in answers


def test_a_waiting_card_does_not_block_the_cli_output(fake_cli, folders):
    """Ответ на хук ждёт решения в своём потоке; пока ждёт — поток чтения жив."""
    cli, _records = fake_cli
    started = threading.Event()

    def confirmer(card):
        started.set()
        time.sleep(0.5)
        return "allow"

    gate = _gate(folders, confirmer=confirmer)
    conv = _conv(cli, gate, folders)

    async def go():
        try:
            began = time.monotonic()
            reply = await _send(conv, gate, READ, _tools({"name": "Bash", "input": {"command": "ls | sort"}}))
            return reply, time.monotonic() - began
        finally:
            conv.close()

    reply, took = asyncio.run(go())
    assert started.is_set() and took >= 0.5
    assert json.loads(reply.text) == [{"name": "Bash", "result": "allowed"}]


def test_a_tool_result_for_a_call_the_hook_never_saw_ends_the_turn(fake_cli, folders):
    """Ревью R5: инструмент выполнился мимо хука — ход останавливается ошибкой (и в журнал)."""
    cli, _records = fake_cli
    logs = []
    gate = _gate(folders)
    conv = _conv(cli, gate, folders, log=logs.append)

    async def go():
        try:
            return await _send(conv, gate, READ, _tools(
                {"name": "Read", "input": {"file_path": str(folders[0] / "a.md")}},
                {"name": "Bash", "input": {"command": "ls"}, "skip_hook": True}))
        finally:
            conv.close()

    reply = asyncio.run(go())
    assert reply.error and reply.error.startswith(claude_stream.GATE_GAP_ERROR)
    assert len(conv.unseen_tools) == 1
    assert any(claude_stream.GATE_GAP_ERROR in line for line in logs)


def test_denied_results_without_a_hook_are_not_a_gap(fake_cli, folders):
    """Отказ правилом CLI (закрытая папка) приходит результатом с ошибкой и без хука — это не дыра."""
    cli, _records = fake_cli
    gate = _gate(folders)
    conv = _conv(cli, gate, folders)

    async def go():
        try:
            return await _send(conv, gate, READ, _tools(
                {"name": "Read", "input": {"file_path": str(folders[1] / "Личное" / "x.md")}}))
        finally:
            conv.close()

    reply = asyncio.run(go())
    assert reply.error is None


def test_denials_are_collected_for_the_chat_line(fake_cli, folders):
    cli, _records = fake_cli
    gate = _gate(folders)
    conv = _conv(cli, gate, folders)

    async def go():
        try:
            await _send(conv, gate, NONE, _tools({"name": "Read", "input": {"file_path": str(folders[2] / "a.pdf")}}))
        finally:
            conv.close()

    asyncio.run(go())
    line = consent.denial_line(gate.take_denials())
    assert "a.pdf" in line and "заблокирован" in line


def test_no_answer_to_initialize_is_an_error_not_an_ungated_turn(fake_cli, folders, monkeypatch):
    cli, records = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_NO_INIT", "1")
    monkeypatch.setattr(claude_stream, "INIT_WAIT_S", 0.5)
    gate = _gate(folders)
    conv = _conv(cli, gate, folders)

    async def go():
        try:
            return await _send(conv, gate, READ, _tools({"name": "Bash", "input": {"command": "ls"}}))
        finally:
            conv.close()

    reply = asyncio.run(go())
    assert reply.error and "проверку согласия" in reply.error
    assert not any(r.get("message", {}).get("type") == "user" for r in records())   # хода не было


def test_unknown_session_with_a_gate_is_still_a_resume_failure(fake_cli, folders):
    cli, _records = fake_cli
    gate = _gate(folders)
    conv = _conv(cli, gate, folders, persist=True, resume="5b0a3c1e-0000-4000-8000-000000000001")

    async def go():
        try:
            return await _send(conv, gate, NONE, "привет")
        finally:
            conv.close()

    reply = asyncio.run(go())
    assert reply.resume_failed and reply.error.startswith(RESUME_ERROR)
    assert conv.session_id is None


def test_without_a_gate_permission_requests_are_still_refused(fake_cli, folders, monkeypatch):
    """Ограниченный режим (0.3.6): входящий can_use_tool — отказ, как раньше."""
    cli, records = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "tool")
    conv = claude_stream.Conversation(system_prompt="s", cli=cli, responder=True, add_dirs=[folders[0]])

    async def go():
        try:
            return await conv.send("привет", timeout_s=20)
        finally:
            conv.close()

    reply = asyncio.run(go())
    assert reply.text == "ответ"
    sent = [r["message"] for r in records() if "message" in r]
    assert not any(m.get("request", {}).get("subtype") == "initialize" for m in sent)
    assert any(m.get("response", {}).get("response", {}).get("behavior") == "deny" for m in sent)
