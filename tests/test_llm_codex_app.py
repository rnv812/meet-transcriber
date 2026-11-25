"""Codex через `codex app-server` (JSON-RPC по stdio): ход с карточками Meet.

Собеседник — поддельный сервер `fake_codex_app_server.py` (Python, модель не
зовётся); исходящие сообщения сверяются с выдержкой схемы протокола
`fixtures/codex_app_server_schema.json` (codex-cli 0.160.0,
`codex app-server generate-json-schema`)."""

import asyncio
import json
import sys
import time
from pathlib import Path

import jsonschema
import psutil
import pytest

from meet.llm import codex, codex_app, consent

FAKE = Path(__file__).with_name("fake_codex_app_server.py")
SCHEMA = json.loads((Path(__file__).parent / "fixtures" / "codex_app_server_schema.json").read_text(encoding="utf-8"))
THREAD = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"


class Harness:
    def __init__(self, monkeypatch, tmp_path, *, mode=consent.MODE_AUTO, answers=()) -> None:
        self.tmp = tmp_path
        self.log = tmp_path / "peer.jsonl"
        self.meeting = tmp_path / "meeting"
        self.kb = tmp_path / "kb"
        self.other = tmp_path / "other"
        for d in (self.meeting, self.kb, self.other):
            d.mkdir()
        self.cards: list[dict] = []
        self.answers = list(answers)
        self.events: list[dict] = []
        self.lines: list[str] = []
        self.exec_calls: list[dict] = []
        monkeypatch.setenv("FAKE_CODEX_LOG", str(self.log))
        monkeypatch.setattr(codex, "find_codex", lambda: "C:/codex.exe")
        monkeypatch.setattr(codex, "_ON_WINDOWS", False)       # команды — как Bash на любой ОС
        monkeypatch.setattr(codex.tempdirs, "system_temp", lambda: tmp_path / "tmp")
        monkeypatch.setattr(codex_app, "command", lambda exe, flags: [sys.executable, str(FAKE), *flags])

        def fake_exec(exe, workdir, stdin_text, timeout_s, env=None, effort=None, images=(), keep_session=False,
                      resume=None, call=None, access=None, work_dirs=(), model=None, on_event=None, loop=None):
            self.exec_calls.append({"access": access, "resume": resume})
            return codex.AgentReply(text="через exec", session_id=resume)

        monkeypatch.setattr(codex, "_exec", fake_exec)
        monkeypatch.setattr(codex, "mcp_config", lambda: {})           # config.toml пользователя не читаем
        self.monkeypatch = monkeypatch
        self.gate = consent.ConsentGate(own_dirs=[self.meeting], work_dirs=[self.kb], sensitive=[],
                                        cwd=self.meeting, confirmer=self._confirm, mode=mode)

    def _confirm(self, card: dict) -> str:
        self.cards.append(card)
        return self.answers.pop(0) if self.answers else "deny"

    def run(self, text: str, *, level=consent.USER, resume=None, keep_session=True, timeout_s=30.0):
        self.gate.begin(level)
        try:
            return asyncio.run(codex.run(
                text, system_prompt="СИСТЕМА", resume=resume, keep_session=keep_session and not resume,
                allowed_dirs=(str(self.meeting), str(self.kb)), access=level,
                work_dirs=(str(self.meeting), str(self.kb)), gate=self.gate,
                on_event=self.events.append, log=self.lines.append, timeout_s=timeout_s))
        finally:
            self.gate.end()

    def received(self) -> list[dict]:
        if not self.log.exists():
            return []
        out = []
        for line in self.log.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if "in" in entry:
                out.append(entry["in"])
        return out

    def argv(self) -> list[str]:
        return json.loads(self.log.read_text(encoding="utf-8").splitlines()[0])["argv"]

    def sent(self, method: str) -> list[dict]:
        return [m["params"] for m in self.received() if m.get("method") == method]

    def mcp(self, servers, config=None, tools=None) -> None:
        """MCP-серверы пользователя: список (`codex mcp list --json`), таблицы config.toml
        ({имя: [инструменты со своей таблицей]}), аннотации для `mcpServerStatus/list`."""
        self.monkeypatch.setattr(codex, "mcp_servers", lambda exe=None, env=None, refresh=False: servers)
        self.monkeypatch.setattr(codex, "mcp_config", lambda: config)
        self.monkeypatch.setenv("FAKE_CODEX_TOOLS", json.dumps(tools or {}))


def _decisions(reply) -> list:
    return json.loads(reply.text)


# --- ход USER: карточки и ответы Codex ----------------------------------------------------------


def test_user_command_outside_the_sandbox_asks_and_allow_accepts(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["allow"])
    reply = h.run(f"CMD rm -rf {h.other.as_posix()}/x")
    assert reply.error is None and _decisions(reply) == ["accept"]
    assert len(h.cards) == 1
    card = h.cards[0]
    assert card["tool_use_id"] == "item-0" and card["kind"] == "shell"
    assert consent.CODEX_UNSANDBOXED in card["warnings"]
    assert card["grant"] is None                               # удаление «до конца встречи» не предлагается
    gate = [e for e in h.events if e["type"] == "gate"]
    assert gate and gate[0]["id"] == "item-0" and gate[0]["decision"] == "approved"


def test_declined_card_declines_and_the_row_is_denied(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["deny"])
    reply = h.run(f"CMD rm -rf {h.other.as_posix()}/x")
    assert _decisions(reply) == ["decline"]
    kinds = [(e["type"], e.get("decision")) for e in h.events]
    assert kinds == [("tool_use", None), ("gate", "declined"), ("tool_result", None)]
    assert h.events[-1]["ok"] is False


def test_allow_for_the_meeting_is_accept_for_session_and_the_repeat_needs_no_card(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=[consent.ALLOW_MEETING])
    first = h.run("CMD npm test")
    assert _decisions(first) == ["acceptForSession"]
    assert h.cards[0]["grant"]["key"] == "shell:Bash:npm test"
    assert "shell:Bash:npm test" in h.gate.grants()
    again = h.run("CMD npm test")            # следующий ход — новый процесс, разрешение держит Meet
    assert _decisions(again) == ["acceptForSession"] and len(h.cards) == 1


def test_delete_is_never_remembered_for_the_meeting(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=[consent.ALLOW_MEETING, "allow"])
    target = f"{h.other.as_posix()}/x"
    assert _decisions(h.run(f"CMD rm -rf {target}")) == ["accept"]
    assert _decisions(h.run(f"CMD rm -rf {target}")) == ["accept"]
    assert len(h.cards) == 2 and not h.gate.grants()


def test_reading_outside_the_folders_on_request_passes_without_a_card(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("CMD git status")
    assert _decisions(reply) == ["accept"] and h.cards == []
    assert [e["decision"] for e in h.events if e["type"] == "gate"] == ["allowed"]


def test_shell_wrapper_is_judged_by_its_script(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["deny"])
    reply = h.run(f"CMD /bin/bash -lc 'rm -rf {h.other.as_posix()}/x'")
    assert _decisions(reply) == ["decline"]
    assert h.cards[0]["args"].startswith("rm -rf") and h.cards[0]["grant"] is None
    h.gate.begin(consent.USER)
    assert h.gate.decide("Bash", {"command": f"rm -rf {h.other.as_posix()}/x"}).why == consent.DELETE


def test_confirm_mode_stays_user_and_asks_every_command(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, mode=consent.MODE_CONFIRM, answers=["allow", "deny"])
    reply = h.run("CMD npm test\nCMD pytest -q")
    assert _decisions(reply) == ["accept", "decline"] and len(h.cards) == 2
    turn = h.sent("turn/start")[0]
    assert turn["approvalPolicy"] == "on-request" and turn["approvalsReviewer"] == "user"
    assert turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert h.exec_calls == []


def test_auto_mode_user_turn_writes_the_working_folders(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.run("готово?")
    turn = h.sent("turn/start")[0]
    assert turn["approvalPolicy"] == "on-request" and turn["approvalsReviewer"] == "user"
    policy = turn["sandboxPolicy"]
    assert policy["type"] == "workspaceWrite" and policy["networkAccess"] is False
    assert [Path(p) for p in policy["writableRoots"]] == [h.meeting, h.kb]
    start = h.sent("thread/start")[0]
    assert start["approvalsReviewer"] == "user" and start["sandbox"] == "workspace-write"
    assert "tools.web_search=false" not in h.argv()


@pytest.mark.parametrize("level", [consent.NONE, consent.READ])
def test_none_and_read_are_read_only_and_requests_decline_without_a_card(monkeypatch, tmp_path, level):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run(f"CMD cp a.txt {h.other.as_posix()}/b.txt", level=level)
    assert _decisions(reply) == ["decline"] and h.cards == []
    turn = h.sent("turn/start")[0]
    assert turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    # ❓ — ход по просьбе: одобрения (и вызовы MCP) идут в ворота Meet, а они в нём только читают.
    assert turn["approvalPolicy"] == ("on-request" if level == consent.READ else "never")
    assert turn["approvalsReviewer"] == "user"
    assert h.sent("thread/start")[0]["sandbox"] == "read-only"
    argv = h.argv()
    assert "tools.web_search=false" in argv and argv.count("--disable") == len(codex._NONE_FEATURES)


def test_file_change_outside_asks_with_the_patch_and_inside_needs_none_in_auto(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["allow"])
    outside = json.dumps([{"path": str(h.other / "n.txt"), "kind": {"type": "add"}, "diff": "+привет\n"}])
    reply = h.run(f"PATCH {outside}")
    assert _decisions(reply) == ["accept"]
    card = h.cards[0]
    assert card["tool_use_id"] == "item-0" and "n.txt" in card["what"] and "+привет" in card["args"]
    assert consent.CODEX_UNSANDBOXED not in card["warnings"]
    rows = [e for e in h.events if e["type"] == "tool_use"]
    assert rows[0]["name"] == "apply_patch"


def test_file_delete_is_a_delete_card(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, mode=consent.MODE_CONFIRM, answers=[consent.ALLOW_MEETING])
    change = json.dumps([{"path": str(h.kb / "old.md"), "kind": {"type": "delete"}, "diff": ""}])
    assert _decisions(h.run(f"PATCH {change}")) == ["accept"]          # не «на сеанс»
    assert h.cards[0]["grant"] is None and not h.gate.grants()
    assert h.cards[0]["args"].startswith("rm -- ") and "old.md" in h.cards[0]["args"]


def test_other_server_requests_are_refused(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("ASK mcpServer/elicitation/request\nASK item/tool/requestUserInput\n"
                  "ASK item/permissions/requestApproval\nASK execCommandApproval")
    got = _decisions(reply)
    assert got[0] == {"action": "decline", "content": None, "_meta": None}
    assert got[1] == {"answers": {}}
    assert got[2]["error"]["code"] == -32601
    assert got[3] == {"decision": {"denied": {"rejection": "Meet этот запрос не поддерживает"}}}
    assert h.cards == []


# --- сеанс -----------------------------------------------------------------------------------


def test_new_thread_carries_the_system_prompt_and_returns_its_id(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("привет")
    assert reply.session_id == THREAD and reply.text == "ok"
    start = h.sent("thread/start")[0]
    assert start["ephemeral"] is False and Path(start["cwd"]) == tmp_path / "tmp" / codex.KEEP_WORKDIR
    text = h.sent("turn/start")[0]["input"][0]["text"]
    assert text.startswith("СИСТЕМА") and text.endswith("привет")


def test_resume_by_stored_id_without_the_system_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CODEX_KNOWN", OTHER)
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("дальше", resume=OTHER)
    assert reply.session_id == OTHER and not reply.resume_failed
    resume = h.sent("thread/resume")[0]
    assert resume["threadId"] == OTHER and resume["approvalsReviewer"] == "user"
    assert h.sent("thread/start") == []
    assert h.sent("turn/start")[0]["input"][0]["text"] == "дальше"


def test_unknown_id_is_a_resume_failure(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("дальше", resume=OTHER)
    assert reply.resume_failed and h.sent("turn/start") == [] and h.exec_calls == []


def test_resume_into_another_thread_is_a_resume_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CODEX_KNOWN", OTHER)
    monkeypatch.setenv("FAKE_CODEX_OTHER", "1")
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("дальше", resume=OTHER)
    assert reply.resume_failed and h.sent("turn/start") == []


def test_failed_turn_is_an_error(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("FAIL лимит исчерпан")
    assert reply.error and "лимит исчерпан" in reply.error


# --- остановка и запасной путь -------------------------------------------------------------------


def _pid(h) -> int | None:
    if not h.log.exists():
        return None
    first = h.log.read_text(encoding="utf-8").splitlines()[0]
    return json.loads(first).get("pid")


def test_cancel_mid_turn_interrupts_then_kills_the_tree(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.gate.begin(consent.USER)

    async def main():
        task = asyncio.create_task(codex.run(
            "HANG", system_prompt="СИСТЕМА", keep_session=True, access=consent.USER,
            work_dirs=(str(h.meeting),), gate=h.gate, on_event=h.events.append, log=h.lines.append))
        for _ in range(200):
            await asyncio.sleep(0.05)
            if h.sent("turn/start"):
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    deadline = time.monotonic() + 15
    pid = _pid(h)
    while time.monotonic() < deadline and (not h.sent("turn/interrupt") or psutil.pid_exists(pid)):
        time.sleep(0.1)
    assert h.sent("turn/interrupt") == [{"threadId": THREAD, "turnId": "turn-1"}]
    assert pid and not psutil.pid_exists(pid)


def test_timeout_interrupts_and_reports_a_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(codex_app, "INTERRUPT_GRACE_S", 2.0)
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("HANG", timeout_s=3.0)
    assert reply.error == codex.TIMEOUT_ERROR
    assert h.sent("turn/interrupt")


@pytest.mark.parametrize("init", ["deaf", "error", "exit"])
def test_server_that_does_not_handshake_falls_back_to_exec(monkeypatch, tmp_path, init):
    monkeypatch.setenv("FAKE_CODEX_INIT", init)
    monkeypatch.setattr(codex_app, "INIT_TIMEOUT_S", 1.0)
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("привет, секретный проект")
    assert reply.text == "через exec" and h.exec_calls == [{"access": consent.USER, "resume": None}]
    assert len(h.lines) == 1 and "codex exec" in h.lines[0] and "секретный" not in h.lines[0]


def test_fallback_in_confirm_mode_only_reads(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CODEX_INIT", "error")
    h = Harness(monkeypatch, tmp_path, mode=consent.MODE_CONFIRM)
    h.run("проверь")
    assert h.exec_calls == [{"access": consent.READ, "resume": None}]


def test_reviewer_other_than_the_user_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CODEX_REVIEWER", "auto_review")
    h = Harness(monkeypatch, tmp_path)
    h.run("проверь")
    assert h.exec_calls and h.sent("turn/start") == []


@pytest.mark.parametrize("level", [consent.NONE, consent.USER])
@pytest.mark.parametrize("servers", [None, [{"name": "a.b", "enabled": True, "transport": "stdio"}],
                                     [{"name": "x", "enabled": True, "transport": "sse"}]])
def test_mcp_that_cannot_be_stubbed_goes_through_exec_without_app_server(monkeypatch, tmp_path, servers, level):
    h = Harness(monkeypatch, tmp_path)
    h.mcp(servers, config={})
    h.run("проверь", level=level)
    assert h.exec_calls and not h.log.exists()
    assert "MCP" in h.lines[0]


def test_mcp_servers_are_stubbed_at_launch(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    monkeypatch.setattr(codex, "mcp_servers", lambda exe=None, env=None, refresh=False: [
        {"name": "jira", "enabled": True, "transport": "stdio"},
        {"name": "atlas", "enabled": True, "transport": "streamable_http"}])
    h.run("проверь")
    argv = h.argv()
    assert 'mcp_servers.jira={enabled=false,command="__meet_disabled_mcp__"}' in argv
    assert 'mcp_servers.atlas={enabled=false,url="http://127.0.0.1:9/__meet_disabled_mcp__"}' in argv
    assert "--ignore-user-config" not in argv


# --- MCP пользователя по уровню хода ---------------------------------------------------------------

_SERVERS = [{"name": "jira", "enabled": True, "transport": "stdio"},
            {"name": "atlas", "enabled": True, "transport": "streamable_http"},
            {"name": "plug", "enabled": True, "transport": "stdio"},          # сервер плагина: нет в config.toml
            {"name": "old", "enabled": False, "transport": "stdio"}]
_CONFIG = {"jira": ["jira_add_comment"], "atlas": [], "old": []}


def test_replica_turn_stubs_user_mcp_off(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.mcp(_SERVERS, _CONFIG)
    h.run("проверь", level=consent.NONE)
    argv = h.argv()
    assert 'mcp_servers.jira={enabled=false,command="__meet_disabled_mcp__"}' in argv
    assert 'mcp_servers.atlas={enabled=false,url="http://127.0.0.1:9/__meet_disabled_mcp__"}' in argv
    assert not any("approval_mode" in a for a in argv) and not any("mcp_servers.old" in a for a in argv)


@pytest.mark.parametrize("level", [consent.USER, consent.READ])
def test_request_turn_enables_user_mcp_and_codex_asks_about_every_call(monkeypatch, tmp_path, level):
    h = Harness(monkeypatch, tmp_path)
    h.mcp(_SERVERS, _CONFIG)
    h.run("проверь", level=level)
    argv = h.argv()
    assert 'mcp_servers.jira.default_tools_approval_mode="prompt"' in argv
    assert 'mcp_servers.jira.tools.jira_add_comment.approval_mode="prompt"' in argv
    assert 'mcp_servers.atlas.default_tools_approval_mode="prompt"' in argv
    assert not any(a.startswith(("mcp_servers.jira={", "mcp_servers.atlas={")) for a in argv)
    # Сервер, которому режим не задать ключом (плагин), выключен заглушкой.
    assert 'mcp_servers.plug={enabled=false,command="__meet_disabled_mcp__"}' in argv
    assert not any("mcp_servers.old" in a for a in argv) and "--ignore-user-config" not in argv


@pytest.mark.parametrize("config", [None, {"jira": ["bad.name"]}])
def test_request_turn_stubs_servers_it_cannot_make_ask(monkeypatch, tmp_path, config):
    h = Harness(monkeypatch, tmp_path)
    h.mcp(_SERVERS[:1], config)
    h.run("проверь")
    argv = h.argv()
    assert 'mcp_servers.jira={enabled=false,command="__meet_disabled_mcp__"}' in argv
    assert not any("approval_mode" in a for a in argv)


def test_user_mcp_read_only_tool_runs_without_a_card(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.mcp(_SERVERS[:2], _CONFIG, tools={"atlas": {"create_page_preview": True}})
    reply = h.run('MCP atlas create_page_preview {"title": "x"}')
    assert _decisions(reply) == ["accept"] and h.cards == []
    assert h.sent("mcpServerStatus/list") == [{"threadId": THREAD, "serverName": "atlas",
                                               "detail": "toolsAndAuthOnly"}]
    assert [(e["type"], e.get("decision")) for e in h.events] == [
        ("tool_use", None), ("gate", "allowed"), ("tool_result", None)]
    assert h.events[0]["name"] == "mcp__atlas__create_page_preview"


def test_user_mcp_change_asks_with_a_card_and_deny_declines(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["deny"])
    h.mcp(_SERVERS[:1], _CONFIG, tools={"jira": {"jira_add_comment": False}})
    reply = h.run('MCP jira jira_add_comment {"issue_key": "ABC-1", "body": "готово"}')
    assert _decisions(reply) == ["decline"]
    card = h.cards[0]
    assert card["tool"] == "mcp__jira__jira_add_comment" and card["kind"] == "mcp" and "готово" in card["args"]
    assert [(e["type"], e.get("decision")) for e in h.events] == [
        ("tool_use", None), ("gate", "declined"), ("tool_result", None)]
    assert h.events[-1]["ok"] is False


def test_user_mcp_allow_for_the_meeting_lets_the_same_call_through(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=[consent.ALLOW_MEETING])
    h.mcp(_SERVERS[:1], _CONFIG)
    call = 'MCP jira jira_add_comment {"issue_key": "ABC-1", "body": "%s"}'
    assert _decisions(h.run(call % "a")) == ["accept"]
    assert _decisions(h.run(call % "b")) == ["accept"] and len(h.cards) == 1
    assert "mcp:mcp__jira__jira_add_comment:issue_key=ABC-1" in h.gate.grants()


def test_explain_turn_reads_mcp_and_refuses_changes_without_a_card(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["allow"])
    h.mcp(_SERVERS[:1], _CONFIG)
    reply = h.run('MCP jira jira_get_issue {"issue_key": "ABC-1"}\n'
                  'MCP jira jira_add_comment {"issue_key": "ABC-1", "body": "x"}', level=consent.READ)
    assert _decisions(reply) == ["accept", "decline"] and h.cards == []


def test_mcp_question_is_a_card_and_refusal_declines(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["allow", "deny"])
    question = json.dumps({"serverName": "jira", "mode": "form", "message": "Перенести задачу?",
                           "requestedSchema": {"type": "object", "properties": {}}, "_meta": None},
                          ensure_ascii=False)
    form = json.dumps({"serverName": "jira", "mode": "form", "message": "Имя?",
                       "requestedSchema": {"type": "object", "properties": {"n": {"type": "string"}}}})
    reply = h.run(f"ELICIT {question}\nELICIT {question}\nELICIT {form}")
    assert [d["action"] for d in _decisions(reply)] == ["accept", "decline", "decline"]
    assert len(h.cards) == 2 and h.cards[0]["title"] == "MCP jira спрашивает"


def test_mcp_messages_match_the_saved_schema(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["allow"])
    h.mcp(_SERVERS[:1], _CONFIG, tools={"jira": {"jira_get_issue": True}})
    question = json.dumps({"serverName": "jira", "mode": "form", "message": "Да?",
                           "requestedSchema": {"type": "object", "properties": {}}, "_meta": None})
    h.run(f'MCP jira jira_get_issue {{"issue_key": "A-1"}}\nMCP jira jira_add_comment {{}}\n'
          f"ELICIT {question}")
    order = ["mcpServer/elicitation/request"] * 3
    answered = {f"srv-{n}": method for n, method in enumerate(order, 1)}
    messages = h.received()
    assert _check_outgoing(messages, answered) >= 8
    assert any(m.get("method") == "mcpServerStatus/list" for m in messages)


def test_without_a_gate_codex_still_uses_exec(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    asyncio.run(codex.run("привет", system_prompt="С", keep_session=True, access=consent.USER))
    assert h.exec_calls and not h.log.exists()


# --- строки хода и схема --------------------------------------------------------------------------


def test_tool_rows_arrive_in_order_while_the_turn_runs(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.run("RUN npm test")
    assert [e["type"] for e in h.events] == ["tool_use", "tool_result"]
    use, result = h.events
    assert use["name"] == "Bash" and use["input"] == {"command": "npm test"}
    assert result["ok"] is True and result["output"] == "вывод"


def _params_def(method: str) -> dict:
    schema = SCHEMA["client_request"]
    for variant in schema["oneOf"]:
        if variant["properties"]["method"]["enum"] == [method]:
            ref = variant["properties"]["params"]["$ref"].split("/")[-1]
            return schema["definitions"][ref]
    raise KeyError(method)


def _check_outgoing(messages: list[dict], answered: dict[str, str]) -> int:
    count = 0
    for msg in messages:
        if "method" in msg and "id" in msg:
            jsonschema.validate(msg, SCHEMA["client_request"])
            declared = set(_params_def(msg["method"]).get("properties", {}))
            assert set(msg["params"]) <= declared, (msg["method"], set(msg["params"]) - declared)
        elif "method" in msg:
            jsonschema.validate(msg, SCHEMA["client_notification"])
        elif "result" in msg:
            jsonschema.validate(msg["result"], SCHEMA["responses"][answered[msg["id"]]])
        else:
            assert set(msg) == {"id", "error"} and isinstance(msg["error"]["code"], int)
        assert "jsonrpc" not in msg
        count += 1
    return count


def test_outgoing_messages_match_the_saved_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CODEX_KNOWN", OTHER)
    h = Harness(monkeypatch, tmp_path, answers=["allow", consent.ALLOW_MEETING, "deny"])
    change = json.dumps([{"path": str(h.other / "n.txt"), "kind": {"type": "add"}, "diff": "+x\n"}])
    h.run(f"CMD rm -rf {h.other.as_posix()}/x\nCMD npm test\nPATCH {change}\n"
          "ASK mcpServer/elicitation/request\nASK item/tool/requestUserInput\nASK applyPatchApproval\n"
          "ASK item/permissions/requestApproval")
    h.run("дальше", resume=OTHER, level=consent.READ)
    order = ["item/commandExecution/requestApproval", "item/commandExecution/requestApproval",
             "item/fileChange/requestApproval", "mcpServer/elicitation/request", "item/tool/requestUserInput",
             "applyPatchApproval", "item/permissions/requestApproval"]
    answered = {f"srv-{n}": method for n, method in enumerate(order, 1)}
    messages = h.received()
    assert _check_outgoing(messages, answered) >= 12
    methods = [m.get("method") for m in messages if "method" in m]
    assert methods[:4] == ["initialize", "initialized", "thread/start", "turn/start"]
    assert "thread/resume" in methods


def test_schema_excerpt_covers_the_methods_the_client_uses():
    names = {v["properties"]["method"]["enum"][0] for v in SCHEMA["client_request"]["oneOf"]}
    assert names == {"initialize", "thread/start", "thread/resume", "turn/start", "turn/interrupt",
                     "mcpServerStatus/list", "model/list"}
    model = SCHEMA["model_list_response"]["definitions"]["Model"]
    assert {"id", "model", "displayName", "hidden", "isDefault", "defaultReasoningEffort",
            "supportedReasoningEfforts"} <= set(model["required"])
    assert "codex-cli 0.160.0" in SCHEMA["source"]
