"""Резидент управляет живым режимом: дочерний `meet assist` под LiveControl.

Настоящий `meet assist` здесь не запускается никогда (модели, аудио, вызовы
модели). Вместо него — процесс-заглушка: тот же контракт (файл эндпоинта с
pid, `/stop`, `/events`, `/ask`, `/task`), поднятый крошечным `http.server`.
Процесс настоящий: так проверяются Popen, журнал `live.log` и убийство дерева.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from meet import control, events, jobs, library, live_control, tray, tray_control

STUB = r'''
import json, os, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

mode, notes = sys.argv[1], sys.argv[2]
args = sys.argv[3:]
endpoint = args[args.index("--endpoint-file") + 1]
out = args[args.index("--out") + 1]


def note(name, text=""):
    with open(os.path.join(notes, name), "a", encoding="utf-8") as f:
        f.write(text + "\n")


note("argv", json.dumps(args, ensure_ascii=False))
if mode == "no-provider":
    print("Подключите Claude Code или Codex в настройках", file=sys.stderr, flush=True)
    sys.exit(1)
if mode == "slow":
    time.sleep(60)
    sys.exit(0)
folder = os.path.join(out, "2026-10-01_10-00")
os.makedirs(folder, exist_ok=True)
with open(os.path.join(folder, "sys.opus"), "wb") as f:
    f.write(b"x")
stop = threading.Event()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        last = self.headers.get("Last-Event-ID")
        note("last_event_id", last or "-")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        start = int(last) + 1 if last else 0
        self.wfile.write(b'event: state\ndata: {"status": null, "digest": "", "transcript": []}\n\n')
        for i in range(start, 3):
            data = json.dumps({"t": "00:0%d" % i, "speaker": "Демьян", "text": "реплика %d" % i},
                              ensure_ascii=False)
            self.wfile.write(("event: line\nid: %d\ndata: %s\n\n" % (i, data)).encode("utf-8"))
        self.wfile.flush()
        try:
            while not stop.is_set():
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                time.sleep(0.05)
        except OSError:
            note("events_closed")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.headers.get("Origin"):
            note("origin", self.headers["Origin"])
        if self.path == "/stop":
            note("stop")
            self._json(200, {"ok": True})
            stop.set()
        elif self.path == "/ask":
            note("ask", body.get("question", ""))
            self._json(200, {"answer": "ответ: " + body.get("question", "")})
        elif self.path == "/task":
            note("task", body.get("task", ""))
            self.send_response(204)
            self.end_headers()
        elif self.path == "/crash":
            self._json(200, {"ok": True})
            print("RuntimeError: устройство пропало", flush=True)
            os._exit(3)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    note("grandchild", str(child.pid))
tmp = endpoint + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"port": srv.server_address[1], "pid": os.getpid(), "folder": folder}, f)
os.replace(tmp, endpoint)
print("Ассистент: http://127.0.0.1:%d/" % srv.server_address[1], flush=True)
stop.wait()
if mode == "hang":
    time.sleep(60)  # застрявший вызов модели держит процесс после финализации
time.sleep(0.1)
os.remove(endpoint)
print("Остановлено: " + folder, flush=True)
'''


def _wait_for(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("не дождались")


class Stub:
    """Фабрика spawn: подменяет `-m meet.cli assist` скриптом-заглушкой,
    остальные аргументы (эндпоинт, порт, папка) передаёт как есть."""

    def __init__(self, tmp_path, mode: str = "ok") -> None:
        self.script = tmp_path / "stub_assist.py"
        self.script.write_text(STUB, encoding="utf-8")
        self.notes = tmp_path / "notes"
        self.notes.mkdir(exist_ok=True)
        self.mode = mode
        self.argv: list | None = None
        self.processes: list = []

    def __call__(self, argv, log_file):
        self.argv = list(argv)
        tail = argv[argv.index("assist") + 1:]
        process = live_control._spawn_process(
            [sys.executable, str(self.script), self.mode, str(self.notes), *tail], log_file)
        self.processes.append(process)
        return process

    def note(self, name: str) -> list[str]:
        path = self.notes / name
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    def cleanup(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                jobs._kill_tree(process)
            process.wait(timeout=10)


class Recorder:
    def __init__(self, bus) -> None:
        self.events: list = []
        bus.subscribe(self.events.append)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events if e.kind.startswith("live.")]

    def last(self, kind: str):
        return [e for e in self.events if e.kind == kind][-1]


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def make_live(data_dir, tmp_path):
    made: list = []

    def make(mode: str = "ok", **kwargs):
        stub = Stub(tmp_path, mode)
        bus = events.EventBus()
        live = live_control.LiveControl(bus, spawn=stub, **kwargs)
        made.append((live, stub))
        return live, stub, Recorder(bus)

    try:
        yield make
    finally:
        for live, stub in made:
            live.stop(wait=True)
            stub.cleanup()


def _active(live) -> bool:
    return live.status()["active"]


def _idle(live) -> bool:
    status = live.status()
    return not status["active"] and not status["starting"]


# --- LiveControl -------------------------------------------------------------


def test_start_spawns_assist_and_becomes_active(make_live, data_dir, tmp_path):
    live, stub, rec = make_live()
    out_root = tmp_path / "recordings"
    reply = live.start(out_root)
    # Не ждёт загрузки модели: ответ сразу, статус дальше — событиями.
    assert reply["ok"] is True and reply["starting"] is True and reply["active"] is False
    _wait_for(lambda: _active(live))
    status = live.status()
    assert status["starting"] is False and status["error"] is None
    assert status["folder"] == str(out_root / "2026-10-01_10-00")
    assert stub.argv[:4] == [sys.executable, "-m", "meet.cli", "assist"]
    for flag in ("--no-browser",):
        assert flag in stub.argv
    assert stub.argv[stub.argv.index("--port") + 1] == "0"
    assert stub.argv[stub.argv.index("--endpoint-file") + 1] == str(data_dir / "live.json")
    assert stub.argv[stub.argv.index("--out") + 1] == str(out_root)
    assert rec.kinds()[:2] == [live_control.LIVE_STARTING, live_control.LIVE_STARTED]
    assert rec.last(live_control.LIVE_STARTED).data["folder"] == status["folder"]


def test_second_start_is_refused(make_live, tmp_path):
    live, _, _ = make_live()
    live.start(tmp_path / "recordings")
    with pytest.raises(live_control.LiveBusy):
        live.start(tmp_path / "recordings")
    _wait_for(lambda: _active(live))
    with pytest.raises(live_control.LiveBusy):
        live.start(tmp_path / "recordings")


def test_stop_asks_child_and_reports_stopped(make_live, data_dir, tmp_path):
    live, stub, rec = make_live()
    live.start(tmp_path / "recordings")
    _wait_for(lambda: _active(live))
    folder = live.status()["folder"]
    reply = live.stop()
    assert reply["ok"] is True
    _wait_for(lambda: _idle(live))
    assert stub.note("stop") == [""]
    assert stub.note("origin") == []  # без Origin: дочерний отказал бы в POST
    assert rec.kinds()[-1] == live_control.LIVE_STOPPED
    assert rec.last(live_control.LIVE_STOPPED).data["folder"] == folder
    assert live.status() == {"active": False, "starting": False, "stopping": False,
                             "folder": None, "error": None, "started_at": None}
    assert not (data_dir / "live.json").exists()


def test_stop_when_idle_is_honest(make_live):
    live, _, rec = make_live()
    reply = live.stop()
    assert reply["ok"] is False and reply["action"] == "not-live"
    assert rec.kinds() == []


def test_child_failing_before_endpoint_reports_its_text_promptly(make_live, data_dir,
                                                                 tmp_path):
    live, _, rec = make_live("no-provider")
    began = time.monotonic()
    live.start(tmp_path / "recordings")
    _wait_for(lambda: live_control.LIVE_FAILED in rec.kinds())
    assert time.monotonic() - began < 10  # не ждём 60 с таймаута старта
    error = rec.last(live_control.LIVE_FAILED).data["error"]
    assert error == "Подключите Claude Code или Codex в настройках"
    status = live.status()
    assert status["active"] is False and status["starting"] is False
    assert status["error"] == error
    log = (data_dir / "logs" / "live.log").read_text(encoding="utf-8")
    assert "Подключите Claude Code" in log


def test_unexpected_exit_reports_failure_with_log_line(make_live, tmp_path):
    live, stub, rec = make_live()
    live.start(tmp_path / "recordings")
    _wait_for(lambda: _active(live))
    port = live._port
    urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{port}/crash", data=b"{}", method="POST"), timeout=5).close()
    _wait_for(lambda: live_control.LIVE_FAILED in rec.kinds())
    failed = rec.last(live_control.LIVE_FAILED).data
    assert failed["error"] == "RuntimeError: устройство пропало"
    assert failed["folder"].endswith("2026-10-01_10-00")
    assert live_control.LIVE_STOPPED not in rec.kinds()
    assert live.status()["error"] == "RuntimeError: устройство пропало"


def test_error_is_cleared_by_next_start(make_live, tmp_path):
    live, stub, rec = make_live("no-provider")
    live.start(tmp_path / "recordings")
    _wait_for(lambda: live.status()["error"])
    stub.mode = "ok"
    live.start(tmp_path / "recordings")
    assert live.status()["error"] is None
    _wait_for(lambda: _active(live))


def test_start_timeout_kills_child(make_live, tmp_path):
    live, stub, rec = make_live("slow", start_timeout=0.5)
    live.start(tmp_path / "recordings")
    _wait_for(lambda: live_control.LIVE_FAILED in rec.kinds())
    assert "не запустился" in rec.last(live_control.LIVE_FAILED).data["error"]
    assert stub.processes[0].poll() is not None


def test_stop_during_start_does_not_wait_for_the_model(make_live, tmp_path):
    live, stub, rec = make_live("slow")
    live.start(tmp_path / "recordings")
    began = time.monotonic()
    live.stop(wait=True)
    assert time.monotonic() - began < 10
    assert _idle(live)
    assert rec.kinds()[-1] == live_control.LIVE_STOPPED
    assert rec.last(live_control.LIVE_STOPPED).data["folder"] is None


def test_stuck_child_is_killed_with_its_tree_after_stop_timeout(make_live, tmp_path):
    psutil = pytest.importorskip("psutil")
    live, stub, rec = make_live("hang", stop_timeout=0.5)
    live.start(tmp_path / "recordings")
    _wait_for(lambda: _active(live))
    _wait_for(lambda: stub.note("grandchild"))
    grandchild = int(stub.note("grandchild")[0])
    live.stop(wait=True)
    assert _idle(live)
    assert stub.note("stop") == [""]
    # Убит после финализации — ничего не потеряно, это штатная остановка.
    assert rec.kinds()[-1] == live_control.LIVE_STOPPED
    _wait_for(lambda: not psutil.pid_exists(grandchild)
              or psutil.Process(grandchild).status() == psutil.STATUS_ZOMBIE)


def test_stale_endpoint_file_is_removed_before_spawn(make_live, data_dir, tmp_path):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "live.json").write_text(
        json.dumps({"port": 1, "pid": 999999, "folder": "C:/старое"}), encoding="utf-8")
    live, stub, _ = make_live("slow")
    live.start(tmp_path / "recordings")
    assert not (data_dir / "live.json").exists()
    assert live.status()["folder"] is None


def test_endpoint_of_another_process_is_ignored(tmp_path):
    path = tmp_path / "live.json"
    path.write_text(json.dumps({"port": 5, "pid": 111, "folder": "F"}), encoding="utf-8")
    assert live_control._read_endpoint(path, 222) is None
    assert live_control._read_endpoint(path, 111) == {"port": 5, "pid": 111, "folder": "F"}
    path.write_text("{битый", encoding="utf-8")
    assert live_control._read_endpoint(path, 111) is None
    assert live_control._read_endpoint(tmp_path / "нет.json", 111) is None


def test_ask_and_task_are_proxied(make_live, tmp_path):
    live, stub, _ = make_live()
    live.start(tmp_path / "recordings")
    _wait_for(lambda: _active(live))
    assert live.ask("что решили?") == {"answer": "ответ: что решили?"}
    assert live.task("Ревью архитектуры") == {"ok": True}
    assert stub.note("ask") == ["что решили?"]
    assert stub.note("task") == ["Ревью архитектуры"]


def test_ask_without_live_is_refused(make_live):
    live, _, _ = make_live()
    with pytest.raises(live_control.LiveNotRunning):
        live.ask("что решили?")
    with pytest.raises(live_control.LiveNotRunning):
        live.open_events()


def _read_block(stream, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        block = stream.get(timeout=0.1)
        if block is None:
            continue
        return block.decode("utf-8")
    raise AssertionError("событие не пришло")


def test_events_relay_passes_last_event_id_and_filters(make_live, tmp_path):
    live, stub, _ = make_live()
    live.start(tmp_path / "recordings")
    _wait_for(lambda: _active(live))
    stream = live.open_events("0")
    try:
        assert stub.note("last_event_id") == ["0"]
        assert _read_block(stream).startswith("event: state\n")
        first = _read_block(stream)
        assert first.startswith("event: line\nid: 1\n") and "реплика 1" in first
        assert "id: 2\n" in _read_block(stream)
        # Комментарии-пинги заглушки клиенту не нужны.
        assert stream.get(timeout=0.2) is None
        live.stop()
        _wait_for(lambda: stream.get(timeout=0.05) == b"")  # конец потока
    finally:
        stream.close()


def test_closing_relay_closes_upstream_and_thread(make_live, tmp_path):
    live, stub, _ = make_live()
    live.start(tmp_path / "recordings")
    _wait_for(lambda: _active(live))
    before = {t.name for t in threading.enumerate()}
    stream = live.open_events()
    _read_block(stream)
    stream.close()
    _wait_for(lambda: stub.note("events_closed"))
    _wait_for(lambda: not any(t.name == live_control.RELAY_THREAD
                              for t in threading.enumerate()))
    assert live_control.RELAY_THREAD not in before


def test_live_stopping_closes_open_relays(make_live, tmp_path):
    live, stub, _ = make_live("hang", stop_timeout=0.5)
    live.start(tmp_path / "recordings")
    _wait_for(lambda: _active(live))
    stream = live.open_events()
    try:
        live.stop(wait=True)
        _wait_for(lambda: stream.get(timeout=0.05) == b"")
    finally:
        stream.close()
    assert not any(t.name == live_control.RELAY_THREAD for t in threading.enumerate())


def test_live_control_stays_stdlib_only():
    """Резидент не тянет aiohttp и SDK провайдеров: минимальная установка для
    записи их не содержит, а живой режим — отдельный процесс."""
    code = ("import sys, meet.tray, meet.tray_control, meet.live_control; "
            "print(any(m.split('.')[0] in ('aiohttp', 'claude_agent_sdk') "
            "for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == "False"


# --- резидент: TrayControl поверх LiveControl --------------------------------


def _write_config(root, data: dict) -> None:
    path = root / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class _Queue:
    def __init__(self):
        self.submitted = []

    def submit(self, kind, folder, options=None):
        self.submitted.append((kind, folder))
        return jobs.Job(id="j1", kind=kind, folder=folder)

    def active_for(self, folder, kinds):
        return None

    def listing(self):
        return []

    def stop(self):
        pass


@pytest.fixture
def resident(monkeypatch, tmp_path):
    from meet.llm import detect

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    _write_config(tmp_path, {
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(tmp_path / "recordings")},
        "llm": {"provider": "auto"},
    })
    monkeypatch.setattr(detect, "available",
                        lambda base_url=None: {"codex": {"found": True}})
    app = tray.TrayApp()
    stub = Stub(tmp_path)
    queue = _Queue()
    state = tray_control.TrayControl(
        app, queue=queue, llm_queue=_Queue(),
        live=live_control.LiveControl(app.bus, spawn=stub, stop_timeout=5))
    state.stub = stub
    try:
        yield state
    finally:
        state.live.stop(wait=True)
        stub.cleanup()


def test_snapshot_carries_live_status(resident, tmp_path):
    assert resident.snapshot()["live"]["active"] is False
    reply = resident.live_start()
    assert reply["starting"] is True
    _wait_for(lambda: resident.snapshot()["live"]["active"])
    live = resident.snapshot()["live"]
    assert live["folder"] == str(tmp_path / "recordings" / "2026-10-01_10-00")
    assert resident.stub.argv[resident.stub.argv.index("--out") + 1] == \
        str(tmp_path / "recordings")


def test_start_recording_while_live_is_refused(resident, monkeypatch):
    resident.live_start()
    _wait_for(lambda: resident.live.status()["active"])
    reply = resident.start_recording()
    assert reply["ok"] is False and reply["action"] == "already-recording"
    assert resident.tray.recording is False
    # И меню/автозапись трея не поднимут вторую запись поверх живой.
    assert resident.tray.start_recording(tray_control.AUTO) is False


def test_live_start_twice_is_bad_request(resident):
    resident.live_start()
    with pytest.raises(control.BadRequest):
        resident.live_start()


def test_live_start_during_normal_recording_is_bad_request(resident):
    resident.tray.recording = True
    with pytest.raises(control.BadRequest):
        resident.live_start()
    assert resident.stub.argv is None


def test_live_start_without_provider_is_conflict(resident, monkeypatch):
    from meet.llm import detect

    monkeypatch.setattr(detect, "available", lambda base_url=None: {})
    with pytest.raises(control.Conflict) as e:
        resident.live_start()
    assert "Подключите Claude Code или Codex" in str(e.value)
    assert resident.stub.argv is None


def test_live_stop_queues_transcription_and_marks_source(resident):
    resident.live_start()
    _wait_for(lambda: resident.live.status()["active"])
    folder = resident.live.status()["folder"]
    resident.live_stop()
    _wait_for(lambda: resident.queue.submitted)
    assert resident.queue.submitted == [(jobs.TRANSCRIBE, folder)]
    from pathlib import Path
    assert library.read_meta(Path(folder))["source"] == "live"
    assert library.describe(Path(folder)).source == "live"


def test_live_stop_when_idle(resident):
    reply = resident.live_stop()
    assert reply["ok"] is False and reply["action"] == "not-live"


def test_failed_live_is_not_transcribed(resident):
    resident.live_start()
    _wait_for(lambda: resident.live.status()["active"])
    urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{resident.live._port}/crash", data=b"{}", method="POST"),
        timeout=5).close()
    _wait_for(lambda: resident.live.status()["error"])
    assert resident.queue.submitted == []
    assert resident.snapshot()["live"]["error"] == "RuntimeError: устройство пропало"


def test_shutdown_stops_live_mode(resident, monkeypatch):
    exits = []
    monkeypatch.setattr(resident.tray, "request_exit", lambda: exits.append(True))
    resident.live_start()
    _wait_for(lambda: resident.live.status()["active"])
    assert resident.shutdown() == {"ok": True}
    assert resident.stub.note("stop") == [""]
    assert exits == [True]


def test_live_ask_validates_question(resident):
    with pytest.raises(control.BadRequest):
        resident.live_ask({"question": "  "})
    with pytest.raises(control.Conflict):
        resident.live_ask({"question": "что решили?"})


def test_delete_refuses_live_folder(resident):
    resident.live_start()
    _wait_for(lambda: resident.live.status()["active"])
    with pytest.raises(control.BadRequest):
        resident.delete_recording("2026-10-01_10-00")


class _FakeLive:
    def __init__(self, calls):
        self.calls = calls

    def stop(self, wait=False):
        self.calls.append(("live.stop", wait))
        return {"ok": True}


class _FakeQueue:
    def __init__(self, calls, name):
        self.calls, self.name = calls, name

    def stop(self):
        self.calls.append(self.name)


class _FakeApi:
    def __init__(self, calls):
        self.calls = calls
        self.state = type("S", (), {})()
        self.state.live = _FakeLive(calls)
        self.state.queue = _FakeQueue(calls, "queue.stop")
        self.state.llm_queue = _FakeQueue(calls, "llm_queue.stop")

    def stop(self, pid=None):
        self.calls.append("api.stop")


def test_resident_exit_stops_live_before_api(monkeypatch, tmp_path):
    """Выход резидента (смерть оболочки, /shutdown, request_exit) гасит живой
    режим штатно — и до остановки API, пока очередь ещё принимает расшифровку."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    app = tray.TrayApp(start_now=False)
    calls: list = []
    monkeypatch.setattr(app, "_start_control_api", lambda: _FakeApi(calls))
    monkeypatch.setattr(app, "_tick", lambda: None)
    monkeypatch.setattr(tray, "TICK_S", 0.01)
    monkeypatch.setattr(tray, "_pid_alive", lambda pid: False)
    app.run_headless(parent_pid=999999)  # оболочка «умерла»
    assert calls == [("live.stop", True), "queue.stop", "llm_queue.stop", "api.stop"]


# --- сквозь control API ------------------------------------------------------


def _call(srv, path, payload=None, method="POST", headers=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}",
                                 data=body if method != "GET" else None,
                                 method=method, headers=headers or {})
    req.add_header("Authorization", f"Bearer {srv.token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        finally:
            e.close()


def test_live_routes_end_to_end(resident, monkeypatch):
    srv = control.ControlServer(resident)
    srv.start(publish=False)
    try:
        status, reply = _call(srv, "/live/start")
        assert status == 200 and reply["starting"] is True
        assert _call(srv, "/live/start")[0] == 400
        _wait_for(lambda: _call(srv, "/state", method="GET")[1]["live"]["active"])
        status, reply = _call(srv, "/live/ask", {"question": "что решили?"})
        assert status == 200 and reply == {"answer": "ответ: что решили?"}
        assert _call(srv, "/live/task", {"task": "Ревью"}) == (200, {"ok": True})
        url = f"http://127.0.0.1:{srv.port}/live/events?token={srv.token}"
        req = urllib.request.Request(url, headers={"Last-Event-ID": "1",
                                                   "Origin": "tauri://localhost"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert "text/event-stream" in r.headers["Content-Type"]
            seen = []
            while len(seen) < 2:
                line = r.readline().decode("utf-8")
                assert line, "поток закрылся раньше событий"
                if line.startswith("event: "):
                    seen.append(line.strip())
                if line.startswith("id: "):
                    seen.append(line.strip())
            assert seen == ["event: state", "event: line"]
            assert r.readline().decode("utf-8").strip() == "id: 2"
        assert resident.stub.note("last_event_id") == ["1"]
        status, reply = _call(srv, "/live/stop")
        assert status == 200 and reply["ok"] is True
        _wait_for(lambda: not _call(srv, "/state", method="GET")[1]["live"]["active"])
        assert _call(srv, "/live/ask", {"question": "ещё?"})[0] == 409
        assert _call(srv, "/live/events", method="GET")[0] == 409
    finally:
        srv.stop()
