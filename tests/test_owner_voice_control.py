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
from pathlib import Path

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
            record=record or fake_record, ready=ready, clock=clock,
            recordings=lambda: tmp_path / "recordings")
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


# --- «Найти по прошлым встречам» (T8) -------------------------------------------


def _derive_spawn(outcome, found=None, seen=None, gate=None):
    """Задача поиска: пишет итог рядом с образцами, как owner_derive.run."""
    def lines(job):
        if seen is not None:
            seen.append(job)
        if gate is not None:
            gate.wait(5)
        owner_voice.save_derived(outcome, found, voices=Path(job.folder))
        return [{"kind": "job.result", "path": outcome["status"]}]
    return _spawn(lines)


FOUND = {"embedding": np.eye(8)[0], "meetings": ["2026-10-01_10-00", "2026-10-02_10-00", "2026-10-03_10-00"],
         "samples": [{"recording": f"2026-10-0{d}_10-00", "start": 12.0, "end": 16.5, "track": "mic"}
                     for d in (1, 2, 3)], "seconds": 412.0, "quality": 0.83}


def test_derive_runs_job_and_status_shows_suggestion_without_vector(setup, tmp_path):
    seen = []
    for d in (1, 2, 3):
        _recording(tmp_path, f"2026-10-0{d}_10-00")
    takes = setup(spawn=_derive_spawn({"status": "suggested", "reason": None}, FOUND, seen))
    got = takes.derive()
    assert got["derive"]["running"] is True or got["suggestion"] is not None
    assert _wait(lambda: not takes.status()["derive"]["running"])
    (job,) = seen
    assert job.kind == jobs.OWNER_VOICE and job.folder == str(tmp_path / "voices")
    assert job.options == {"derive": True, "recordings": str(tmp_path / "recordings")}
    status = takes.status()
    assert status["suggestion"] == {k: v for k, v in FOUND.items() if k != "embedding"} | {
        "date": status["suggestion"]["date"], "conflict": False}
    assert status["derive"]["last"]["status"] == "suggested" and status["derive"]["error"] is None
    assert status["samples"] == []  # без подтверждения — не образец


def test_derive_nothing_found_reports_reason(setup):
    takes = setup(spawn=_derive_spawn({"status": "too_few", "reason": "Подходящих встреч пока 1."}))
    takes.derive()
    assert _wait(lambda: not takes.status()["derive"]["running"])
    status = takes.status()
    assert status["suggestion"] is None
    assert status["derive"]["last"]["reason"] == "Подходящих встреч пока 1."


def test_derive_crash_is_shown_as_error(setup):
    takes = setup(spawn=_spawn(lambda job: [{"kind": "error", "text": "сломалось"}], code=1))
    takes.derive()
    assert _wait(lambda: not takes.status()["derive"]["running"])
    assert "сломалось" in takes.status()["derive"]["error"]


def test_accept_stores_auto_sample_decline_clears(setup, tmp_path):
    takes = setup()
    voices = tmp_path / "voices"
    owner_voice.save_derived({"status": "suggested"}, FOUND, voices=voices)
    got = takes.answer({"accept": True})
    assert got["suggestion"] is None
    assert [s["source"] for s in got["samples"]] == ["auto"]
    with pytest.raises(control.Conflict, match="Предложения уже нет"):
        takes.answer({"accept": True})
    owner_voice.save_derived({"status": "suggested"}, FOUND, voices=voices)
    got = takes.answer({"accept": False})
    assert got["suggestion"] is None and [s["source"] for s in got["samples"]] == ["auto"]
    with pytest.raises(control.BadRequest):
        takes.answer({"accept": "да"})


def test_derive_refused_while_recording_installing_or_not_ready(setup):
    takes = setup(busy=True)
    with pytest.raises(control.Conflict, match="Идёт запись"):
        takes.derive()
    takes = setup(installing=True)
    with pytest.raises(control.Conflict, match="установка движка"):
        takes.derive()
    takes = setup(ready=lambda: owner_voice_control.NO_MODEL)
    with pytest.raises(control.Conflict, match="Скачайте модель"):
        takes.derive()


def test_derive_and_take_exclude_each_other(setup):
    gate = threading.Event()
    takes = setup(spawn=_derive_spawn({"status": "too_few", "reason": "мало"}, gate=gate))
    takes.derive()
    assert takes.active() is True  # движок в это время не ставят
    with pytest.raises(control.Conflict, match="поиск"):
        takes.record({})
    again = takes.derive()  # повторное нажатие — та же задача
    assert again["derive"]["running"] is True
    gate.set()
    assert _wait(lambda: not takes.status()["derive"]["running"])
    assert takes.active() is False


def test_derive_refused_during_take(setup):
    gate = threading.Event()
    takes = setup(spawn=_spawn(lambda job: gate.wait(5) and []))
    takes.record({})
    with pytest.raises(control.Conflict, match="записывается"):
        takes.derive()
    gate.set()


def _recording(tmp_path, name):
    folder = tmp_path / "recordings" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "mic.opus").write_bytes(b"")
    return folder


TODAY = 1_791_331_200.0 + 12 * 3600  # 2026-10-07, полдень по UTC


def test_stale_reasons_are_dropped(setup, tmp_path):
    """Причина последнего поиска перестала быть правдой — окно её не видит,
    и из файла она снимается."""
    voices = tmp_path / "voices"
    takes = setup(clock=lambda: TODAY)
    sample = owner_voice.add(np.eye(8)[0], source="enroll", seconds=20, voices=voices)
    owner_voice.save_derived({"status": "already", "reason": "уже есть", "sample_id": sample.id,
                              "date": "2026-10-06"}, None, voices=voices)
    assert takes.status()["derive"]["last"]["status"] == "already"
    owner_voice.remove(sample.id, voices)
    assert takes.status()["derive"]["last"] is None and owner_voice.derived(voices) is None

    (voices / "Демьян.json").write_text('{"samples": []}', encoding="utf-8")
    owner_voice.save_derived({"status": "in_base", "reason": "похож на «Демьян»", "person": "Демьян",
                              "date": "2026-10-06"}, None, voices=voices)
    assert takes.status()["derive"]["last"]["person"] == "Демьян"
    (voices / "Демьян.json").unlink()
    assert takes.status()["derive"]["last"] is None

    _recording(tmp_path, "2026-10-05_10-00")
    owner_voice.save_derived({"status": "too_few", "reason": "мало", "newest": "2026-10-05_10-00",
                              "date": "2026-10-06"}, None, voices=voices)
    assert takes.status()["derive"]["last"]["status"] == "too_few"
    _recording(tmp_path, "2026-10-06_15-00")  # новая встреча — повод искать снова
    assert takes.status()["derive"]["last"] is None


def test_old_reason_is_dropped_after_a_week(setup, tmp_path):
    voices = tmp_path / "voices"
    owner_voice.save_derived({"status": "inconsistent", "reason": "по-разному", "date": "2026-09-29"},
                             None, voices=voices)
    assert setup(clock=lambda: TODAY).status()["derive"]["last"] is None
    owner_voice.save_derived({"status": "inconsistent", "reason": "по-разному", "date": "2026-10-01"},
                             None, voices=voices)
    assert setup(clock=lambda: TODAY).status()["derive"]["last"]["reason"] == "по-разному"


def test_suggestion_drops_samples_of_deleted_recordings_and_keeps_conflict(setup, tmp_path):
    voices = tmp_path / "voices"
    for d in (1, 2):
        _recording(tmp_path, f"2026-10-0{d}_10-00")
    owner_voice.save_derived({"status": "suggested"}, {**FOUND, "conflict": True}, voices=voices)
    got = setup().status()["suggestion"]
    assert [r["recording"] for r in got["samples"]] == ["2026-10-01_10-00", "2026-10-02_10-00"]
    assert got["conflict"] is True


def test_derive_job_id_is_shown_and_stop_cancels(setup):
    gate = threading.Event()
    takes = setup(spawn=_derive_spawn({"status": "too_few", "reason": "мало"}, gate=gate))
    job_id = takes.derive()["derive"]["job"]
    assert job_id and takes.jobs.get(job_id).kind == jobs.OWNER_VOICE
    assert _wait(lambda: takes.jobs.get(job_id).state == jobs.RUNNING)
    assert takes.jobs.cancel(job_id)
    gate.set()
    assert _wait(lambda: not takes.status()["derive"]["running"])
    got = takes.status()["derive"]
    assert got["error"] is None and got["job"] is None


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

    def owner_voice_derive(self):
        self.calls.append("derive")
        return {"samples": [], "derive": {"running": True}}

    def owner_voice_suggestion(self, body):
        self.calls.append(("answer", body))
        return {"samples": [], "suggestion": None}


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


def test_owner_voice_derive_routes(server):
    assert _call(server, "/owner-voice/derive", "POST", {})["derive"]["running"] is True
    assert _call(server, "/owner-voice/suggestion", "POST", {"accept": True})["suggestion"] is None
    assert server.state.calls == ["derive", ("answer", {"accept": True})]


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


def test_tray_control_derive_and_answer(monkeypatch, tmp_path):
    from meet import tray, tray_control

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setattr(owner_voice_control, "readiness", lambda downloading=False: None)
    app = tray.TrayApp()
    state = tray_control.TrayControl(app)
    assert state.owner_voice()["suggestion"] is None
    with pytest.raises(control.Conflict, match="Предложения уже нет"):
        state.owner_voice_suggestion({"accept": True})
    app.recording = True
    with pytest.raises(control.Conflict, match="Идёт запись"):
        state.owner_voice_derive()
