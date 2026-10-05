"""Резидент: запись образца голоса владельца (meet.owner_voice_control) и
маршруты `/owner-voice*`.

Микрофон не открывается: запись подменена (пишет пустой файл), задача —
подставной подпроцесс, который печатает строки JSON как job_worker. Попытка
идёт настоящим фоновым потоком: он ждёт конца задачи и убирает папку записи."""

import json
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest

from meet import control, events, jobs, owner_voice, owner_voice_control


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _spawn(lines, code=0, seen=None):
    def spawn(job, on_line):
        if seen is not None:
            seen.append(job)
        for line in lines(job):
            on_line(json.dumps(line, ensure_ascii=False))
        return code
    return spawn


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(owner_voice_control, "JOB_POLL_S", 0.01)

    def make(*, spawn=None, record=None, ready=lambda: None, busy=False, installing=False, mic=None,
             clock=time.time):
        bus = events.EventBus()
        queue = jobs.KeyedQueues(bus, spawn=spawn or _spawn(lambda job: [
            {"kind": "job.result", "path": "id1"}]))
        captured = []

        def fake_record(device, out):
            captured.append((device, out))
            out.write_bytes(b"RIFF")
            return {"ok": True, "path": str(out), "device": device or "Микрофон", "fallback": False,
                    "seconds": 25.0, "rate": 48000}

        state = {"busy": busy, "installing": installing}
        takes = owner_voice_control.OwnerTakes(
            queue=queue, bus=bus, busy=lambda: state["busy"], installing=lambda: state["installing"],
            voices=lambda: tmp_path / "voices", mic=lambda: mic, log=lambda text: None,
            record=record or fake_record, ready=ready, clock=clock)
        takes.captured = captured
        takes.state = state
        takes.jobs = queue
        return takes
    return make


def _settled(takes):
    assert _wait(lambda: takes.status()["take"]["state"] not in owner_voice_control.ACTIVE)
    return takes.status()["take"]


def test_record_runs_probe_then_job_and_reports_done(setup, tmp_path):
    seen = []
    takes = setup(spawn=_spawn(lambda job: [{"kind": "job.result", "path": "abc"}], seen=seen))
    got = takes.record({"device": "USB-микрофон"})
    assert got["take"]["state"] in owner_voice_control.ACTIVE + ("done",)
    take = _settled(takes)
    ((device, wav),) = takes.captured
    assert device == "USB-микрофон" and wav.name == "owner.wav"
    assert take["state"] == "done" and take["sample_id"] == "abc" and take["error"] is None
    assert take["device"] == "USB-микрофон" and "dir" not in take
    (job,) = seen
    assert job.kind == jobs.OWNER_VOICE and job.folder == str(tmp_path / "voices")
    assert job.options == {"wav": str(wav), "device": "USB-микрофон"}
    assert not wav.parent.exists()  # временная папка записи — прочь


def test_system_mic_name_comes_from_the_probe(setup):
    takes = setup()
    takes.record({})
    take = _settled(takes)
    assert takes.captured[0][0] is None and take["device"] == "Микрофон"


def test_without_device_records_the_configured_mic(setup):
    """Мастер не выбирает микрофон: образец — с того, с которого пишутся встречи."""
    takes = setup(mic="USB-микрофон")
    takes.record({"device": None})
    assert _settled(takes)["device"] == "USB-микрофон"
    assert takes.captured[0][0] == "USB-микрофон"


def test_quality_error_reaches_the_window_in_plain_words(setup):
    takes = setup(spawn=_spawn(lambda job: [{"kind": "error", "text": "Голос не слышен."}], code=3))
    takes.record({})
    take = _settled(takes)
    assert take["state"] == "failed" and take["error"] == "Голос не слышен."
    assert not takes.captured[0][1].parent.exists()


def test_probe_failure_fails_the_take_without_a_job(setup):
    seen, outs = [], []

    def broken(device, out):
        outs.append(out)
        out.write_bytes(b"RIFF")  # даже если файл остался — папка уходит
        return {"ok": False, "error": "Устройство не ответило"}

    takes = setup(record=broken, spawn=_spawn(lambda job: [], seen=seen))
    takes.record({})
    take = _settled(takes)
    assert take["state"] == "failed" and "Устройство не ответило" in take["error"]
    assert seen == [] and not outs[0].parent.exists()


@pytest.mark.parametrize("answer", [None, "строка", ["список"]])
def test_strange_probe_answer_fails_and_cleans_up(setup, answer):
    outs = []

    def odd(device, out):
        outs.append(out)
        out.write_bytes(b"RIFF")
        return answer

    takes = setup(record=odd)
    takes.record({})
    assert _settled(takes)["state"] == "failed"
    assert not outs[0].parent.exists()
    takes.record({})  # не «уже записывается» навсегда


def test_crash_in_the_take_thread_fails_and_cleans_up(setup):
    outs = []

    def boom(device, out):
        outs.append(out)
        out.write_bytes(b"RIFF")
        raise OSError("сбой")

    takes = setup(record=boom)
    takes.record({})
    take = _settled(takes)
    assert take["state"] == "failed" and "OSError" in take["error"]
    assert not outs[0].parent.exists()


def test_failing_job_submit_fails_and_cleans_up(setup):
    takes = setup()

    def broken(*a, **kw):
        raise RuntimeError("очередь сломана")

    takes.jobs.submit_once = broken
    takes.record({})
    assert _settled(takes)["state"] == "failed"
    assert not takes.captured[0][1].parent.exists()


def test_stuck_recording_fails_after_timeout(setup):
    """Поток записи пропал (подпроцесс завис дольше таймаута) — попытка не
    висит «записывается» вечно, папка уходит."""
    now = [1000.0]
    release = threading.Event()
    outs = []

    def hang(device, out):
        outs.append(out)
        out.write_bytes(b"RIFF")
        release.wait(5)
        return {"ok": True, "device": "Микрофон"}

    takes = setup(record=hang, clock=lambda: now[0])
    takes.record({})
    assert _wait(lambda: outs)
    assert takes.status()["take"]["state"] == "recording"
    now[0] += owner_voice_control.STUCK_S + 1
    take = takes.status()["take"]
    assert take["state"] == "failed" and take["error"] == owner_voice_control.STUCK
    assert not outs[0].parent.exists()
    release.set()  # поздно вернувшаяся запись итог не меняет
    time.sleep(0.05)
    assert takes.status()["take"]["state"] == "failed"
    takes.record({})


def test_meeting_started_mid_take_aborts_it(setup):
    seen = []
    takes = setup(spawn=_spawn(lambda job: [], seen=seen))

    def record(device, out):
        out.write_bytes(b"RIFF")
        takes.state["busy"] = True  # автозапись звонка началась во время образца
        return {"ok": True, "device": "Микрофон"}

    takes._record = record
    takes.record({})
    take = _settled(takes)
    assert take["state"] == "failed" and take["error"] == owner_voice_control.STARTED_RECORDING
    assert seen == []


def test_engine_install_started_mid_take_aborts_it(setup):
    seen = []
    takes = setup(spawn=_spawn(lambda job: [], seen=seen))

    def record(device, out):
        out.write_bytes(b"RIFF")
        takes.state["installing"] = True
        return {"ok": True, "device": "Микрофон"}

    takes._record = record
    takes.record({})
    assert _settled(takes)["error"] == owner_voice_control.ENGINE_INSTALLING
    assert seen == []


def test_refused_while_a_meeting_is_recorded(setup):
    takes = setup(busy=True)
    with pytest.raises(control.Conflict, match="Идёт запись"):
        takes.record({})
    assert takes.captured == [] and takes.status()["recording"] is True


def test_refused_while_the_engine_is_being_installed(setup):
    takes = setup(installing=True)
    with pytest.raises(control.Conflict, match="установка движка"):
        takes.record({})


def test_refused_without_model_or_token(setup):
    takes = setup(ready=lambda: owner_voice_control.NO_TOKEN)
    with pytest.raises(control.Conflict, match="Hugging Face"):
        takes.record({})
    got = takes.status()
    assert got["ready"] is False and got["reason"] == owner_voice_control.NO_TOKEN


def test_one_take_at_a_time(setup):
    gate = threading.Event()
    takes = setup(spawn=_spawn(lambda job: gate.wait(5) and [{"kind": "job.result", "path": "x"}]))
    takes.record({})
    assert takes.active()
    with pytest.raises(control.Conflict, match="уже записывается"):
        takes.record({})
    gate.set()
    assert _settled(takes)["state"] == "done"
    assert not takes.active()
    takes.record({})  # после конца — снова можно


def test_slot_busy_with_another_job_fails_instead_of_attaching(setup, tmp_path):
    gate = threading.Event()
    takes = setup(spawn=_spawn(lambda job: gate.wait(5) and []))
    other, _ = takes.jobs.submit_once(jobs.OWNER_VOICE, str(tmp_path / "voices"), {"wav": "чужой.wav"})
    takes.record({})
    take = _settled(takes)
    assert take["state"] == "failed" and take["error"] == owner_voice_control.BUSY_JOB
    assert not takes.captured[0][1].parent.exists()
    gate.set()


def test_bad_device_is_a_bad_request(setup):
    with pytest.raises(control.BadRequest):
        setup().record({"device": 5})


def test_status_lists_samples_without_vectors_and_delete_removes(setup, tmp_path):
    takes = setup()
    sample = owner_voice.add(np.eye(8)[0], source="enroll", seconds=21.5, device="Микрофон",
                             quality=0.81, voices=tmp_path / "voices", date="2026-10-05")
    got = takes.status()
    assert got["samples"] == [{"id": sample.id, "source": "enroll", "date": "2026-10-05",
                               "seconds": 21.5, "device": "Микрофон", "recording": None,
                               "quality": 0.81}]
    assert got["take"] is None and got["ready"] is True and got["seconds"] == 25.0
    assert takes.delete(sample.id)["samples"] == []
    assert takes.delete(sample.id) == {"error": "образца нет"}


def test_cancelled_job_settles_and_cleans_up_without_polling(setup):
    """Снятая задача события не шлёт; окно закрыто (status() не зовут) — папку
    записи всё равно убирает поток попытки."""
    gate = threading.Event()
    takes = setup(spawn=_spawn(lambda job: gate.wait(5) and []))
    takes.record({})
    assert _wait(lambda: takes._take["job"] is not None)
    job_id = takes._take["job"]
    folder = takes.captured[0][1].parent
    assert _wait(lambda: takes.jobs.get(job_id).state == jobs.RUNNING)
    assert takes.jobs.cancel(job_id)
    gate.set()
    assert _wait(lambda: takes._take["state"] == "failed")
    assert not folder.exists()


def test_readiness_names_what_is_missing(monkeypatch):
    from meet import credentials, engine, models

    monkeypatch.setattr(engine, "installed", lambda m: m != "pyannote.audio")
    assert owner_voice_control.readiness() == owner_voice_control.NO_ENGINE
    monkeypatch.setattr(engine, "installed", lambda m: True)
    monkeypatch.setattr(credentials, "hf_token_source", lambda: None)
    assert owner_voice_control.readiness() == owner_voice_control.NO_TOKEN
    monkeypatch.setattr(credentials, "hf_token_source", lambda: "keyring")
    monkeypatch.setattr(models, "downloaded", lambda repo: False)
    assert owner_voice_control.readiness() == owner_voice_control.NO_MODEL
    assert owner_voice_control.readiness(downloading=True) == owner_voice_control.MODEL_DOWNLOADING
    monkeypatch.setattr(models, "downloaded", lambda repo: True)
    assert owner_voice_control.readiness() is None


def test_default_readiness_sees_the_model_downloading(tmp_path, monkeypatch):
    """Мастер отпускает с шага моделей, пока модель качается: шаг голоса
    говорит «ещё скачивается», а не «скачайте»."""
    from meet.diarize import DIARIZATION_MODEL

    seen = []
    monkeypatch.setattr(owner_voice_control, "readiness", lambda downloading=False: seen.append(
        downloading) or None)
    gate = threading.Event()
    queue = jobs.KeyedQueues(events.EventBus(), spawn=_spawn(lambda job: gate.wait(5) and []))
    takes = owner_voice_control.OwnerTakes(queue=queue, bus=events.EventBus(), busy=lambda: False,
                                           voices=lambda: tmp_path)
    takes.status()
    queue.submit_once(jobs.DOWNLOAD_MODEL, DIARIZATION_MODEL, {})
    takes.status()
    gate.set()
    assert seen == [False, True]


def test_capture_asks_the_probe_subprocess(monkeypatch, tmp_path):
    import os

    from meet import tray_control

    calls = []
    monkeypatch.setattr(tray_control, "_run_probe", lambda args, failed, timeout: calls.append(
        (args, failed, timeout)) or {"ok": True})
    owner_voice_control.capture("USB", tmp_path / "a.wav")
    ((args, failed, timeout),) = calls
    assert args == ["--record", "mic", "--seconds", "25.0", "--out", str(tmp_path / "a.wav"),
                    "--parent-pid", str(os.getpid()), "--name", "USB"]
    assert failed == "ok" and timeout > 25


# --- маршруты -------------------------------------------------------------------


class _State:
    def __init__(self):
        self.bus = events.EventBus()
        self.calls = []

    def owner_voice(self):
        self.calls.append("status")
        return {"samples": [], "take": None, "ready": True}

    def owner_voice_record(self, body):
        if body.get("device") == "занят":
            raise control.Conflict(owner_voice_control.BUSY_RECORDING)
        self.calls.append(("record", body))
        return {"samples": [], "take": {"state": "recording"}, "ready": True}

    def owner_voice_delete(self, sample_id):
        self.calls.append(("delete", sample_id))
        return {"error": "образца нет"} if sample_id == "missing" else {"samples": []}


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    srv = control.ControlServer(_State())
    srv.start(pid=4242)
    yield srv
    srv.stop(pid=4242)


def _call(srv, path, method="GET", payload=None, expect=200):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {srv.token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == expect
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        assert e.code == expect
        return json.loads(e.read())


def test_owner_voice_routes(server):
    assert _call(server, "/owner-voice")["ready"] is True
    assert _call(server, "/owner-voice/record", "POST", {"device": "USB"})["take"]["state"] == "recording"
    assert "Идёт запись" in _call(server, "/owner-voice/record", "POST", {"device": "занят"},
                                  expect=409)["error"]
    assert _call(server, "/owner-voice/ab%20c", "DELETE") == {"samples": []}
    assert _call(server, "/owner-voice/missing", "DELETE", expect=404) == {"error": "образца нет"}
    assert server.state.calls == ["status", ("record", {"device": "USB"}), ("delete", "ab c"),
                                  ("delete", "missing")]


# --- в резиденте ----------------------------------------------------------------


def test_tray_control_refuses_take_while_recording(monkeypatch, tmp_path):
    from meet import tray, tray_control

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setattr(owner_voice_control, "readiness", lambda downloading=False: None)
    app = tray.TrayApp()
    state = tray_control.TrayControl(app)
    assert state.owner_voice()["take"] is None
    app.recording = True
    with pytest.raises(control.Conflict, match="Идёт запись"):
        state.owner_voice_record({})
    assert state.owner_voice_delete("нет") == {"error": "образца нет"}



def test_engine_install_is_refused_during_a_take(monkeypatch, tmp_path):
    from meet import tray, tray_control

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    state = tray_control.TrayControl(tray.TrayApp())
    monkeypatch.setattr(state.owner_takes, "active", lambda: True)
    with pytest.raises(control.Conflict, match="образец голоса"):
        state.install_engine({})
