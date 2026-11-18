"""Antigravity CLI (`agy`) через `agy acp` (Agent Client Protocol): ход с
воротами и карточками Meet, разовые правила, MCP по уровню хода, продолжение
и удаление сеансов, запасной путь `-p --output-format stream-json`.

Собеседник — поддельный сервер `fake_agy_acp.py` (Python, модель не зовётся)."""

import asyncio
import json
import sys
from pathlib import Path

import psutil
import pytest

from meet.llm import antigravity as agy
from meet.llm import consent, opencode

FAKE = Path(__file__).with_name("fake_agy_acp.py")
SID = "a1b2c3d4-0000-4000-8000-000000000001"
OTHER = "a1b2c3d4-0000-4000-8000-0000000000ff"


class Harness:
    def __init__(self, monkeypatch, tmp_path, *, mode=consent.MODE_AUTO, answers=(), gate=True) -> None:
        self.tmp = tmp_path
        self.log = tmp_path / "peer.jsonl"
        self.meeting = tmp_path / "meeting"
        self.kb = tmp_path / "kb"
        self.other = tmp_path / "other"
        self.user_cfg = tmp_path / "user-gemini" / "config"
        for d in (self.meeting, self.kb, self.other, self.user_cfg):
            d.mkdir(parents=True)
        self.cards: list[dict] = []
        self.answers = list(answers)
        self.events: list[dict] = []
        self.lines: list[str] = []
        self.print_calls: list[dict] = []
        monkeypatch.setenv("FAKE_AGY_LOG", str(self.log))
        monkeypatch.setattr(agy, "find_agy", lambda: "C:/agy/bin/agy.exe")
        monkeypatch.setattr(agy, "_ON_WINDOWS", False)          # команды — как Bash на любой ОС
        monkeypatch.setattr(agy, "home", lambda: tmp_path / "agy-home")
        monkeypatch.setattr(agy, "keep_dir", lambda: str(tmp_path / "keep"))
        monkeypatch.setattr(agy, "user_config_dir", lambda: self.user_cfg)
        monkeypatch.setattr(agy, "command", lambda exe, flags: [sys.executable, str(FAKE), *flags])

        def fake_print(exe, argv, stdin_text, timeout_s, env, call):
            self.print_calls.append({"argv": argv, "stdin": stdin_text})
            return 0, json.dumps({"type": "result", "result": "через -p"}) + "\n", ""

        monkeypatch.setattr(agy, "_print_run", fake_print)
        self.monkeypatch = monkeypatch
        self.gate = consent.ConsentGate(own_dirs=[self.meeting], work_dirs=[self.kb], sensitive=[],
                                        cwd=tmp_path / "keep", confirmer=self._confirm,
                                        mode=mode) if gate else None

    def _confirm(self, card: dict) -> str:
        self.cards.append(card)
        return self.answers.pop(0) if self.answers else "deny"

    def run(self, text: str, *, level=consent.USER, resume=None, keep_session=True, timeout_s=30.0,
            model=None, **kw):
        if self.gate is not None:
            self.gate.begin(level)
        try:
            return asyncio.run(agy.run(
                text, system_prompt="СИСТЕМА", resume=resume, keep_session=keep_session and not resume,
                allowed_dirs=(str(self.meeting), str(self.kb)),
                access=level if self.gate is not None else None,
                work_dirs=(str(self.meeting), str(self.kb)), gate=self.gate, model=model,
                on_event=self.events.append, log=self.lines.append, timeout_s=timeout_s, **kw))
        finally:
            if self.gate is not None:
                self.gate.end()

    def entries(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def head(self) -> dict:
        return self.entries()[0]

    def sent(self, method: str) -> list[dict]:
        return [e["in"]["params"] for e in self.entries() if "in" in e and e["in"].get("method") == method]

    def mcp_config(self, servers: dict) -> None:
        (self.user_cfg / "mcp_config.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def _decisions(reply) -> list:
    return json.loads(reply.text)


# --- запуск: своя папка данных, разовые правила, без пропуска разрешений ---------------------


@pytest.mark.parametrize("level", [consent.NONE, consent.READ, consent.USER])
def test_every_level_runs_isolated_with_one_run_rules_and_never_skips_permissions(monkeypatch, tmp_path, level):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("привет", level=level)
    assert reply.error is None and reply.text == "ok"
    head = h.head()
    argv = head["argv"]
    assert "--dangerously-skip-permissions" not in argv
    assert argv[argv.index("--gemini-dir") + 1] == str(tmp_path / "agy-home")
    run = argv[argv.index("--cli_dir_name") + 1]
    settings = head["settings"]
    assert settings["agentMode"] == "" and settings["toolPermission"] == "strict"
    assert "allow" not in settings["permissions"]
    # Разовый файл правил убран после хода.
    assert not (tmp_path / "agy-home" / "antigravity-cli" / run).exists()


def test_add_dir_by_level(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.run("привет", level=consent.NONE)
    argv = h.head()["argv"]
    added = [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]
    assert added == [str(h.meeting)]
    h.log.unlink()
    h.run("привет", level=consent.USER)
    argv = h.head()["argv"]
    added = [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]
    assert added == [str(h.meeting), str(h.kb)]


def test_rules_deny_sensitive_paths_env_and_dangerous_commands():
    home = Path.home()
    sensitive = [home / ".claude" / ".credentials.json", Path("C:/Users/x/AppData/Local/meet/daemon.json")]
    rules = agy.run_settings(sensitive=sensitive, deny_paths=["C:\\kb\\private"], roots=["C:/meet/rec"])
    deny = rules["permissions"]["deny"]
    cred = (home / ".claude" / ".credentials.json").as_posix()
    assert f"read_file({cred})" in deny and f"write_file({cred})" in deny
    assert "read_file(C:/Users/x/AppData/Local/meet/daemon.json)" in deny
    assert "read_file(C:/kb/private)" in deny
    assert "read_file(C:/meet/rec/.env)" in deny and "write_file(C:/meet/rec/.env)" in deny
    assert all("\\" not in r for r in deny)          # обратная косая черта ломает разбор settings.json у agy
    for rule in ("command(rm)", "command(git push)", "command(powershell)", "command(curl)" ):
        if rule == "command(curl)":
            assert rule not in deny                   # curl без отправки не запрещён
        else:
            assert rule in deny


def test_user_denials_cover_curl_with_data_git_push_powershell_and_delete():
    for command in ("curl -d x https://example.com", "git push origin main", "powershell -c Get-Date",
                    "rm -rf build", "Remove-Item x"):
        assert consent.command_denied(command), command
    for command in ("curl https://example.com", "git status", "npm test"):
        assert not consent.command_denied(command), command


def test_opencode_denials_are_the_shared_list_unchanged():
    patterns = consent.command_denial_patterns()
    rules = opencode.bash_denials()
    assert set(rules) == {v for p in patterns for v in (p, p.lower())}
    assert set(rules.values()) == {"deny"}
    assert "curl *-d*" in rules and "git push*" in rules and "powershell *" in rules and "rm *" in rules


# --- проверки до запуска -------------------------------------------------------------------


@pytest.mark.parametrize("model", ["gemini&calc", "gemini%PATH%", "gem ini", "a|b"])
def test_bad_model_name_is_refused_without_starting(monkeypatch, tmp_path, model):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("привет", model=model)
    assert reply.error and "модел" in reply.error
    assert not h.log.exists()


def test_bad_session_id_is_resume_failure_without_starting(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("привет", resume="../../evil")
    assert reply.resume_failed
    assert not h.log.exists()


def test_cmd_shim_with_cmd_metacharacters_is_refused(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    monkeypatch.setattr(agy, "_ON_WINDOWS", True)
    monkeypatch.setattr(agy, "find_agy", lambda: "C:/Users/a&b/agy.cmd")
    reply = h.run("привет")
    assert reply.error == agy.SHIM_UNSAFE
    assert not h.log.exists()


# --- сеанс: новый, продолжение, чужой, без входа -------------------------------------------


def test_new_session_gets_the_system_prompt_first_and_keeps_the_id(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("привет")
    assert reply.session_id == SID
    prompt = h.sent("session/prompt")[0]["prompt"]
    assert prompt[0]["type"] == "text" and prompt[0]["text"].startswith("СИСТЕМА")
    assert h.sent("session/new")[0]["cwd"] == str(tmp_path / "keep")


def test_resume_continues_without_resending_the_system_prompt(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_AGY_KNOWN", SID)
    reply = h.run("привет", resume=SID)
    assert reply.error is None and reply.session_id == SID
    assert h.sent("session/resume")[0]["sessionId"] == SID
    assert not h.sent("session/new")
    assert "СИСТЕМА" not in h.sent("session/prompt")[0]["prompt"][0]["text"]


def test_unknown_session_is_resume_failure(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("привет", resume=OTHER)
    assert reply.resume_failed and not h.sent("session/prompt")


def test_other_session_continued_is_resume_failure(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_AGY_KNOWN", SID)
    monkeypatch.setenv("FAKE_AGY_OTHER", "1")
    reply = h.run("привет", resume=SID)
    assert reply.resume_failed and not h.sent("session/prompt")


def test_not_logged_in_is_a_clear_text_without_hanging(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_AGY_AUTH", "1")
    reply = h.run("привет", timeout_s=20)
    assert reply.error and "вход" in reply.error.lower() and "--login" in reply.error
    assert not h.print_calls                       # без входа и запасной путь не поможет


def test_session_in_auto_mode_is_switched_to_default(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_AGY_MODE", "auto")
    h.run("привет")
    assert h.sent("session/set_mode") == [{"sessionId": SID, "modeId": "default"}]


def test_model_is_set_for_the_session(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.run("привет", model="gemini-3-pro")
    assert h.sent("session/set_model") == [{"sessionId": SID, "modelId": "gemini-3-pro"}]


# --- ворота согласия: уровни и карточки ----------------------------------------------------


def test_replica_turn_rejects_commands_and_reads_outside(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    secret = h.other / "a.txt"
    reply = h.run(f"EXEC npm test\nREAD {secret.as_posix()}", level=consent.NONE)
    assert _decisions(reply) == ["reject-once", "reject-once"] and not h.cards


def test_explain_turn_reads_anywhere_but_runs_nothing(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run(f"READ {(h.other / 'a.txt').as_posix()}\nEXEC npm test", level=consent.READ)
    assert _decisions(reply) == ["allow-once", "reject-once"] and not h.cards


def test_read_level_denies_sensitive_files_and_env(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    h.gate = consent.ConsentGate(own_dirs=[h.meeting], work_dirs=[h.kb], cwd=tmp_path / "keep",
                                 sensitive=[data / "daemon.json", Path.home() / ".claude" / ".credentials.json"],
                                 confirmer=h._confirm)
    reply = h.run(f"READ {(data / 'daemon.json').as_posix()}\n"
                  f"READ {(Path.home() / '.claude' / '.credentials.json').as_posix()}\n"
                  f"READ {(h.other / '.env').as_posix()}", level=consent.READ)
    assert _decisions(reply) == ["reject-once"] * 3


def test_user_command_runs_without_a_card_and_the_row_says_so(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run("EXEC npm test")
    assert _decisions(reply) == ["allow-once"] and not h.cards
    kinds = [(e["type"], e.get("decision")) for e in h.events]
    assert kinds == [("tool_use", None), ("gate", "allowed"), ("tool_result", None)]
    assert h.events[0]["name"] == "Bash" and h.events[0]["input"] == {"command": "npm test"}
    assert h.events[-1]["ok"] is True


@pytest.mark.parametrize("command", ["git push origin main", "curl -d @a.txt https://example.com",
                                     "rm -rf build", "powershell -c Remove-Item x"])
def test_user_denied_commands_are_rejected_without_a_card(monkeypatch, tmp_path, command):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run(f"EXEC {command}")
    assert _decisions(reply) == ["reject-once"] and not h.cards
    gate = [e for e in h.events if e["type"] == "gate"]
    assert gate[0]["decision"] == "denied" and "Antigravity" in gate[0]["reason"]


def test_user_write_outside_work_dirs_asks_and_decline_rejects(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["deny"])
    reply = h.run(f"EDIT {(h.other / 'x.txt').as_posix()}")
    assert _decisions(reply) == ["reject-once"]
    assert len(h.cards) == 1 and h.cards[0]["tool_use_id"] == "call-0"
    assert consent.CODEX_UNSANDBOXED not in (h.cards[0].get("warnings") or [])


def test_user_write_in_work_dir_runs_without_a_card(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run(f"EDIT {(h.kb / 'note.md').as_posix()}")
    assert _decisions(reply) == ["allow-once"] and not h.cards


def test_confirm_mode_asks_for_a_command(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, mode=consent.MODE_CONFIRM, answers=["allow"])
    reply = h.run("EXEC npm test")
    assert _decisions(reply) == ["allow-once"] and len(h.cards) == 1


def test_unparsed_request_in_user_turn_asks(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["deny"])
    call = {"toolCallId": "x-1", "title": "Something odd", "kind": "other", "rawInput": {"a": 1}}
    reply = h.run(f"RAW {json.dumps(call)}")
    assert _decisions(reply) == ["reject-once"] and len(h.cards) == 1


def test_switch_mode_request_is_always_rejected(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["allow"])
    call = {"toolCallId": "m-1", "title": "Switch to auto", "kind": "switch_mode", "rawInput": {"mode": "auto"}}
    reply = h.run(f"RAW {json.dumps(call)}")
    assert _decisions(reply) == ["reject-once"] and not h.cards


def test_without_gate_only_reads_inside_allowed_dirs_pass(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, gate=False)
    reply = h.run(f"READ {(h.kb / 'a.md').as_posix()}\nREAD {(h.other / 'b.md').as_posix()}\nEXEC ls")
    assert _decisions(reply) == ["allow-once", "reject-once", "reject-once"]


# --- MCP по уровню хода --------------------------------------------------------------------


def test_replica_turn_passes_no_mcp_servers(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.mcp_config({"jira": {"command": "jira-mcp", "args": ["--x"], "env": {"TOKEN": "t"}}})
    h.run("привет", level=consent.NONE)
    assert h.sent("session/new")[0]["mcpServers"] == []


def test_user_turn_passes_the_users_mcp_servers(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    h.mcp_config({"jira": {"command": "jira-mcp", "args": ["--x"], "env": {"TOKEN": "t"}},
                  "off": {"command": "x", "disabled": True},
                  "atlas": {"url": "https://atlas.example/mcp", "headers": {"Authorization": "Bearer t"}}})
    h.run("привет", level=consent.USER)
    servers = h.sent("session/new")[0]["mcpServers"]
    assert {"name": "jira", "command": "jira-mcp", "args": ["--x"],
            "env": [{"name": "TOKEN", "value": "t"}]} in servers
    assert all(s["name"] != "off" for s in servers)


def test_mcp_read_only_tool_runs_without_a_card(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run('MCP jira get_issue {"issueKey": "ABC-1"} ro')
    assert _decisions(reply) == ["allow-once"] and not h.cards
    use = [e for e in h.events if e["type"] == "tool_use"][0]
    assert use["name"] == "mcp__jira__get_issue"


def test_mcp_write_asks_and_decline_rejects(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=["deny"])
    reply = h.run('MCP jira add_comment {"issueKey": "ABC-1", "body": "x"}')
    assert _decisions(reply) == ["reject-once"]
    assert len(h.cards) == 1 and h.cards[0]["kind"] == "mcp"


def test_mcp_write_allowed_for_the_meeting_repeats_without_a_card(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, answers=[consent.ALLOW_MEETING])
    call = 'MCP jira add_comment {"issueKey": "ABC-1", "body": "x"}'
    assert _decisions(h.run(call)) == ["allow-once"]
    assert _decisions(h.run(call)) == ["allow-once"] and len(h.cards) == 1


def test_mcp_in_replica_turn_is_rejected(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    reply = h.run('MCP jira get_issue {"issueKey": "ABC-1"} ro', level=consent.NONE)
    assert _decisions(reply) == ["reject-once"] and not h.cards


# --- отмена, разовые вызовы, удаление сеанса ------------------------------------------------


def test_cancel_sends_session_cancel_and_kills_the_tree(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    monkeypatch.setattr(agy, "CANCEL_GRACE_S", 0.5)

    async def scenario():
        h.gate.begin(consent.USER)
        task = asyncio.ensure_future(agy.run("HANG", system_prompt="С", keep_session=True,
                                             allowed_dirs=(str(h.meeting),), access=consent.USER,
                                             work_dirs=(str(h.meeting),), gate=h.gate, timeout_s=30))
        for _ in range(200):
            await asyncio.sleep(0.05)
            if h.sent("session/prompt"):
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        h.gate.end()

    asyncio.run(scenario())
    assert h.sent("session/cancel") == [{"sessionId": SID}]
    pid = h.head()["pid"]
    for _ in range(100):
        if not psutil.pid_exists(pid):
            break
        asyncio.run(asyncio.sleep(0.05))
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_one_off_call_closes_and_deletes_its_session(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, gate=False)
    conv = tmp_path / "agy-home" / "antigravity-cli" / "conversations"
    brain = tmp_path / "agy-home" / "antigravity-cli" / "brain" / SID
    conv.mkdir(parents=True)
    brain.mkdir(parents=True)
    (conv / f"{SID}.db").write_text("x")
    (conv / f"{SID}.db-wal").write_text("x")
    (conv / "keep-me.db").write_text("x")
    reply = h.run("привет", keep_session=False)
    assert reply.session_id is None
    assert h.sent("session/close") == [{"sessionId": SID}]
    assert not (conv / f"{SID}.db").exists() and not (conv / f"{SID}.db-wal").exists()
    assert not brain.exists() and (conv / "keep-me.db").exists()


def test_forget_session_only_takes_valid_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(agy, "home", lambda: tmp_path)
    conv = tmp_path / "antigravity-cli" / "conversations"
    conv.mkdir(parents=True)
    (conv / f"{SID}.db").write_text("x")
    assert agy.forget_session("../x") == 0
    assert agy.forget_session(SID) == 1
    assert not (conv / f"{SID}.db").exists()


def test_llm_forget_session_dispatches_to_agy(monkeypatch):
    from meet import llm

    seen = []
    monkeypatch.setattr(agy, "forget_session", lambda sid: seen.append(sid) or 1)
    assert llm.forget_session("agy", SID) == 1 and seen == [SID]


# --- запасной путь: -p --output-format stream-json -----------------------------------------


@pytest.mark.parametrize("init", ["deaf", "error", "exit"])
def test_acp_unavailable_falls_back_to_print_mode(monkeypatch, tmp_path, init):
    h = Harness(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_AGY_INIT", init)
    monkeypatch.setattr(agy, "INIT_TIMEOUT_S", 1.0)
    reply = h.run("привет", level=consent.NONE)
    assert reply.text == "через -p" and h.print_calls
    argv = h.print_calls[0]["argv"]
    assert argv[:6] == ["-p", "", "--input-format", "text", "--output-format", "stream-json"]
    assert "--settings" in argv and "--dangerously-skip-permissions" not in argv
    assert h.print_calls[0]["stdin"].startswith("СИСТЕМА")
    assert any("agy acp не подошёл" in line for line in h.lines)


def test_stream_json_init_steps_and_result():
    out = "\n".join(json.dumps(e) for e in (
        {"type": "init", "session_id": SID, "tools": []},
        {"type": "step_update", "step_type": "tool", "step_index": 1, "tool_name": "run_command",
         "tool_call": {"CommandLine": "npm test"}, "status": "running"},
        {"type": "step_update", "step_type": "tool", "step_index": 1, "tool_name": "run_command",
         "status": "done", "result": "42 passed"},
        {"type": "step_update", "step_type": "text", "text": "част"},
        {"type": "result", "result": "Готово", "session_id": SID},
    ))
    parsed = agy.parse_stream(out, "")
    assert parsed.session_id == SID and parsed.text == "Готово"
    uses = [e for e in parsed.events if e["type"] == "tool_use"]
    results = [e for e in parsed.events if e["type"] == "tool_result"]
    assert len(uses) == 1 and uses[0]["input"] == {"command": "npm test"}
    assert results and results[0]["ok"] is True


def test_stream_json_soft_denial_becomes_a_row():
    err = ("1 tool(s) required approval that headless mode cannot prompt for, so they were auto-denied. "
           "Settings allow-rules do not apply; re-run with --dangerously-skip-permissions to auto-approve all tools.")
    parsed = agy.parse_stream(json.dumps({"type": "result", "result": "Не смог"}), err)
    rows = [e for e in parsed.events if e["type"] == "tool_result"]
    assert rows and rows[0]["ok"] is False and rows[0]["output"] == agy.SOFT_DENIED


def test_print_mode_other_session_is_resume_failure(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_AGY_INIT", "exit")

    def fake_print(exe, argv, stdin_text, timeout_s, env, call):
        return 0, json.dumps({"type": "init", "session_id": OTHER}) + "\n" + json.dumps(
            {"type": "result", "result": "x"}) + "\n", ""

    monkeypatch.setattr(agy, "_print_run", fake_print)
    reply = h.run("привет", resume=SID)
    assert reply.resume_failed


# --- вход ---------------------------------------------------------------------------------


def test_login_status_reads_auth_required_without_a_model(monkeypatch, tmp_path):
    monkeypatch.setattr(agy, "home", lambda: tmp_path / "agy-home")
    monkeypatch.setattr(agy, "command", lambda exe, flags: [sys.executable, str(FAKE), *flags])
    monkeypatch.setenv("FAKE_AGY_LOG", str(tmp_path / "peer.jsonl"))
    monkeypatch.setenv("FAKE_AGY_AUTH", "1")
    ok, why = agy.login_status("C:/agy.exe")
    assert ok is False and "--login" in why
    monkeypatch.delenv("FAKE_AGY_AUTH")
    ok, why = agy.login_status("C:/agy.exe")
    assert ok is True and why is None
    methods = [json.loads(line).get("in", {}).get("method") for line in
               (tmp_path / "peer.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "session/prompt" not in methods and "session/close" in methods

