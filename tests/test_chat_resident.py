"""Чат агента-участника в резиденте (V4 задача 6): прокси к ребёнку
(`LiveControl`), маршруты control API, «Продолжить разговор» после встречи
(`GET/POST /recordings/{id}/chat`, `jobs.CHAT`), «Как часто писать», «Только
сводка».

Ребёнок — настоящее aiohttp-приложение `meet.assist.web` в своём потоке с
настоящим `Participant` (поддельный runner), модель не зовётся. Конфиг и
записи — во временной папке."""

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from meet import control, events, jobs, library, live_control, settings, tray, tray_control
from meet.assist.chatlog import ChatLog

from test_chat_api import ChatState, _png

RID = "2026-10-07_10-00"


# --- ребёнок в своём потоке ---------------------------------------------------------


class Child:
    """`meet.assist.web` на эфемерном порту в отдельном цикле событий."""

    def __init__(self, tmp_path, token):
        from aiohttp import web

        from meet.assist.web import build_app

        self.state = ChatState(tmp_path, token=token)
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self._web = web
        self._app = build_app(self.state)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        assert self.ready.wait(10)

    def _serve(self):
        asyncio.set_event_loop(self.loop)

        async def start():
            self.runner = self._web.AppRunner(self._app, shutdown_timeout=1.0)
            await self.runner.setup()
            site = self._web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            self.port = self.runner.addresses[0][1]
            self.ready.set()

        self.loop.run_until_complete(start())
        self.loop.run_forever()

    def call(self, coro, timeout=10):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self):
        async def stop():
            self.state.request_stop()
            await self.runner.cleanup()

        try:
            self.call(stop())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)


def _live_for(port, token=None):
    """LiveControl, будто ребёнок уже запущен и слушает на `port`."""
    live = live_control.LiveControl(events.EventBus())
    if token is not None:
        live._control_token = token
    live._process = object()
    live._active = True
    live._port = port
    return live


@pytest.fixture
def child(tmp_path):
    made = Child(tmp_path, token="tok-1")
    try:
        yield made
    finally:
        made.close()


def test_relayed_events_include_chat():
    for name in ("chat_snapshot", "chat", "chat_partial", "agent"):
        assert name in live_control.RELAYED_EVENTS


def test_child_gets_the_control_token_in_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    seen = {}

    def spawn(argv, log_file, extra_env=None):
        seen["env"] = dict(extra_env or {})
        raise OSError("не запускаем")

    live = live_control.LiveControl(events.EventBus(), spawn=spawn)
    live.start(tmp_path / "recordings")
    assert seen["env"][live_control.CONTROL_TOKEN_ENV] == live._control_token
    assert len(live._control_token) >= 24


def test_cli_takes_the_control_token_out_of_env(monkeypatch):
    from meet import cli

    monkeypatch.setenv(live_control.CONTROL_TOKEN_ENV, "abc")
    assert cli._take_control_token() == "abc"
    import os

    assert live_control.CONTROL_TOKEN_ENV not in os.environ


def test_proxy_routes_reach_the_child(child, tmp_path):
    live = _live_for(child.port, token="tok-1")
    reply = live.chat_post({"text": "Что с бюджетом?", "client_id": "c-1"})
    assert reply["id"] == "m1" and reply["attachments"] == []
    assert live.chat_post({"text": "Что с бюджетом?", "client_id": "c-1"})["duplicate"] is True
    feed = live.chat()
    assert [m["text"] for m in feed["messages"]] == ["Что с бюджетом?"]
    pasted = live.chat_paste(_png(), "image/png", "скрин экрана.png")
    assert pasted["status"] == "ready" and pasted["attachment"]["name"] == "скрин экрана.png"
    note = tmp_path / "План.md"
    note.write_text("# План\n\nСрок — пятница.\n", encoding="utf-8")
    attached = live.chat_attach(str(note))
    assert attached["attachment"]["type"] == "doc"
    assert live.chat_remove(attached["id"]) == {"ok": True, "changed": True}
    assert child.state.chat.get(attached["id"])["status"] == "removed"
    agent = child.state.chat.append("agent", status="shown", text="Глянуть?", buttons=["Глянь"]).message
    assert live.chat_click(agent["id"], {"label": "Глянь"})["ok"] is True
    assert live.chat_react(agent["id"], {"emoji": "❓", "on": None}) == {"ok": True, "changed": True}
    assert live.chat_stop({}) == {"ok": False}
    with pytest.raises(live_control.LiveError, match="кнопки"):
        live.chat_click(agent["id"], {"label": "Нет такой"})
    assert live.agent_frequency("less")["label"] == "реже"
    assert child.state.participant.frequency == "реже"
    assert child.state.saved == []     # в настройки сохраняет резидент, не ребёнок


def test_attach_with_a_foreign_token_is_refused(child, tmp_path):
    note = tmp_path / "x.md"
    note.write_text("x", encoding="utf-8")
    live = _live_for(child.port, token="foreign-token")
    with pytest.raises(live_control.LiveError, match="только от приложения"):
        live.chat_attach(str(note))
    assert child.state.chat.messages() == []


def test_proxy_without_live_is_refused():
    live = live_control.LiveControl(events.EventBus())
    for call in (lambda: live.chat(), lambda: live.chat_post({"text": "a"}),
                 lambda: live.agent_frequency("less")):
        with pytest.raises(live_control.LiveNotRunning):
            call()


def _block(stream, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        block = stream.get(timeout=0.1)
        if block:
            return block.decode("utf-8")
    raise AssertionError("событие не пришло")


def test_relay_passes_chat_events(child):
    live = _live_for(child.port, token="tok-1")
    stream = live.open_events()
    try:
        names = []
        while "chat_snapshot" not in names:
            names.append(_block(stream).split("\n", 1)[0].removeprefix("event: "))
        live.chat_post({"text": "Привет", "client_id": "c-9"})
        while True:
            block = _block(stream)
            if block.startswith("event: chat\n"):
                data = json.loads(block.split("data: ", 1)[1])
                assert data["op"] == "add" and data["message"]["text"] == "Привет"
                break
        assert child.call(child.state.participant.tick())   # ход агента
        seen = set()
        while not {"chat_partial", "agent"} <= seen:
            seen.add(_block(stream).split("\n", 1)[0].removeprefix("event: "))
    finally:
        stream.close()


# --- резидент: TrayControl ------------------------------------------------------------


def _write_config(root, data: dict) -> None:
    path = root / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    _write_config(tmp_path, {
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(tmp_path / "recordings"),
                      "voices_dir": str(tmp_path / "voices")},
        "llm": {"provider": "auto"},
        "assistant": {"knowledge_dir": None, "notes_dir": None},
    })
    folder = tmp_path / "recordings" / RID
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "title": "Планёрка", "segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Демьян", "text": "Начнём."}]})
    return tray.TrayApp()


class FakeLive:
    """LiveControl без ребёнка: что передано и что вернуть."""

    def __init__(self, folder=None, running=True):
        self.calls = []
        self.folder = folder
        self.running = running
        self.agent_live = True

    def status(self):
        return {"folder": str(self.folder) if self.folder else None, "active": self.running}

    def busy(self):
        return False

    def __getattr__(self, name):
        if name.startswith("chat") or name == "agent_frequency":
            def call(*args):
                if not self.running:
                    raise live_control.LiveNotRunning("Ассистент не запущен")
                self.calls.append((name, *args))
                return {"ok": True, "live": self.agent_live}
            return call
        raise AttributeError(name)


@pytest.fixture
def state(app, monkeypatch):
    release = threading.Event()

    def spawn(job, on_line):
        release.wait(timeout=5)
        return 0

    queue = jobs.JobQueue(app.bus, spawn=spawn)
    llm_queue = jobs.JobQueue(app.bus, spawn=spawn)
    st = tray_control.TrayControl(app, queue=queue, llm_queue=llm_queue, live=FakeLive())
    import meet.llm as llm
    from meet.llm import detect

    def available(base_url=None, probe_local=True, via_proxy=False):
        return {name: {"found": name == "codex"} for name in llm.PROVIDERS}

    monkeypatch.setattr(detect, "available", available)
    try:
        yield st
    finally:
        release.set()
        queue.stop()
        llm_queue.stop()


def _events(app):
    got = []
    app.bus.subscribe(got.append)
    return got


def test_live_chat_validates_and_forwards(state, tmp_path):
    state.live_chat_post({"text": "  привет ", "attachments": ["a1", "a1"], "client_id": "c"})
    assert state.live.calls[-1] == ("chat_post", {"text": "привет", "attachments": ["a1"],
                                                  "client_id": "c"})
    for body in ({}, {"text": ""}, {"text": "a", "attachments": ["C:/x.docx"]},
                 {"text": "x" * 9000}, {"text": "a", "client_id": 5}):
        with pytest.raises(control.BadRequest):
            state.live_chat_post(body)
    state.live_chat({"limit": "50"})
    assert state.live.calls[-1] == ("chat", 50)
    state.live_chat_click("m3", {"label": "Глянь"})
    assert state.live.calls[-1] == ("chat_click", "m3", {"label": "Глянь"})
    with pytest.raises(control.BadRequest):
        state.live_chat_click("../x", {"label": "Глянь"})
    state.live_chat_react("m3", {"emoji": "👎"})
    assert state.live.calls[-1] == ("chat_react", "m3", {"emoji": "👎", "on": None})
    with pytest.raises(control.BadRequest):
        state.live_chat_react("m3", {"emoji": "🔥"})
    state.live_chat_stop({"id": "m4"})
    assert state.live.calls[-1] == ("chat_stop", {"id": "m4"})
    state.live_chat_remove("a7")
    assert state.live.calls[-1] == ("chat_remove", "a7")
    for bad in ("m7", "../a7", "a", 7):
        with pytest.raises(control.BadRequest):
            state.live_chat_remove(bad)
    state.live_chat_paste(b"png", "image/png; charset=binary", "%D1%84.png")
    assert state.live.calls[-1] == ("chat_paste", b"png", "image/png", "ф.png")
    with pytest.raises(control.BadRequest):
        state.live_chat_paste(b"x", "application/pdf")


def test_live_chat_attach_checks_the_path(state, tmp_path):
    doc = tmp_path / "Отчёт.md"
    doc.write_text("x", encoding="utf-8")
    state.live_chat_attach({"path": str(doc)})
    assert state.live.calls[-1] == ("chat_attach", str(doc))
    for path in ("rel/x.md", "//server/share/x", str(tmp_path / "нет.md"), None):
        with pytest.raises(control.BadRequest):
            state.live_chat_attach({"path": path})


def test_live_chat_without_live_is_409(state):
    state.live.running = False
    with pytest.raises(control.Conflict):
        state.live_chat_post({"text": "a"})


def test_agent_frequency_persists_and_reaches_the_live_agent(state):
    assert state.agent_frequency({"frequency": "реже"}) == {
        "frequency": "less", "label": "реже", "live": True}
    assert settings.load().assist.frequency == "less"
    assert state.live.calls[-1] == ("agent_frequency", "less")
    # M1: ребёнок без агента-участника («Только сводка») — live от него, false.
    state.live.agent_live = False
    assert state.agent_frequency({"frequency": "more"})["live"] is False
    state.live.running = False
    assert state.agent_frequency({"frequency": "normal"})["live"] is False
    assert settings.load().assist.frequency == "normal"
    with pytest.raises(control.BadRequest):
        state.agent_frequency({"frequency": "иногда"})


# --- после встречи: GET/POST /recordings/{id}/chat ---------------------------------------


def test_recording_chat_new_meeting(state, tmp_path):
    folder = tmp_path / "recordings" / RID
    log = ChatLog(folder)
    log.append("user", text="Привет")
    log.append("tool", event="request", call="read", args=["x"])     # не в ленте
    out = state.recording_chat(RID)
    assert [m["text"] for m in out["messages"]] == ["Привет"]
    assert out["legacy"] is None and out["live"] is False and out["job"] is None
    assert out["seq"] == 2


def test_recording_chat_legacy_flag_for_old_meetings(state, tmp_path):
    folder = tmp_path / "recordings" / RID
    assert state.recording_chat(RID)["legacy"] is None        # ни чата, ни прежних данных
    (folder / "live_state.json").write_text(json.dumps({
        "summary": {"topic": "", "points": [], "decisions": [], "tasks": [], "open_questions": []},
        "hints": [{"id": "h1", "kind": "risk", "text": "Нет владельца"}]}, ensure_ascii=False),
        encoding="utf-8")
    (folder / "qa.jsonl").write_text('{"q": "срок?", "a": "пятница", "at": 1.0}\n', encoding="utf-8")
    out = state.recording_chat(RID)
    assert out["messages"] == [] and out["legacy"]["hints"][0]["text"] == "Нет владельца"
    assert out["legacy"]["qa"][0]["q"] == "срок?"
    assert not (folder / "assistant").exists()                 # чтение файлов не создаёт
    assert state.recording_chat("../..") == {"error": "записи нет"}


def test_continue_chat_journals_the_message_and_queues_a_job(state, app, tmp_path):
    got = _events(app)
    out = state.continue_chat(RID, {"text": "  Что решили по срокам? ", "client_id": "k-1"})
    message, job = out["message"], out["job"]
    assert message["id"] == "m1" and message["text"] == "Что решили по срокам?"
    assert message["after_meeting"] is True and "t" not in message
    assert job["kind"] == jobs.CHAT
    assert state.llm_queue.get(job["id"]).options == {"message": "m1"}
    assert any(e.kind == "chat.updated" and e.data["id"] == RID for e in got)
    again = state.continue_chat(RID, {"text": "Что решили по срокам?", "client_id": "k-1"})
    assert again["duplicate"] is True and again["message"]["id"] == "m1"
    assert again["job"]["id"] == job["id"] and len(state.llm_queue.listing()) == 1
    assert state.recording_chat(RID)["job"]["id"] == job["id"]
    with pytest.raises(control.BadRequest):
        state.continue_chat(RID, {"text": "   "})


def test_continue_chat_during_live_of_this_recording_is_409(state, tmp_path):
    state.live.folder = tmp_path / "recordings" / RID
    with pytest.raises(control.Conflict, match="живой режим"):
        state.continue_chat(RID, {"text": "привет"})
    assert state.recording_chat(RID)["live"] is True


def test_continue_chat_needs_the_participant_setting(state, tmp_path):
    cfg = json.loads((tmp_path / "meet" / "config.json").read_text(encoding="utf-8"))
    _write_config(tmp_path, {**cfg, "assist": {"participant": False}})
    with pytest.raises(control.Conflict, match="выключен в настройках"):
        state.continue_chat(RID, {"text": "привет"})
    assert state.llm_queue.listing() == []


def test_continue_chat_refuses_when_live_status_is_unknown(state):
    def broken():
        raise RuntimeError("ребёнок не отвечает")

    state.live.status = broken
    with pytest.raises(control.Unavailable, match="живой режим"):
        state.continue_chat(RID, {"text": "привет"})
    assert state.recording_chat(RID)["live"] is False     # чтение — как раньше


def test_chat_job_end_emits_chat_updated(state, app):
    got = _events(app)
    job = jobs.Job(id="j1", kind=jobs.CHAT, folder=str(state._root() / RID))
    job.state = jobs.DONE
    app.bus.emit(jobs.JOB_DONE, job=job.to_raw())
    assert [e.data["id"] for e in got if e.kind == "chat.updated"] == [RID]


def test_agent_context_writes_assistant_chat_md_only_with_a_chat(state, tmp_path):
    folder = tmp_path / "recordings" / RID
    out = state.agent_context(RID)
    assert "assistant_chat.md" not in out["files"]
    assert not (folder / "assistant_chat.md").exists()
    log = ChatLog(folder)
    log.append("user", text="Что по срокам?")
    log.append("agent", status="shown", text="Пятница")
    out = state.agent_context(RID)
    assert "assistant_chat.md" in out["files"]
    text = (folder / "assistant_chat.md").read_text(encoding="utf-8")
    assert "Что по срокам?" in text and "Пятница" in text
    assert "assistant_chat.md" in state.agent_files(RID)["files"]


# --- control API: маршруты --------------------------------------------------------------


class RouteState:
    def __init__(self):
        self.bus = events.EventBus()
        self.calls = []

    def __getattr__(self, name):
        if name.startswith(("live_chat", "recording_chat", "continue_chat", "agent_frequency")):
            def call(*args):
                self.calls.append((name, *args))
                return {"ok": True}
            return call
        raise AttributeError(name)


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    st = RouteState()
    srv = control.ControlServer(st)
    srv.start(pid=4343)
    srv.state_obj = st
    try:
        yield srv
    finally:
        srv.stop(pid=4343)


def _call(srv, path, payload=None, method="POST", raw=None, headers=None):
    body = raw if raw is not None else (json.dumps(payload).encode("utf-8") if payload is not None
                                        else None)
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", data=body, method=method,
                                 headers=headers or {})
    req.add_header("Authorization", f"Bearer {srv.token}")
    if raw is None and body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def test_control_routes_for_chat(server):
    calls = server.state_obj.calls
    _call(server, "/live/chat?limit=20", method="GET")
    assert calls[-1] == ("live_chat", {"limit": "20"})
    _call(server, "/live/chat", {"text": "a"})
    assert calls[-1] == ("live_chat_post", {"text": "a"})
    _call(server, "/live/chat/paste", raw=b"\x89PNG", headers={
        "Content-Type": "image/png", "X-File-Name": "%D1%84.png"})
    assert calls[-1] == ("live_chat_paste", b"\x89PNG", "image/png", "%D1%84.png")
    _call(server, "/live/chat/attach", {"path": "C:/x.md"})
    assert calls[-1] == ("live_chat_attach", {"path": "C:/x.md"})
    _call(server, "/live/chat/stop", {"id": "m2"})
    assert calls[-1] == ("live_chat_stop", {"id": "m2"})
    _call(server, "/live/chat/attachments/a3/remove", {})
    assert calls[-1] == ("live_chat_remove", "a3")
    _call(server, "/live/chat/m3/click", {"label": "Глянь"})
    assert calls[-1] == ("live_chat_click", "m3", {"label": "Глянь"})
    _call(server, "/live/chat/m3/react", {"emoji": "👍"})
    assert calls[-1] == ("live_chat_react", "m3", {"emoji": "👍"})
    _call(server, "/agent/frequency", {"frequency": "less"}, method="PUT")
    assert calls[-1] == ("agent_frequency", {"frequency": "less"})
    _call(server, f"/recordings/{RID}/chat", method="GET")
    assert calls[-1] == ("recording_chat", RID)
    _call(server, f"/recordings/{RID}/chat", {"text": "дальше"})
    assert calls[-1] == ("continue_chat", RID, {"text": "дальше"})


def test_paste_over_10_mb_is_refused_by_the_resident(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _call(server, "/live/chat/paste", raw=b"0" * (10 * 1024 * 1024 + 1),
              headers={"Content-Type": "image/png"})
    assert e.value.code == 400
    assert not server.state_obj.calls


# --- «Только сводка» ---------------------------------------------------------------------


def test_summary_only_switches_the_participant_off():
    a = settings.Settings.from_raw({"assist": {"participant": True, "activity": "summary"}}).assist
    assert a.participant is True and a.participant_on is False
    b = settings.Settings.from_raw({"assist": {"participant": True, "activity": "calm"}}).assist
    assert b.participant_on is True
    assert settings.Settings.from_raw({}).assist.participant_on is True
    assert settings.Settings.from_raw({"assist": {"participant": False}}).assist.participant_on is False


def test_summary_only_wires_no_participant(tmp_path, monkeypatch):
    from test_assist_participant import _wired

    cfg = settings.Settings.from_raw({"assist": {"participant": True, "activity": "summary"}})
    heavy, st = _wired(tmp_path, monkeypatch, cfg)
    assert st.participant is None and st.chat_feed is None
    assert heavy.qa_kwargs is not None and "agent" not in st.view()
    cfg = settings.Settings.from_raw({"assist": {"participant": True}})
    heavy, st = _wired(tmp_path / "on", monkeypatch, cfg)
    assert st.participant is not None and st.chat_feed is not None



# --- раунд исправлений 1 ---------------------------------------------------------------


def test_duplicate_without_job_and_answer_queues_again(state, tmp_path):
    """I1: задача потерялась (перезапуск резидента, отмена) — повтор с тем же
    client_id спрашивает агента снова; отвеченное — нет."""
    first = state.continue_chat(RID, {"text": "Сроки?", "client_id": "k-1"})
    state.llm_queue.cancel(first["job"]["id"])
    again = state.continue_chat(RID, {"text": "Сроки?", "client_id": "k-1"})
    assert again["duplicate"] is True and again["job"]["id"] != first["job"]["id"]
    assert state.llm_queue.get(again["job"]["id"]).options == {"message": "m1"}
    state.llm_queue.cancel(again["job"]["id"])
    folder = tmp_path / "recordings" / RID
    ChatLog(folder).append("agent", status="shown", text="Пятница", re="m1")
    third = state.continue_chat(RID, {"text": "Сроки?", "client_id": "k-1"})
    assert third["duplicate"] is True and third["job"] is None


def test_killed_chat_job_reply_is_closed(state, app, tmp_path):
    """M6: «пишет» от убитой задачи закрывается — по её концу и при чтении."""
    folder = tmp_path / "recordings" / RID
    log = ChatLog(folder)
    log.append("user", text="Сроки?")
    stuck = log.begin_reply(mode="reply", re="m1").message
    state._background = lambda fn, name="": fn()
    job = jobs.Job(id="j9", kind=jobs.CHAT, folder=str(folder))
    job.state = jobs.FAILED
    app.bus.emit(jobs.JOB_FAILED, job=job.to_raw())
    assert log.get(stuck["id"])["status"] == "cancelled"
    second = log.begin_reply(mode="reply", re="m1").message   # как после выхода резидента
    out = state.recording_chat(RID)
    assert [m["status"] for m in out["messages"] if m["id"] == second["id"]] == ["cancelled"]
