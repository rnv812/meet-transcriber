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
    assert cmd[cmd.index("--permission-mode") + 1] == "auto"     # 0.4: по умолчанию — автомод
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


class _StubGate:
    """Ворота с заданными исходами по имени инструмента (решения — дело
    `consent.py` и его тестов; здесь — как `claude_stream` их передаёт CLI).
    `ask` — карточка (`confirmer`) → allow/deny; `can_use_tool` — всегда
    карточка (0.4, спецификация §3)."""

    def __init__(self, rules, confirmer=lambda card: "allow"):
        self.rules, self.confirmer = rules, confirmer
        self.calls, self._seen = [], set()

    def begin(self, level):
        self._seen = set()

    def end(self):
        pass

    def unseen(self, ids):
        return [t for t in ids or () if t and t not in self._seen]

    def check(self, tool, data=None, *, tool_use_id=None, via="hook"):
        self.calls.append((tool, via))
        if via == "hook" and tool_use_id:
            self._seen.add(tool_use_id)
        outcome = "ask" if via != "hook" else self.rules.get(tool, "auto")
        if outcome != "ask":
            return consent.Decision(outcome, reason=f"нельзя: {tool}" if outcome == "deny" else "", what=tool)
        answer = self.confirmer({"tool": tool, "args": json.dumps(data, ensure_ascii=False)})
        if answer in ("allow", "allow_meeting"):
            return consent.Decision("allow", what=tool, why="confirmed")
        return consent.Decision("deny", reason=f"Пользователь отклонил: {tool}", what=tool, why="declined")


def _hook_answers(records):
    return [r["message"]["response"]["response"] for r in records()
            if "message" in r and r["message"].get("type") == "control_response"]


def test_gate_outcomes_map_to_hook_answers(fake_cli, folders):
    """auto → `{}` (решают правила CLI и классификатор автомода); allow →
    `permissionDecision: allow` (CLI не спрашивает); ask → карточка → allow;
    deny → отказ с причиной; `can_use_tool` — карточка."""
    cli, records = fake_cli
    cards = []

    def confirmer(card):
        cards.append(card["tool"])
        return "deny" if card["tool"] == "WebFetch" else "allow"

    gate = _StubGate({"Read": "allow", "Edit": "auto", "Bash": "ask", "Agent": "deny", "WebFetch": "auto"},
                     confirmer)
    conv = _conv(cli, gate, folders)
    turn = _tools(
        {"name": "Read", "input": {"file_path": "a.md"}, "ask": True},
        {"name": "Edit", "input": {"file_path": "a.md"}},
        {"name": "Bash", "input": {"command": "rm -rf x"}, "ask": True},
        {"name": "Agent", "input": {"prompt": "x"}},
        {"name": "WebFetch", "input": {"url": "https://example.com"}, "ask": True},
    )

    async def go():
        try:
            return await _send(conv, gate, consent.READ, turn)
        finally:
            conv.close()

    out = json.loads(asyncio.run(go()).text)
    assert [(x["name"], x["result"], x.get("hook")) for x in out] == [
        ("Read", "allowed", "allow"), ("Edit", "allowed", "{}"), ("Bash", "allowed", "allow"),
        ("Agent", "denied", None), ("WebFetch", "denied", None)]
    assert out[3]["reason"] == "нельзя: Agent"
    assert "Пользователь отклонил" in out[4]["reason"]          # can_use_tool → карточка → «Отклонить»
    assert cards == ["Bash", "WebFetch"]
    assert ("WebFetch", "can_use_tool") in gate.calls
    answers = _hook_answers(records)
    assert {} in answers
    assert {"behavior": "deny", "message": "Пользователь отклонил: WebFetch"} in answers


def test_gate_answer_unit():
    gate = _StubGate({"Read": "allow", "Edit": "auto", "Agent": "deny", "Bash": "ask"})
    hook = lambda tool: {"subtype": "hook_callback", "callback_id": claude_stream.GATE_HOOK_ID,  # noqa: E731
                         "input": {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {}}}
    allow = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}
    assert claude_stream._gate_answer(gate, hook("Read"))[0] == allow
    assert claude_stream._gate_answer(gate, hook("Bash"))[0] == allow
    assert claude_stream._gate_answer(gate, hook("Edit"))[0] == {}
    body = claude_stream._gate_answer(gate, hook("Agent"))[0]
    assert body["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert body["hookSpecificOutput"]["permissionDecisionReason"] == "нельзя: Agent"
    # Ворота вернули «ask» без карточки (так быть не должно) — отказ, а не пропуск.
    odd = _StubGate({})
    odd.check = lambda *a, **k: consent.Decision("ask", what="x")
    assert claude_stream._gate_answer(odd, hook("Bash"))[0]["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert claude_stream._gate_answer(odd, {"subtype": "can_use_tool", "tool_name": "Bash", "input": {}})[0][
        "behavior"] == "deny"


def test_a_waiting_card_does_not_block_the_cli_output(fake_cli, folders):
    """Ответ на хук ждёт решения в своём потоке; пока ждёт — поток чтения жив."""
    cli, _records = fake_cli
    started = threading.Event()

    def confirmer(card):
        started.set()
        time.sleep(0.5)
        return "allow"

    gate = _StubGate({"Bash": "ask"}, confirmer)
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
    assert json.loads(reply.text) == [{"name": "Bash", "result": "allowed", "hook": "allow"}]


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


# --- 0.4: автомод, init/initialize, control-запросы, перезапуск, сжатие ------------------


def test_free_command_mode_auto_or_confirm_and_no_mcp_switch(tmp_path):
    auto = claude_stream.build_command(["claude"], system_prompt="s", responder=True, freedom=True, mode="auto")
    confirm = claude_stream.build_command(["claude"], system_prompt="s", responder=True, freedom=True,
                                          mode="confirm")
    assert auto[auto.index("--permission-mode") + 1] == "auto"
    assert confirm[confirm.index("--permission-mode") + 1] == "default"
    for cmd in (auto, confirm):
        assert cmd[cmd.index("--permission-prompt-tool") + 1] == "stdio"
        assert "--strict-mcp-config" not in cmd
        assert "--include-hook-events" in cmd
        assert cmd[cmd.index("--max-turns") + 1] == "40"
    with pytest.raises(TypeError):
        claude_stream.build_command(["claude"], system_prompt="s", responder=True, freedom=True, mcp=False)
    with pytest.raises(TypeError):
        claude_stream.Conversation(system_prompt="s", mcp=False)


def _run(conv, coro_fn):
    async def go():
        try:
            return await coro_fn()
        finally:
            conv.close()
    return asyncio.run(go())


def test_initialize_and_init_report_mode_commands_models_and_mcp(fake_cli, folders, monkeypatch):
    cli, records = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_MCP", "team-jira,team-gitlab:failed")
    gate = _StubGate({})
    conv = _conv(cli, gate, folders, mode="auto")
    seen = {}

    async def go():
        seen["before"] = conv.permission_mode
        await conv.control("mcp_status")          # поднимает процесс без хода модели
        seen["after_init"] = (conv.permission_mode, conv.auto_unavailable)
        seen["user_sent"] = any(r.get("message", {}).get("type") == "user" for r in records())
        return await _send(conv, gate, READ, "привет")

    reply = _run(conv, go)
    assert reply.error is None
    assert seen["before"] is None and seen["after_init"] == ("auto", False) and not seen["user_sent"]
    assert [c["name"] for c in conv.commands] == ["review", "context"]
    assert conv.commands[0] == {"name": "review", "description": "Review a PR", "hint": "[pr]"}
    assert [m["value"] for m in conv.models] == ["opus", "haiku"]
    assert conv.slash_commands == ["review", "context", "compact", "mcp"]
    assert conv.mcp_servers == [{"name": "team-jira", "status": "connected"},
                                {"name": "team-gitlab", "status": "failed"}]


def test_auto_mode_unavailable_is_visible(fake_cli, folders, monkeypatch):
    """Автомод недоступен (модель, disableAutoMode) — CLI молча стартует в
    default (сверено на 2.1.293): видно по `current_permission_mode`."""
    cli, _records = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_PERMISSION_MODE", "default")
    conv = _conv(cli, _StubGate({}), folders, mode="auto")
    _run(conv, lambda: conv.control("mcp_status"))
    assert conv.permission_mode == "default" and conv.auto_unavailable
    confirm = _conv(cli, _StubGate({}), folders, mode="confirm")
    _run(confirm, lambda: confirm.control("mcp_status"))
    assert not confirm.auto_unavailable          # «спрашивать каждое действие» — так и задумано


def test_set_model_switches_and_status_updates_the_mode(fake_cli, folders):
    cli, records = fake_cli
    models = []
    conv = _conv(cli, _StubGate({}), folders, mode="auto", on_model=models.append)

    async def go():
        await conv.set_model("haiku")
        await asyncio.sleep(0.3)                  # system/status приходит после ответа
        return conv.permission_mode

    assert _run(conv, go) == "default" and conv.auto_unavailable
    sent = [r["message"]["request"] for r in records() if r.get("message", {}).get("type") == "control_request"]
    assert {"subtype": "set_model", "model": "haiku"} in sent
    assert conv.model == "claude-haiku-4-5" and models[-1] == "claude-haiku-4-5"
    # Перезапуск с --resume идёт с выбранной моделью.
    assert conv._model == "haiku"


def test_mcp_status_and_reconnect_by_control_request(fake_cli, folders, monkeypatch):
    cli, records = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_MCP", "team-jira:failed,team-gitlab")
    conv = _conv(cli, _StubGate({}), folders)

    async def go():
        before = await conv.mcp_status()
        result = await conv.mcp_reconnect()       # без имени — все сбойные
        return before, result

    before, result = _run(conv, go)
    assert [(s["name"], s["status"]) for s in before] == [("team-jira", "failed"), ("team-gitlab", "connected")]
    assert before[0]["error"] == "Connection closed"
    assert result["results"] == {"team-jira": None} and not result["restarted"]
    assert [(s["name"], s["status"]) for s in result["servers"]] == [("team-jira", "connected"),
                                                                      ("team-gitlab", "connected")]
    assert conv.mcp_servers[0]["status"] == "connected"
    sent = [r["message"]["request"] for r in records() if r.get("message", {}).get("type") == "control_request"]
    assert {"subtype": "mcp_reconnect", "serverName": "team-jira"} in sent
    assert conv.spawns == 1


def test_unknown_server_is_an_error_without_restart(fake_cli, folders):
    cli, _records = fake_cli
    conv = _conv(cli, _StubGate({}), folders)
    result = _run(conv, lambda: conv.mcp_reconnect("nope"))
    assert "nope" in result["results"]["nope"] and not result["restarted"] and conv.spawns == 1


def test_control_errors_raise(fake_cli, folders):
    cli, _records = fake_cli
    conv = _conv(cli, _StubGate({}), folders)
    with pytest.raises(claude_stream.ControlError) as e:
        _run(conv, lambda: conv.control("no_such_thing"))
    assert e.value.unsupported and e.value.subtype == "no_such_thing"


def test_reconnect_without_the_subtype_restarts_with_resume(fake_cli, folders, monkeypatch):
    """Подтипа нет (старый CLI) — перезапуск с --resume: разговор тот же, MCP — заново."""
    cli, records = fake_cli
    sid = "5b0a3c1e-0000-4000-8000-0000000000aa"
    monkeypatch.setenv("FAKE_CLAUDE_KNOWN", sid)
    monkeypatch.setenv("FAKE_CLAUDE_MCP", "team-jira:failed")
    monkeypatch.setenv("FAKE_CLAUDE_NO_RECONNECT", "1")
    gate = _StubGate({})
    conv = _conv(cli, gate, folders, persist=True, resume=sid)

    async def go():
        await _send(conv, gate, READ, "привет")
        return await conv.mcp_reconnect("team-jira")

    result = _run(conv, go)
    assert result["restarted"] and result["context"]
    assert conv.spawns == 2 and conv.session_id == sid
    argvs = [r["argv"] for r in records() if "argv" in r]
    assert len(argvs) == 2 and f"--resume={sid}" in argvs[1]


def test_restart_keeps_the_conversation_only_when_persisted(fake_cli, folders, monkeypatch):
    cli, _records = fake_cli
    sid = "5b0a3c1e-0000-4000-8000-0000000000bb"
    monkeypatch.setenv("FAKE_CLAUDE_KNOWN", sid)
    gate = _StubGate({})
    kept = _conv(cli, gate, folders, persist=True, resume=sid)

    async def go_kept():
        await _send(kept, gate, READ, "привет")
        pid = kept.pid
        ok = await kept.restart()
        return ok, pid != kept.pid and kept.alive

    assert _run(kept, go_kept) == (True, True)
    lost = _conv(cli, gate, folders)

    async def go_lost():
        await _send(lost, gate, READ, "привет")
        return await lost.restart()

    assert _run(lost, go_lost) is False           # без сохранения — вызывающему нужна затравка


def test_control_during_a_turn_does_not_wait_for_it(fake_cli, folders):
    """/mcp во время ответа: control-запрос уходит сразу, ход ждёт карточку."""
    cli, _records = fake_cli
    release = threading.Event()
    gate = _StubGate({"Bash": "ask"}, lambda card: "allow" if release.wait(5) else "deny")
    conv = _conv(cli, gate, folders)

    async def go():
        turn = asyncio.create_task(_send(conv, gate, READ, _tools({"name": "Bash", "input": {"command": "x"}})))
        while not gate.calls:
            await asyncio.sleep(0.05)
        servers = await conv.mcp_status()
        release.set()
        return servers, await turn

    servers, reply = _run(conv, go)
    assert [s["name"] for s in servers] == ["team-jira", "team-gitlab"]
    assert json.loads(reply.text)[0]["result"] == "allowed"


def test_compact_turn_reports_the_boundary(fake_cli, folders):
    cli, _records = fake_cli
    gate = _StubGate({})
    conv = _conv(cli, gate, folders)

    async def go():
        await _send(conv, gate, READ, "привет")
        return await conv.compact()

    reply = _run(conv, go)
    assert reply.error is None
    assert reply.compacted == {"trigger": "manual", "pre_tokens": 12345, "post_tokens": 900}
    assert conv.context_tokens == 900


def test_local_command_output_is_the_reply_text(fake_cli, folders):
    """Команда CLI без ответа модели (отказ `!`…`` и т. п.) — текст CLI в ответе."""
    cli, _records = fake_cli
    gate = _StubGate({})
    conv = _conv(cli, gate, folders)
    reply = _run(conv, lambda: _send(conv, gate, READ, "/fail"))
    assert reply.text == "Shell command permission check failed" and reply.compacted is None


# --- 0.4: ход работы в чате — события инструментов --------------------------------------

# Строки потока claude 2.1.293 (сняты пробником, поддельный API; сокращены).
RECORDED = [
    {"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "echo TOPRAN", "description": "x"}}]}},
    {"type": "user", "parent_tool_use_id": None, "message": {"role": "user", "content": [
        {"tool_use_id": "toolu_1", "type": "tool_result", "content": "TOPRAN", "is_error": False}]},
     "tool_result_meta": [{"id": "toolu_1", "permission_decision": {"decision": "accept", "source": "config",
                                                                    "reason_type": "mode"}}]},
    {"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_use", "id": "toolu_2", "name": "mcp__team-jira__jira_get_issue", "input": {"key": "A-1"}}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "content": "Permission to use Bash with command git status has been denied.",
         "is_error": True, "tool_use_id": "toolu_2"}]}},
    {"type": "system", "subtype": "hook_started", "hook_id": "h1", "hook_name": "PreToolUse:Bash",
     "hook_event": "PreToolUse"},
    {"type": "system", "subtype": "hook_response", "hook_id": "h1", "hook_name": "PreToolUse:Bash",
     "hook_event": "PreToolUse", "output": "1\r\n", "stdout": "1\r\n", "stderr": "", "exit_code": 0,
     "outcome": "success"},
    {"type": "assistant", "parent_tool_use_id": "toolu_9", "agent_id": "a1", "message": {"content": [
        {"type": "tool_use", "id": "toolu_3", "name": "Read", "input": {"file_path": "a"}}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_3", "content": [{"type": "text", "text": "x" * 70_000},
                                                                     {"type": "image", "source": {}}]}]}},
]


def test_tool_events_from_recorded_stream_lines():
    clock = iter([10.0, 10.25, 11.0, 11.5, 12.0, 12.0, 12.0, 12.0, 12.0, 12.0])
    tracker = claude_stream.ToolTracker(clock=lambda: next(clock))
    events = [e for msg in RECORDED for e in tracker.scan(msg)]
    use, res = events[0], events[1]
    assert use == {"type": "tool_use", "id": "toolu_1", "name": "Bash", "server": None, "tool": "Bash",
                   "input": {"command": "echo TOPRAN", "description": "x"}, "parent": None}
    assert res == {"type": "tool_result", "id": "toolu_1", "ok": True, "output": "TOPRAN", "truncated": False,
                   "duration_ms": 250, "parent": None,
                   "cli_decision": {"decision": "accept", "source": "config", "reason_type": "mode"}}
    assert events[2]["server"] == "team-jira" and events[2]["tool"] == "jira_get_issue"
    assert events[3]["ok"] is False and events[3]["output"].startswith("Permission to use")
    assert events[3]["duration_ms"] == 500
    assert events[4] == {"type": "hook", "hook_id": "h1", "name": "PreToolUse:Bash", "event": "PreToolUse",
                         "status": "started", "outcome": None, "exit_code": None, "output": ""}
    assert events[5]["status"] == "done" and events[5]["outcome"] == "success" and events[5]["output"] == "1\r\n"
    assert events[6]["parent"] == "toolu_9"
    big = events[7]
    assert big["truncated"] and len(big["output"].encode("utf-8")) <= claude_stream.OUTPUT_LIMIT
    # Повтор того же tool_use (сообщения модели по блокам) — без второго события.
    assert tracker.scan(RECORDED[0]) == []


def test_turn_events_reach_the_caller_with_gate_decisions(fake_cli, folders):
    cli, _records = fake_cli
    gate = _StubGate({"Read": "allow", "Bash": "auto", "Agent": "deny", "Write": "ask"},
                     confirmer=lambda card: "allow")
    conv = _conv(cli, gate, folders)
    events = []
    turn = _tools({"name": "Read", "input": {"file_path": "a.md"}},
                  {"name": "Bash", "input": {"command": "npm test"}},
                  {"name": "Agent", "input": {"prompt": "x"}},
                  {"name": "Write", "input": {"file_path": "b.md"}})

    async def go():
        gate.begin(READ)
        try:
            return await conv.send(turn, timeout_s=20, on_event=events.append)
        finally:
            gate.end()

    _run(conv, go)
    gates = [(e["name"], e["decision"]) for e in events if e["type"] == "gate"]
    assert gates == [("Read", "allowed"), ("Bash", "auto"), ("Agent", "denied"), ("Write", "approved")]
    agent = next(e for e in events if e["type"] == "gate" and e["name"] == "Agent")
    assert agent["reason"] == "нельзя: Agent" and agent["via"] == "hook"
    kinds = [(e["type"], e.get("name")) for e in events if e["type"] in ("tool_use", "gate")]
    # Строка инструмента появляется раньше решения ворот по нему.
    assert kinds.index(("tool_use", "Read")) < kinds.index(("gate", "Read"))
    results = {e["id"]: e["ok"] for e in events if e["type"] == "tool_result"}
    uses = [e for e in events if e["type"] == "tool_use"]
    assert [results[u["id"]] for u in uses] == [True, True, False, True]
