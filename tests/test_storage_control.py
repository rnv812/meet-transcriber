"""Резидент и перенос движка и моделей: `GET /storage` (где всё лежит и можно
ли переносить сейчас), ответ на вопрос об остатках в общем кэше, отказ качать
модели посреди переноса."""

import json
import threading
import time

import pytest

from meet import control, jobs, paths, storage, tray, tray_control


def _write_config(root, data: dict) -> None:
    path = root / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf-shared"))
    _write_config(tmp_path, {})
    return tray.TrayApp()


@pytest.fixture(autouse=True)
def _no_hold():
    """Удержание — одно на процесс: тест его за собой снимает."""
    storage.HOLD.release()
    yield
    storage.HOLD.release()


def test_storage_is_free_to_move_when_idle(app, tmp_path):
    state = tray_control.TrayControl(app)
    got = state.storage()
    assert got["busy"] is None
    assert got["custom"] is False
    assert got["home"] == str(paths.data_dir())
    assert got["hf_cache"] == str(tmp_path / "hf-shared")


def test_storage_is_busy_while_recording(app):
    state = tray_control.TrayControl(app)
    app.recording = True
    assert "запись" in state.storage()["busy"]


def test_storage_is_busy_while_the_assistant_runs(app, monkeypatch):
    state = tray_control.TrayControl(app)
    monkeypatch.setattr(state.live, "busy", lambda: True)
    assert "ассистент" in state.storage()["busy"]


def _blocking(app, release):
    def spawn(job, on_line):
        release.wait(timeout=5)
        return 0

    return spawn


def _wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "не дождались"
        time.sleep(0.02)


def test_storage_is_busy_while_a_model_downloads(app):
    release = threading.Event()
    state = tray_control.TrayControl(app, downloads=jobs.KeyedQueues(app.bus, spawn=_blocking(app, release)))
    try:
        state.download_model({"id": "gigaam/v3_e2e_rnnt"})
        assert "модел" in state.storage()["busy"]
    finally:
        release.set()
        state.downloads.stop()


def test_storage_is_busy_while_a_job_runs(app, tmp_path):
    release = threading.Event()
    queue = jobs.JobQueue(app.bus, spawn=_blocking(app, release))
    state = tray_control.TrayControl(app, queue=queue)
    try:
        queue.submit(jobs.TRANSCRIBE, str(tmp_path / "rec"), {})
        assert "расшифровка" in state.storage()["busy"]
        release.set()
        _wait_for(lambda: state.storage()["busy"] is None)
    finally:
        release.set()
        queue.stop()


def test_models_do_not_download_during_a_move(app):
    (paths.data_dir() / storage.JOURNAL_FILE).write_text("{}", encoding="utf-8")
    state = tray_control.TrayControl(app)
    with pytest.raises(control.Conflict, match="перенос"):
        state.download_model({"id": "Systran/faster-whisper-small"})
    assert state.downloads.listing() == []


def test_leftovers_answer_reaches_storage(app, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "answer_leftovers", lambda delete: calls.append(delete) or {"ok": True})
    state = tray_control.TrayControl(app)
    assert state.storage_leftovers({"delete": True}) == {"ok": True}
    assert state.storage_leftovers({"delete": False}) == {"ok": True}
    assert calls == [True, False]
    with pytest.raises(control.BadRequest):
        state.storage_leftovers({"delete": "да"})



def test_storage_routes_reach_the_state(monkeypatch, tmp_path):
    """Живой сервер control API: оболочка спрашивает `GET /storage`, окно
    отвечает на вопрос об остатках `POST /storage/leftovers`."""
    import urllib.request

    from meet import events

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))

    class State:
        bus = events.EventBus()
        answers: list = []

        def storage(self):
            return {"busy": None, "models_bytes": 5}

        def storage_leftovers(self, body):
            self.answers.append(body)
            return {"ok": True, "removed": []}

    state = State()
    srv = control.ControlServer(state)
    srv.start(pid=4243)
    try:
        def call(path, payload=None):
            data = json.dumps(payload).encode("utf-8") if payload is not None else None
            req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", data=data,
                                         method="POST" if data else "GET")
            req.add_header("Authorization", f"Bearer {srv.token}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))

        assert call("/storage") == {"busy": None, "models_bytes": 5}
        assert call("/storage/leftovers", {"delete": True}) == {"ok": True, "removed": []}
        assert state.answers == [{"delete": True}]
    finally:
        srv.stop(pid=4243)


def test_hold_refuses_while_busy_and_holds_when_idle(app):
    state = tray_control.TrayControl(app)
    app.recording = True
    assert state.storage_hold() == {"held": False, "busy": "идёт запись"}
    assert not storage.HOLD.held()
    app.recording = False
    assert state.storage_hold() == {"held": True, "busy": None}
    assert storage.HOLD.held()
    assert state.storage_release() == {"ok": True}
    assert not storage.HOLD.held()


def test_while_held_nothing_new_starts(app, tmp_path):
    """Удержан: ручная запись, автозапись, задачи и загрузки — отказ (409),
    а не начало, которое перезапуск резидента оборвал бы."""
    release = threading.Event()
    queue = jobs.JobQueue(app.bus, spawn=_blocking(app, release))
    downloads = jobs.KeyedQueues(app.bus, spawn=_blocking(app, release))
    state = tray_control.TrayControl(app, queue=queue, downloads=downloads)
    try:
        assert state.storage_hold()["held"] is True
        with pytest.raises(control.Conflict, match="перенос"):
            state.start_recording()
        assert app.start_recording(tray.AUTO) is False  # автозапись
        assert app.recording is False
        with pytest.raises(control.Conflict, match="перенос"):
            state.download_model({"id": "gigaam/v3_e2e_rnnt"})
        with pytest.raises(control.Conflict, match="перенос"):
            queue.submit(jobs.TRANSCRIBE, str(tmp_path / "rec"), {})
        with pytest.raises(control.Conflict, match="перенос"):
            state.llm_queue.submit(jobs.SUMMARY, str(tmp_path / "rec"), {})
        assert queue.listing() == [] and downloads.listing() == []
        state.storage_release()
        state.download_model({"id": "gigaam/v3_e2e_rnnt"})
        assert downloads.listing()
    finally:
        release.set()
        queue.stop()
        downloads.stop()
        state.llm_queue.stop()


def test_assistant_does_not_start_while_held(app, monkeypatch):
    state = tray_control.TrayControl(app)
    monkeypatch.setattr(tray_control, "_provider_installed", lambda cfg: True)
    started = []
    monkeypatch.setattr(state.live, "start", lambda root, *a: started.append(root) or {"ok": True})
    state.storage_hold()
    with pytest.raises(control.Conflict, match="перенос"):
        state.live_start()
    assert started == []


def test_storage_reports_the_engine_prefix(app):
    import sys

    assert tray_control.TrayControl(app).storage()["prefix"] == sys.prefix
