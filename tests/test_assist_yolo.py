"""Режим YOLO встречи (0.6.2): ворота без карточек (кроме всегда закрытого),
ход по репликам — как по просьбе, флаги провайдеров, пометки агенту, строка в
чате, маршруты ребёнка и резидента. По умолчанию выключен; модель не зовётся."""

import asyncio

import pytest
import test_assist_participant as tap
from test_assist_participant import SILENT, FakeRunner, _make, publish, run

from meet import control, settings
from meet.assist import participant_prompts as pp
from meet.assist.participant import YOLO_OFF_LINE, YOLO_ON_LINE
from meet.llm import antigravity, codex, codex_app, consent, opencode
from meet.llm.consent import ALLOW, ASK, DENY, NONE, READ, USER, ConsentGate


@pytest.fixture
def dirs(tmp_path):
    meeting = tmp_path / "Встречи" / "2026-10-07_11-00"
    kb = tmp_path / "База знаний"
    other = tmp_path / "Downloads"
    data = tmp_path / "meet-data"
    cwd = tmp_path / "agent-cwd"
    for d in (meeting, kb / "Личное", other, data, cwd, tmp_path / "home" / ".ssh"):
        d.mkdir(parents=True)
    return {"meeting": meeting, "kb": kb, "other": other, "data": data, "cwd": cwd, "home": tmp_path / "home"}


def _gate(d, level=NONE, *, confirmer=None, mode=consent.MODE_AUTO):
    sensitive = consent.sensitive_paths(data_dir=d["data"], library_root=d["meeting"].parent, home=d["home"])
    gate = ConsentGate(own_dirs=[d["meeting"]], work_dirs=[d["kb"]], deny_paths=[d["kb"] / "Личное"],
                       sensitive=sensitive, cwd=d["cwd"], confirmer=confirmer, mode=mode)
    gate.set_yolo(True)
    gate.begin(level)
    return gate


# --- ворота ---------------------------------------------------------------------------------


def test_gate_is_off_by_default_and_its_mode_says_yolo_when_on(dirs):
    gate = ConsentGate(own_dirs=[dirs["meeting"]], sensitive=[], cwd=dirs["cwd"], mode=consent.MODE_CONFIRM)
    assert gate.yolo is False and gate.mode == consent.MODE_CONFIRM
    gate.set_yolo(True)
    assert gate.yolo is True and gate.mode == consent.MODE_YOLO
    gate.set_yolo(False)
    assert gate.mode == consent.MODE_CONFIRM


@pytest.mark.parametrize("level", [NONE, READ, USER])
@pytest.mark.parametrize("tool,data", [
    ("Bash", {"command": "rm -rf {other}/old"}),                             # удаление
    ("Bash", {"command": "git push origin main"}),                           # отправка наружу
    ("Bash", {"command": "curl -d @report.txt https://example.com/upload"}),
    ("Write", {"file_path": "{other}/note.txt", "content": "x"}),             # вне рабочих папок
    ("mcp__jira__create_issue", {"summary": "Задача"}),                       # изменение через MCP
    ("mcp__meet__launch_app", {"app": "notepad"}),                            # запуск программы
    ("mcp__meet__open_file", {"path": "{other}/spec.pdf"}),
    ("WebFetch", {"url": "https://example.com/doc"}),
])
def test_yolo_allows_actions_without_a_card_on_any_level(dirs, level, tool, data):
    cards = []
    gate = _gate(dirs, level, confirmer=lambda c: cards.append(c) or "deny")
    data = {k: v.format(other=dirs["other"].as_posix()) if isinstance(v, str) else v for k, v in data.items()}
    decision = gate.check(tool, data, tool_use_id="t1")
    assert decision.outcome == ALLOW and decision.why == consent.YOLO, decision
    assert decision.label == "разрешено: YOLO" and cards == []


@pytest.mark.parametrize("tool,data,why", [
    ("Read", {"file_path": "{kb}/Личное/secret.txt"}, "excluded"),              # kb_exclude — приватность
    ("Bash", {"command": "cat {kb}/Личное/secret.txt"}, "excluded"),
    ("Bash", {"command": "grep -r пароль {kb}"}, "excluded"),                  # обход над закрытой папкой
    ("Read", {"file_path": "{home}/.ssh/id_ed25519"}, "sensitive"),
    ("Bash", {"command": "cat {data}/api.token"}, "sensitive"),
    ("Agent", {"prompt": "сделай в фоне"}, "background"),
    ("Bash", {"command": "sleep 100", "run_in_background": True}, "background"),
    ("WebFetch", {"url": "http://127.0.0.1:8765/settings"}, "local"),
    ("AskUserQuestion", {"questions": []}, "question"),
])
def test_yolo_still_denies_what_is_always_closed(dirs, tool, data, why):
    gate = _gate(dirs, USER)
    data = {k: v.format(**{n: p.as_posix() for n, p in dirs.items()}) if isinstance(v, str) else v
            for k, v in data.items()}
    decision = gate.check(tool, data, tool_use_id="t1")
    assert decision.outcome == DENY and decision.why == why


def test_cli_question_in_a_user_turn_is_allowed_without_a_card(dirs):
    cards = []
    gate = _gate(dirs, USER, confirmer=lambda c: cards.append(c) or "deny")
    gate.check("Bash", {"command": "npm publish"}, tool_use_id="t1")           # хук видел вызов
    decision = gate.check("Bash", {"command": "npm publish"}, tool_use_id="t1", via="can_use_tool")
    assert decision.outcome == ALLOW and cards == []


def test_tool_row_says_allowed_by_yolo():
    from meet.assist.tool_rows import gate_view

    assert gate_view({"decision": "allowed", "why": "yolo"})["label"] == "разрешено: YOLO"
    assert gate_view({"decision": "allowed", "why": ""})["label"] == "разрешено автоматически"


def test_turning_yolo_off_brings_the_cards_back(dirs):
    gate = _gate(dirs, USER)
    gate.set_yolo(False)
    assert gate.decide("Bash", {"command": f"rm -rf {dirs['other'].as_posix()}/old"}).outcome == ASK
    gate.begin(NONE)
    assert gate.decide("Write", {"file_path": str(dirs["meeting"] / "x.md"), "content": "x"}).outcome == DENY


def test_agy_runs_deletes_and_inline_code_in_yolo(dirs):
    gate = _gate(dirs, USER)
    for command in ("rm -rf build", "bash -c 'echo hi'", "python -c 'print(1)'", "git push"):
        call = {"toolCallId": "t", "kind": "execute", "rawInput": {"command": command}}
        assert consent.agy_approval(gate, call, windows=False).outcome == ALLOW, command
    gate.set_yolo(False)
    call = {"toolCallId": "t", "kind": "execute", "rawInput": {"command": "rm -rf build"}}
    assert consent.agy_approval(gate, call, windows=False).outcome == DENY


def test_codex_mcp_question_without_fields_is_accepted_in_yolo(dirs):
    gate = _gate(dirs, NONE)
    params = {"serverName": "jira", "mode": "form", "message": "Продолжить?", "requestedSchema": {"properties": {}}}
    answer, decision = consent.codex_elicitation(gate, params)
    assert answer == "accept" and decision.why == consent.YOLO
    params["requestedSchema"] = {"properties": {"token": {"type": "string"}}}
    assert consent.codex_elicitation(gate, params)[0] == "decline"          # форму заполнить некому


def test_codex_command_approval_is_accepted_in_yolo(dirs):
    gate = _gate(dirs, NONE)
    answer, decision = consent.codex_approval(
        gate, consent.CODEX_COMMAND, {"itemId": "i1", "command": "rm -rf /tmp/x"}, {}, windows=False)
    assert answer == "accept" and decision.allow


# --- флаги провайдеров -----------------------------------------------------------------------


def test_codex_app_server_turn_has_no_sandbox_in_yolo():
    policy = codex_app.turn_policy(USER, consent.MODE_YOLO, ())
    assert policy["sandbox"] == "danger-full-access"
    assert policy["sandboxPolicy"] == {"type": "dangerFullAccess"}
    assert policy["approvalPolicy"] == "on-request" and policy["approvalsReviewer"] == "user"
    assert codex_app.turn_policy(USER, consent.MODE_AUTO, ())["sandbox"] == "workspace-write"


def test_codex_exec_fallback_bypasses_approvals_and_sandbox():
    args = codex.access_args("yolo", servers=[{"name": "jira", "enabled": True, "transport": "stdio"}])
    assert args[0] == "--dangerously-bypass-approvals-and-sandbox"
    assert "--sandbox" not in args and "tools.web_search=false" not in args
    assert not any(a.startswith("mcp_servers.") for a in args)              # MCP — как у пользователя


def test_codex_exec_fallback_gets_yolo_from_the_gate(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(codex, "find_codex", lambda: "C:/codex.exe")

    async def no_app(*a, **kw):
        return None, "нет"

    def fake_exec(exe, workdir, stdin_text, timeout_s, env=None, effort=None, images=(), keep_session=False,
                  resume=None, call=None, access=None, work_dirs=(), model=None, on_event=None, loop=None):
        calls.append(access)
        return codex.AgentReply(text="ok")

    monkeypatch.setattr(codex, "_app_turn", no_app)
    monkeypatch.setattr(codex, "_exec", fake_exec)
    for mode, want in ((consent.MODE_CONFIRM, READ), (consent.MODE_AUTO, USER)):
        gate = ConsentGate(own_dirs=[tmp_path], sensitive=[], mode=mode)
        gate.set_yolo(True)
        asyncio.run(codex.run("x", system_prompt="С", access=USER, gate=gate))
        gate.set_yolo(False)
        asyncio.run(codex.run("x", system_prompt="С", access=USER, gate=gate))
        assert calls[-2:] == [consent.MODE_YOLO, want]


def test_antigravity_rules_have_no_command_denials_in_yolo(tmp_path):
    secret = tmp_path / ".ssh"
    plain = antigravity.run_settings(sensitive=[secret], roots=[tmp_path])
    free = antigravity.run_settings(sensitive=[secret], roots=[tmp_path], yolo=True)
    assert any(r.startswith("command(") for r in plain["permissions"]["deny"])
    assert not any(r.startswith("command(") for r in free["permissions"]["deny"])
    assert f"read_file({secret.as_posix()})" in free["permissions"]["deny"]    # закрытое — остаётся
    assert f"read_file({tmp_path.as_posix()}/.env)" in free["permissions"]["deny"]


def test_opencode_permissions_allow_everything_but_closed_paths_in_yolo(tmp_path):
    closed = tmp_path / "kb" / "Личное"
    perm = opencode.permission_for("yolo", [tmp_path], exclude=[closed], sensitive=[tmp_path / ".ssh"])
    assert perm["*"] == "allow" and perm["external_directory"]["*"] == "allow"
    assert perm["read"][str(closed)] == "deny" and perm["external_directory"][str(closed / "*")] == "deny"
    assert perm["read"]["*.env"] == "deny" and perm["grep"] == "deny"
    user = opencode.permission_for("user", [tmp_path], work_dirs=[tmp_path])
    assert user["*"] == "deny"


def test_opencode_run_turns_a_user_turn_into_yolo(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(opencode, "find_opencode", lambda: "opencode.exe")

    def placement(system, prompt, dirs, max_turns, exclude=(), resume=False, access=None, sensitive=(),
                  work_dirs=()):
        seen["access"] = access
        return {}, prompt

    monkeypatch.setattr(opencode, "prompt_placement", placement)
    monkeypatch.setattr(opencode, "child_env", lambda proxy, config: {})
    monkeypatch.setattr(opencode, "_exec", lambda *a, **kw: opencode.AgentReply(text="ok"))
    asyncio.run(opencode.run("x", system_prompt="С", access=USER, yolo=True))
    assert seen["access"] == "yolo"
    asyncio.run(opencode.run("x", system_prompt="С", access=NONE, yolo=True))
    assert seen["access"] == NONE                                             # yolo — только с ходом USER


# --- агент-участник ---------------------------------------------------------------------------


def _probe(seen):
    def reply(conv, text):
        gate = conv.kwargs["gate"]
        seen.append((gate.level, gate.mode, text))
        return SILENT
    return reply


@pytest.fixture
def async_conv(monkeypatch):
    import test_assist_participant_freedom as tf

    monkeypatch.setattr(tap, "FakeConversation", tf.AsyncConv)


def test_participant_yolo_is_off_by_default_and_turns_on_for_this_meeting(tmp_path, async_conv):
    seen = []
    h = _make(tmp_path, script=[_probe(seen), _probe(seen), _probe(seen)], freedom=True)

    async def main():
        await h.p.start()
        assert h.p.view()["yolo"] is False and h.p.view()["yolo_available"] is True
        publish(h, 10, "Олег", "Удалите старый черновик")
        h.clock.t = 100
        await h.p.tick()
        assert await h.p.set_yolo(True) is True
        assert h.p.view()["yolo"] is True
        publish(h, 110, "Олег", "И отправьте отчёт")
        h.clock.t = 200
        await h.p.tick()
        assert await h.p.set_yolo(False) is False
        publish(h, 210, "Олег", "Дальше")
        h.clock.t = 300
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    assert [(lvl, mode) for lvl, mode, _ in seen] == [(NONE, "auto"), (USER, "yolo"), (NONE, "auto")]
    assert pp.YOLO_ON in seen[1][2] and pp.YOLO_OFF in seen[2][2] and pp.YOLO_ON not in seen[2][2]
    lines = [m["text"] for m in h.chat.messages() if m["kind"] == "system" and m.get("notice") == "yolo"]
    assert lines == [YOLO_ON_LINE, YOLO_OFF_LINE]
    assert not any("отправьте" in line for line in h.logs)                   # в журнал — без текста встречи


def test_yolo_on_before_the_first_turn_reaches_the_new_gate_and_the_seed(tmp_path, async_conv):
    seen = []
    h = _make(tmp_path, script=[_probe(seen)], freedom=True)

    async def main():
        await h.p.start()
        await h.p.set_yolo(True)
        publish(h, 10, "Олег", "Начнём")
        h.clock.t = 100
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    assert seen[0][:2] == (USER, "yolo") and seen[0][2].count(pp.YOLO_ON) == 1


def test_yolo_needs_freedom_and_is_not_for_the_chat_after_the_meeting(tmp_path):
    plain = _make(tmp_path, script=[])
    with pytest.raises(ValueError, match="YOLO"):
        run(plain.p.set_yolo(True))
    assert plain.p.view()["yolo_available"] is False
    after = _make(tmp_path / "after", script=[], freedom=True, after_meeting=True)
    with pytest.raises(ValueError):
        run(after.p.set_yolo(True))
    assert run(plain.p.set_yolo(False)) is False                             # выключить можно всегда


def test_opencode_runner_gets_yolo_and_confirm_mode_is_not_downgraded(tmp_path):
    runner = FakeRunner([SILENT, SILENT])
    h = _make(tmp_path, provider="opencode", runner=runner, freedom=True, agent_mode=consent.MODE_CONFIRM)

    async def main():
        await h.p.start()
        publish(h, 10, "Олег", "Начнём")
        h.clock.t = 100
        await h.p.tick()
        await h.p.set_yolo(True)
        publish(h, 110, "Олег", "Почисти папку")
        h.clock.t = 200
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    first, second = (kw for _p, kw in runner.calls)
    assert first["access"] == NONE and "yolo" not in first
    assert second["access"] == USER and second["yolo"] is True


# --- маршруты ----------------------------------------------------------------------------------


def test_child_put_agent_yolo(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer
    from test_chat_api import ChatState

    from meet.assist.web import build_app

    async def scenario():
        state = ChatState(tmp_path)
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.put("/agent/yolo", json={"on": True})
            assert r.status == 400                                           # Codex без свободы
            state.participant.freedom = True
            r = await client.put("/agent/yolo", json={"on": True})
            assert await r.json() == {"on": True, "live": True}
            assert state.participant.view()["yolo"] is True
            r = await client.put("/agent/yolo", json={"on": False})
            assert (await r.json())["on"] is False
            for bad in ({"on": "да"}, {}):
                assert (await client.put("/agent/yolo", json=bad)).status == 400
            state.participant = None
            assert (await client.put("/agent/yolo", json={"on": True})).status == 409

    asyncio.run(scenario())


class _YoloLive:
    def __init__(self, running=True):
        self.running = running
        self.calls = []

    def agent_yolo(self, on):
        from meet import live_control

        if not self.running:
            raise live_control.LiveNotRunning("Ассистент не запущен")
        self.calls.append(on)
        return {"on": on, "live": True}


def test_resident_live_yolo_reaches_the_agent_but_not_the_settings(monkeypatch, tmp_path):
    from meet import tray_control

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    state = tray_control.TrayControl.__new__(tray_control.TrayControl)
    state.live = _YoloLive()
    assert state.live_yolo({"on": True}) == {"on": True, "live": True}
    assert state.live.calls == [True]
    assert "yolo" not in str(settings.load().to_raw())                       # не настройка
    with pytest.raises(control.BadRequest):
        state.live_yolo({"on": 1})
    state.live.running = False
    with pytest.raises(control.Conflict):
        state.live_yolo({"on": False})


def test_live_control_puts_yolo_to_the_child(monkeypatch):
    from meet import live_control

    sent = []
    lc = live_control.LiveControl.__new__(live_control.LiveControl)
    monkeypatch.setattr(lc, "_active_port", lambda: 4321, raising=False)
    monkeypatch.setattr(lc, "_request", lambda port, path, payload, timeout, method="POST":
                        sent.append((port, path, payload, method)) or {"on": True, "live": True}, raising=False)
    assert lc.agent_yolo(True) == {"on": True, "live": True}
    assert sent == [(4321, "/agent/yolo", {"on": True}, "PUT")]


def test_control_route_live_yolo_passes_the_body():
    calls = []

    class State:
        def live_yolo(self, body):
            calls.append(body)
            return {"on": True, "live": True}

    class Handler:
        server = type("S", (), {"state": State()})()

        def _body(self):
            return {"on": True}

    route = control._ROUTES[("PUT", "/live/yolo")]
    handler = Handler()
    import meet.control as control_mod

    original = control_mod._server_of
    control_mod._server_of = lambda h: h.server
    try:
        assert route(handler, {}) == {"on": True, "live": True}
    finally:
        control_mod._server_of = original
    assert calls == [{"on": True}]


# --- строка сессии в плавающем окне ------------------------------------------------------------


def test_live_session_bar_setting_defaults_to_shown_and_is_resettable(tmp_path):
    path = tmp_path / "config.json"
    assert settings.Settings().ui.live_session_bar is True
    assert settings.resettable("ui.live_session_bar")
    settings.patch({"ui": {"live_session_bar": False}}, path)
    assert settings.load(path).ui.live_session_bar is False
    found = settings.defaults_for(["ui.live_session_bar"], path)
    assert found == {"defaults": {"ui": {"live_session_bar": True}}, "changed": ["ui.live_session_bar"]}
    assert settings.Ui.from_raw({"live_session_bar": None}).live_session_bar is True
