"""Анализ встречи и название от модели в резиденте: API, автоматическая
постановка, защита от лишних вызовов, происхождение названия.

Модель не вызывается: задачи ставятся в очередь с подменённым spawn,
`detect.available` подменён. Данные выдуманы.
"""

import json
import threading
from pathlib import Path

import pytest

import meet.llm as llm
from meet import analysis, control, jobs, library, tray, tray_control
from meet.llm import detect

RID = "2026-10-01_09-30"
LONG = 900.0  # длиннее auto_record.min_call_seconds по умолчанию (120 с)


def _write_config(tmp_path, **sections) -> None:
    path = tmp_path / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(tmp_path / "recordings"),
                      "voices_dir": str(tmp_path / "voices")},
        "llm": {"provider": "auto"},
        "assistant": {"knowledge_dir": None, "notes_dir": None},
        "export": {"meetings_dir": None},
    }
    for name, value in sections.items():
        data[name] = {**data.get(name, {}), **value}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _transcript(folder: Path, end: float = LONG, text: str = "Решили выпустить бету в пятницу.") -> None:
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 5.0, "speaker": "Ольга", "text": "Начнём с беты."},
        {"start": 5.0, "end": end, "speaker": "SPEAKER_01", "text": text}]})


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    _write_config(tmp_path)
    folder = tmp_path / "recordings" / RID
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    _transcript(folder)
    return tray.TrayApp()


def _installed(monkeypatch, **found):
    def available(base_url=None, probe_local=True):
        return {name: {"found": found.get(name.replace("-", "_"), False)} for name in llm.PROVIDERS}
    monkeypatch.setattr(detect, "available", available)


@pytest.fixture
def state(app, monkeypatch):
    release = threading.Event()

    def spawn(job, on_line):
        release.wait(timeout=5)
        return 0

    queue = jobs.JobQueue(app.bus, spawn=spawn)
    llm_queue = jobs.JobQueue(app.bus, spawn=spawn)
    st = tray_control.TrayControl(app, queue=queue, llm_queue=llm_queue)
    st._background = lambda fn, name=None: fn()
    _installed(monkeypatch, claude_code=True)
    try:
        yield st
    finally:
        release.set()
        queue.stop()
        llm_queue.stop()


def _folder(tmp_path) -> Path:
    return tmp_path / "recordings" / RID


def _done(app, kind, folder, state="done"):
    app.bus.emit(jobs.JOB_DONE if state == "done" else jobs.JOB_FAILED,
                 job={"id": "j1", "kind": kind, "folder": str(folder), "state": state})


def _analyze_jobs(st):
    return [j for j in st.llm_queue.listing() if j["kind"] == jobs.ANALYZE]


def _write_analysis(folder: Path, title="Бета в пятницу", fresh=True):
    data = library.read_transcript(folder)
    fp = analysis.fingerprint(data) if fresh else "старый"
    return analysis.write(folder, {"version": 1, "model": "fake", "created_at": 1.0, "fingerprint": fp,
                                   "features": ["chapters", "title"], "chapters": [], "title": title})


def _events(app, kind):
    seen = []
    app.bus.subscribe(lambda e: seen.append(e.data) if e.kind == kind else None)
    return seen


# --- API ----------------------------------------------------------------------------


def test_analysis_state_none_then_queued(state, tmp_path):
    assert state.analysis(RID) == {"state": "none"}
    job = state.make_analysis(RID)
    assert job["kind"] == jobs.ANALYZE
    got = state.analysis(RID)
    assert got["state"] in ("queued", "running") and got["job"]["id"] == job["id"]
    assert state.analysis("../..") == {"error": "записи нет"}


def test_analysis_ready_stale_failed(state, tmp_path):
    folder = _folder(tmp_path)
    _write_analysis(folder)
    got = state.analysis(RID)
    assert got["state"] == "ready" and got["analysis"]["title"] == "Бета в пятницу"
    _transcript(folder, text="Решили выпустить бету в понедельник.")
    assert state.analysis(RID)["state"] == "stale"
    analysis.mark_failed(folder, "таймаут вызова модели")
    got = state.analysis(RID)
    assert got["state"] == "failed" and got["error"] == "таймаут вызова модели"


def test_manual_analysis_is_one_job_and_needs_a_provider(state, monkeypatch):
    first = state.make_analysis(RID)
    assert state.make_analysis(RID)["id"] == first["id"]
    assert len(_analyze_jobs(state)) == 1
    _installed(monkeypatch)
    state.llm_queue.cancel(first["id"])
    with pytest.raises(control.Conflict, match="Подключите"):
        state.make_analysis(RID)


def test_manual_analysis_waits_for_transcription(state, tmp_path):
    state.queue.submit(jobs.TRANSCRIBE, str(_folder(tmp_path)))
    with pytest.raises(control.Conflict, match="расшифровки"):
        state.make_analysis(RID)


def test_delete_refused_while_analysis_runs(state):
    state.make_analysis(RID)
    with pytest.raises(control.BadRequest, match="модели"):
        state.delete_recording(RID)


def test_routes_exist():
    paths = [(m, p.pattern) for m, p, _ in control._PATTERNS]
    assert ("GET", r"^/recordings/([^/]+)/analysis$") in paths
    assert ("POST", r"^/recordings/([^/]+)/analysis$") in paths


# --- автоматическая постановка ----------------------------------------------------------


def test_auto_analysis_after_transcription(state, app, tmp_path):
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    queued = _analyze_jobs(state)
    assert len(queued) == 1 and Path(queued[0]["folder"]) == _folder(tmp_path)


@pytest.mark.parametrize("kind", [jobs.IMPORT])
def test_auto_analysis_after_import(state, app, tmp_path, kind):
    _done(app, kind, _folder(tmp_path))
    assert len(_analyze_jobs(state)) == 1


def test_no_auto_analysis_when_switched_off(state, app, tmp_path):
    _write_config(tmp_path, analysis={"auto": False})
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    assert _analyze_jobs(state) == []


def test_no_auto_analysis_when_all_parts_are_off(state, app, tmp_path):
    _write_config(tmp_path, analysis={name: False for name in analysis.FEATURES})
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    assert _analyze_jobs(state) == []


def test_no_auto_analysis_for_a_short_meeting(state, app, tmp_path, monkeypatch):
    lines = []
    monkeypatch.setattr(app, "log", lines.append)
    _transcript(_folder(tmp_path), end=30.0)
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    assert _analyze_jobs(state) == []
    assert any("короче" in line for line in lines)


def test_no_auto_analysis_without_a_provider(state, app, tmp_path, monkeypatch):
    _installed(monkeypatch)
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    assert _analyze_jobs(state) == []


def test_no_auto_analysis_when_it_is_already_fresh(state, app, tmp_path):
    _write_analysis(_folder(tmp_path))
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    assert _analyze_jobs(state) == []


def test_auto_analysis_is_deferred_during_a_recording(state, app, tmp_path, monkeypatch):
    busy = {"on": True}
    monkeypatch.setattr(state, "_busy_now", lambda: {"x"} if busy["on"] else set())
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    assert _analyze_jobs(state) == []
    busy["on"] = False
    state._on_saved(str(tmp_path / "recordings" / "другая"), None, False)
    assert len(_analyze_jobs(state)) == 1


def test_no_double_queue_and_rerun_after_the_running_one(state, app, tmp_path, monkeypatch):
    folder = _folder(tmp_path)
    _done(app, jobs.TRANSCRIBE, folder)
    _done(app, jobs.TRANSCRIBE, folder)
    assert len(_analyze_jobs(state)) == 1
    # идущий анализ: новый повод — пометка «повторить после», а не вторая задача
    job = state.llm_queue.active_for(str(folder), (jobs.ANALYZE,))
    job.state = jobs.RUNNING
    _transcript(folder, text="Решили выпустить бету в понедельник.")
    state._queue_analysis(folder, low=True)
    assert len(_analyze_jobs(state)) == 1
    state.llm_queue.cancel(job.id)
    job.state = jobs.DONE
    state._analysis_finished(folder, jobs.DONE)
    assert len([j for j in _analyze_jobs(state) if j["state"] == "queued"]) == 1


def test_automatic_analysis_waits_behind_manual_model_work(state, app, tmp_path):
    # Резидент занят: первая задача идёт, остальные ждут в очереди.
    state.llm_queue.submit(jobs.SUMMARY, str(tmp_path / "занято"))
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    summary = state.llm_queue.submit(jobs.SUMMARY, str(_folder(tmp_path)))
    pending = state.llm_queue._pending
    assert pending.index(summary.id) < pending.index(_analyze_jobs(state)[0]["id"])


def test_rediarize_requeues_only_a_stale_analysis(state, tmp_path, monkeypatch):
    folder = _folder(tmp_path)
    monkeypatch.setattr(state, "_speakers_change", lambda rid, change: {"ok": True})
    state.speakers_rediarize_apply(RID)
    assert _analyze_jobs(state) == []  # анализа не было — переразделение его не заводит
    _write_analysis(folder)
    state.speakers_rediarize_apply(RID)
    assert _analyze_jobs(state) == []  # свежий
    _write_analysis(folder, fresh=False)
    state.speakers_rediarize_apply(RID)
    assert len(_analyze_jobs(state)) == 1


def test_text_edit_marks_the_analysis_stale(state, app, tmp_path):
    seen = _events(app, tray_control.ANALYSIS_UPDATED)
    folder = _folder(tmp_path)
    _write_analysis(folder)
    data = library.read_transcript(folder)
    data["segments"][1]["text"] = "Решили выпустить бету позже."
    state.save_transcript(RID, data)
    assert seen == [{"id": RID, "state": "stale"}]
    assert _analyze_jobs(state) == []  # правки текста анализ сами не перезапускают


def test_finished_analysis_emits_event(state, app, tmp_path):
    seen = _events(app, tray_control.ANALYSIS_UPDATED)
    _write_analysis(_folder(tmp_path))
    state._analysis_finished(_folder(tmp_path), jobs.DONE)
    assert seen == [{"id": RID, "state": "ready"}]
