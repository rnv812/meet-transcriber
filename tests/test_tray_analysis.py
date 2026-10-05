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
        # Конфиг 0.3.0 (секция анализа есть): без неё это обновившийся с 0.2.x,
        # у которого авто-анализ ждёт ответа на предложение (тесты consent ниже).
        "analysis": {},
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


def _cancellable(app):
    """Очередь, чья задача идёт, пока её не отменят (как убитый подпроцесс)."""
    import time as _time

    def spawn(job, on_line):
        deadline = _time.monotonic() + 5
        while job.state != jobs.CANCELLED and _time.monotonic() < deadline:
            _time.sleep(0.01)
        return 1

    return jobs.JobQueue(app.bus, spawn=spawn)


def _wait(cond, timeout=5.0):
    import time as _time

    deadline = _time.monotonic() + timeout
    while not cond():
        assert _time.monotonic() < deadline, "не дождались"
        _time.sleep(0.01)


@pytest.mark.parametrize("running", [False, True])
def test_delete_cancels_the_analysis_instead_of_refusing(app, tmp_path, monkeypatch, running):
    _installed(monkeypatch, claude_code=True)
    llm_queue = _cancellable(app)
    st = tray_control.TrayControl(app, queue=jobs.JobQueue(app.bus, spawn=lambda j, f: 0),
                                  llm_queue=llm_queue)
    st._background = lambda fn, name=None: fn()
    try:
        busy = None
        if not running:  # первая задача занимает слот, анализ ждёт за ней
            busy = llm_queue.submit(jobs.SUMMARY, str(tmp_path / "другая"))
        job = st.make_analysis(RID)
        if running:
            _wait(lambda: llm_queue.get(job["id"]).state == jobs.RUNNING)
        assert st.delete_recording(RID) == {"ok": True}
        assert llm_queue.get(job["id"]).state == jobs.CANCELLED
        assert not _folder(tmp_path).exists()
    finally:
        if busy is not None:
            llm_queue.cancel(busy.id)
        llm_queue.stop()


def test_summary_still_blocks_delete(state):
    state.llm_queue.submit(jobs.SUMMARY, str(state._folder(RID)))
    with pytest.raises(control.BadRequest, match="модели"):
        state.delete_recording(RID)


def test_merge_cancels_the_parts_analysis(app, tmp_path, monkeypatch):
    from meet import merge

    _installed(monkeypatch, claude_code=True)
    llm_queue = _cancellable(app)
    state = tray_control.TrayControl(app, queue=jobs.JobQueue(app.bus, spawn=lambda j, f: 0),
                                     llm_queue=llm_queue)
    state._background = lambda fn, name=None: fn()
    second = tmp_path / "recordings" / "2026-10-01_10-30"
    second.mkdir()
    (second / "sys.opus").write_bytes(b"x")
    _transcript(second)
    job = state.make_analysis(RID)
    other = state.make_analysis(second.name)
    target = tmp_path / "recordings" / "2026-10-01_09-30_merged"
    target.mkdir()
    monkeypatch.setattr(merge, "create", lambda root, folders, keep_originals: target)
    state.merge_recordings({"ids": [RID, second.name], "keep_originals": True})
    assert state.llm_queue.get(job["id"]).state == jobs.CANCELLED
    assert state.llm_queue.get(other["id"]).state == jobs.CANCELLED
    llm_queue.stop()


def test_routes_exist():
    paths = [(m, p.pattern) for m, p, _ in control._PATTERNS]
    assert ("GET", r"^/recordings/([^/]+)/analysis$") in paths
    assert ("POST", r"^/recordings/([^/]+)/analysis$") in paths
    assert ("POST", r"^/recordings/([^/]+)/title/suggest$") in paths
    assert ("POST", r"^/recordings/([^/]+)/analysis/consent$") in paths


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


def test_rediarize_queues_unless_the_analysis_is_fresh(state, tmp_path, monkeypatch):
    folder = _folder(tmp_path)
    monkeypatch.setattr(state, "_speakers_change", lambda rid, change: {"ok": True})
    _write_analysis(folder)
    state.speakers_rediarize_apply(RID)
    assert _analyze_jobs(state) == []  # свежий
    _write_analysis(folder, fresh=False)
    state.speakers_rediarize_apply(RID)
    assert len(_analyze_jobs(state)) == 1


def test_rediarize_analyses_a_recording_without_analysis_when_auto(state, tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_speakers_change", lambda rid, change: {"ok": True})
    _write_config(tmp_path, analysis={"auto": False})
    state.speakers_rediarize_apply(RID)
    assert _analyze_jobs(state) == []
    _write_config(tmp_path)
    state.speakers_rediarize_apply(RID)
    assert len(_analyze_jobs(state)) == 1


def test_speaker_split_requeues_only_a_stale_analysis(state, tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_speakers_change", lambda rid, change: {"ok": True})
    state.speakers_split_apply(RID, {})
    assert _analyze_jobs(state) == []  # анализа не было: разделение спикера текст не меняет


# --- перезапуск резидента (pending_analysis) --------------------------------------------


def _pending(tmp_path):
    return library.read_meta(_folder(tmp_path)).get("pending_analysis")


def test_queued_analysis_is_marked_and_the_mark_is_cleared_when_done(state, app, tmp_path):
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    assert _pending(tmp_path)["manual"] is False
    _write_analysis(_folder(tmp_path))
    state._analysis_finished(_folder(tmp_path), jobs.DONE)
    assert _pending(tmp_path) is None


def test_mark_survives_a_job_killed_by_shutdown(state, tmp_path):
    state.make_analysis(RID)
    assert _pending(tmp_path)["manual"] is True
    state._analysis_finished(_folder(tmp_path), jobs.FAILED, stopping=True)
    assert _pending(tmp_path) is not None


def test_cancel_and_failure_clear_the_mark(state, tmp_path):
    state.make_analysis(RID)
    state._analysis_finished(_folder(tmp_path), jobs.CANCELLED)
    assert _pending(tmp_path) is None
    state._mark_analysis(_folder(tmp_path), True)
    state._analysis_finished(_folder(tmp_path), jobs.FAILED, job={"error": "Killed", "started_at": 5.0})
    assert _pending(tmp_path) is None
    # процесс умер, не записав ошибку, — её записывает резидент: окну есть что показать
    assert library.read_meta(_folder(tmp_path))["analysis_error"]["error"] == "Killed"


def test_deferred_analysis_is_marked(state, app, tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_busy_now", lambda: {"x"})
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    assert _pending(tmp_path) is not None


def _restart(app):
    """Новый резидент: задачи ставятся, но не запускаются — смотрим, что поставлено."""
    llm_queue = jobs.JobQueue(app.bus, spawn=lambda job, on_line: 0)
    llm_queue._ensure_worker = lambda: None
    st = tray_control.TrayControl(app, queue=jobs.JobQueue(app.bus, spawn=lambda j, f: 0),
                                  llm_queue=llm_queue)
    st._background = lambda fn, name=None: fn()
    return st


def _mark(tmp_path, manual):
    import time as _time

    library.write_meta(_folder(tmp_path), {"pending_analysis": {"at": _time.time(), "manual": manual}})


def test_recover_requeues_a_marked_analysis(app, tmp_path, monkeypatch):
    _installed(monkeypatch, claude_code=True)
    _mark(tmp_path, False)
    st = _restart(app)
    done = st.recover()
    assert done["analysis"] == [RID]
    assert [j["kind"] for j in st.llm_queue.listing()] == [jobs.ANALYZE]


def test_recover_skips_fresh_and_keeps_the_mark_without_a_provider(app, tmp_path, monkeypatch):
    _mark(tmp_path, False)
    _installed(monkeypatch)
    st = _restart(app)
    st.recover()
    assert st.llm_queue.listing() == [] and _pending(tmp_path) is not None
    _installed(monkeypatch, claude_code=True)
    _write_analysis(_folder(tmp_path))
    st.recover()
    assert st.llm_queue.listing() == [] and _pending(tmp_path) is None


def test_recover_manual_mark_ignores_auto_off_and_defers_while_recording(app, tmp_path, monkeypatch):
    _installed(monkeypatch, claude_code=True)
    _write_config(tmp_path, analysis={"auto": False})
    _mark(tmp_path, True)
    st = _restart(app)
    busy = {"on": True}
    monkeypatch.setattr(st, "_busy_now", lambda: {"x"} if busy["on"] else set())
    st.recover()
    assert st.llm_queue.listing() == [] and _pending(tmp_path) is not None
    busy["on"] = False
    st._flush_deferred_analysis()
    assert [j["kind"] for j in st.llm_queue.listing()] == [jobs.ANALYZE]


def test_recover_drops_an_auto_mark_when_auto_is_off(app, tmp_path, monkeypatch):
    _installed(monkeypatch, claude_code=True)
    _write_config(tmp_path, analysis={"auto": False})
    _mark(tmp_path, False)
    st = _restart(app)
    st.recover()
    assert st.llm_queue.listing() == [] and _pending(tmp_path) is None


# --- очередь: просьба человека, повтор, гонка с правкой ---------------------------------


def test_manual_request_promotes_a_queued_background_analysis(state, app, tmp_path):
    state.llm_queue.submit(jobs.SUMMARY, str(tmp_path / "занято"))
    other = tmp_path / "recordings" / "2026-10-01_08-00"
    other.mkdir()
    (other / "sys.opus").write_bytes(b"x")
    _transcript(other)
    _done(app, jobs.TRANSCRIBE, other)
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    mine = state.make_analysis(RID)
    pending = state.llm_queue._pending
    first_other = next(j["id"] for j in _analyze_jobs(state) if Path(j["folder"]) == other)
    assert pending.index(mine["id"]) < pending.index(first_other)


def test_manual_rerun_ignores_auto_off(state, tmp_path):
    _write_config(tmp_path, analysis={"auto": False})
    job = state.make_analysis(RID)
    state.llm_queue.get(job["id"]).state = jobs.RUNNING
    state.make_analysis(RID)  # пока идёт — «повторить после»
    state.llm_queue.get(job["id"]).state = jobs.DONE
    _write_analysis(_folder(tmp_path), fresh=False)
    state._analysis_finished(_folder(tmp_path), jobs.DONE)
    queued = [j for j in _analyze_jobs(state) if j["state"] == "queued"]
    assert len(queued) == 1


def test_run_finishing_after_an_edit_is_stale_and_sets_no_title(state, app, tmp_path):
    from meet import settings as settings_mod
    from meet.llm.base import AgentReply

    _write_config(tmp_path, assistant={"auto_title": True})
    folder = _folder(tmp_path)
    seen = _events(app, tray_control.ANALYSIS_UPDATED)

    async def runner(prompt, **kwargs):
        # Пока модель думает, человек правит текст.
        _transcript(folder, text="Решили выпустить бету позже.")
        return AgentReply(text=json.dumps({"chapters": [], "title": "Бета в пятницу"}, ensure_ascii=False))

    doc = analysis.run(folder, runner, settings_mod.load(), features=("chapters", "title"))
    analysis.write(folder, doc)
    state._analysis_finished(folder, jobs.DONE)
    assert state.analysis(RID)["state"] == "stale"
    assert seen[-1] == {"id": RID, "state": "stale"}
    assert "title" not in _meta(tmp_path)


def test_flush_while_still_busy_keeps_the_deferral(state, app, tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_busy_now", lambda: {"x"})
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    state._flush_deferred_analysis()
    assert _analyze_jobs(state) == [] and len(state._analysis_deferred) == 1


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


# --- название от модели -----------------------------------------------------------------


def _meta(tmp_path):
    return library.read_meta(_folder(tmp_path))


def test_analysis_title_applied_only_when_enabled(state, tmp_path):
    folder = _folder(tmp_path)
    _write_analysis(folder)
    state._analysis_finished(folder, jobs.DONE)
    assert "title" not in _meta(tmp_path)
    _write_config(tmp_path, assistant={"auto_title": True})
    state._analysis_finished(folder, jobs.DONE)
    assert _meta(tmp_path)["title"] == "Бета в пятницу"
    assert state.recording(RID)["title_source"] == "ai"


def test_user_title_is_never_overwritten(state, tmp_path):
    _write_config(tmp_path, assistant={"auto_title": True})
    state.update_recording(RID, {"title": "Моё название"})
    assert _meta(tmp_path)["title_source"] == "user"
    _write_analysis(_folder(tmp_path))
    state._analysis_finished(_folder(tmp_path), jobs.DONE)
    assert _meta(tmp_path)["title"] == "Моё название"


def test_clearing_the_title_returns_to_auto(state, tmp_path):
    state.update_recording(RID, {"title": "Моё название"})
    state.update_recording(RID, {"title": ""})
    assert state.recording(RID)["title_source"] == "auto"
    assert "title_source" not in _meta(tmp_path)


def test_accepting_a_suggestion_marks_it_ai(state, tmp_path):
    got = state.update_recording(RID, {"title": "Бета в пятницу", "title_source": "ai"})
    assert got["title_source"] == "ai"


def test_summary_title_line_is_applied(state, app, tmp_path):
    _write_config(tmp_path, assistant={"auto_title": True})
    library.write_meta(_folder(tmp_path), {"summary_title": {"title": "Итоги беты", "at": 1.0}})
    _done(app, jobs.SUMMARY, _folder(tmp_path))
    assert _meta(tmp_path)["title"] == "Итоги беты"


def test_live_topic_title_when_the_recording_with_assistant_is_saved(state, app, tmp_path):
    """Тема, которую вёл ассистент, — черновое название записи с ассистентом:
    при её сохранении (source: live), а не по событию ассистента."""
    _write_config(tmp_path, assistant={"auto_title": True})
    folder = _folder(tmp_path)
    (folder / "live_state.json").write_text(json.dumps(
        {"summary": {"topic": "Запуск беты"}, "hints": []}, ensure_ascii=False), encoding="utf-8")
    state._on_saved(str(folder), tray_control.LIVE, False)
    assert _meta(tmp_path)["title"] == "Запуск беты"
    assert _meta(tmp_path)["title_source"] == "ai"


def test_browser_call_title_is_site(state, app, tmp_path, monkeypatch):
    folder = _folder(tmp_path)
    app.recording_title = "Google Meet"
    state._on_saved(str(folder), tray_control.AUTO, False)
    assert _meta(tmp_path)["title_source"] == "site"
    # общий заголовок окна модель может заменить
    _write_config(tmp_path, assistant={"auto_title": True})
    _write_analysis(folder)
    state._analysis_finished(folder, jobs.DONE)
    assert _meta(tmp_path)["title"] == "Бета в пятницу"


def test_ai_title_follows_into_the_knowledge_base(state, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_config(tmp_path, assistant={"auto_title": True},
                  export={"meetings_dir": str(vault), "auto_export": False})
    old = Path(state.kb_export(RID)["path"])
    _write_analysis(_folder(tmp_path), title="Бета в пятницу")
    state._analysis_finished(_folder(tmp_path), jobs.DONE)
    new = vault / "2026-10-01 - Бета в пятницу"
    assert not old.exists() and new.is_dir()


def test_suggest_title_from_fresh_analysis_needs_no_model(state, tmp_path, monkeypatch):
    monkeypatch.setattr(tray_control, "_suggest_title", lambda folder: pytest.fail("без вызова модели"))
    _write_analysis(_folder(tmp_path))
    assert state.suggest_title(RID) == {"title": "Бета в пятницу", "from": "analysis"}
    assert "title" not in _meta(tmp_path)  # только предложение


def test_suggest_title_calls_the_model_subprocess(state, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(tray_control, "_suggest_title",
                        lambda folder: seen.append(folder) or {"title": "Запуск беты", "from": "model"})
    assert state.suggest_title(RID) == {"title": "Запуск беты", "from": "model"}
    assert seen == [_folder(tmp_path)]
    monkeypatch.setattr(tray_control, "_suggest_title", lambda folder: {"error": "rate_limit"})
    with pytest.raises(RuntimeError, match="rate_limit"):
        state.suggest_title(RID)
    _installed(monkeypatch)
    with pytest.raises(control.Conflict):
        state.suggest_title(RID)


def test_suggest_title_runs_one_model_call_per_recording(state, tmp_path, monkeypatch):
    import threading as _threading

    started, release, calls = _threading.Event(), _threading.Event(), []

    def slow(folder):
        calls.append(folder)
        started.set()
        release.wait(5)
        return {"title": "Запуск беты", "from": "model"}

    monkeypatch.setattr(tray_control, "_suggest_title", slow)
    results = []
    first = _threading.Thread(target=lambda: results.append(state.suggest_title(RID)))
    first.start()
    started.wait(5)
    second = _threading.Thread(target=lambda: results.append(state.suggest_title(RID)))
    second.start()
    __import__("time").sleep(0.3)  # второй запрос успевает встать в ожидание первого
    release.set()
    first.join(5)
    second.join(5)
    assert len(calls) == 1 and results == [{"title": "Запуск беты", "from": "model"}] * 2


def test_accepted_suggestion_is_not_replaced_automatically(state, tmp_path):
    _write_config(tmp_path, assistant={"auto_title": True})
    state.update_recording(RID, {"title": "Принятое", "title_source": "ai"})
    assert _meta(tmp_path)["title_accepted"] is True
    _write_analysis(_folder(tmp_path))
    state._analysis_finished(_folder(tmp_path), jobs.DONE)
    assert _meta(tmp_path)["title"] == "Принятое"
    assert state.recording(RID)["title_source"] == "ai"  # бейдж остаётся
    state.update_recording(RID, {"title": "Моё"})
    assert "title_accepted" not in _meta(tmp_path)


# --- отмена ждущего анализа снимает отметку (fix round 2) -------------------------------


def _queued_behind_summary(state, tmp_path):
    """Анализ ждёт за чужой задачей (ждущая задача снимается без события)."""
    state.llm_queue.submit(jobs.SUMMARY, str(tmp_path / "занято"))
    job = state.make_analysis(RID)
    assert state.llm_queue.get(job["id"]).state == jobs.QUEUED
    assert _pending(tmp_path) is not None
    return job


def test_cancelling_a_queued_analysis_clears_the_mark(app, state, tmp_path, monkeypatch):
    job = _queued_behind_summary(state, tmp_path)
    assert state.cancel_job(job["id"]) == {"ok": True}
    assert _pending(tmp_path) is None
    assert "analysis" not in _restart(app).recover()


def test_drop_of_a_queued_or_deferred_analysis_clears_the_mark(app, state, tmp_path, monkeypatch):
    _queued_behind_summary(state, tmp_path)
    state._drop_analysis(_folder(tmp_path))
    assert _pending(tmp_path) is None
    monkeypatch.setattr(state, "_busy_now", lambda: {"x"})
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))  # отложен
    assert _pending(tmp_path) is not None
    state._drop_analysis(_folder(tmp_path))
    assert _pending(tmp_path) is None and not state._analysis_deferred


def test_merge_keeping_originals_does_not_bring_parts_analysis_back(app, state, tmp_path, monkeypatch):
    from meet import merge

    second = tmp_path / "recordings" / "2026-10-01_10-30"
    second.mkdir()
    (second / "sys.opus").write_bytes(b"x")
    _transcript(second)
    state.llm_queue.submit(jobs.SUMMARY, str(tmp_path / "занято"))
    state.make_analysis(RID)
    state.make_analysis(second.name)
    target = tmp_path / "recordings" / "2026-10-01_09-30_merged"
    target.mkdir()
    monkeypatch.setattr(merge, "create", lambda root, folders, keep_originals: target)
    state.merge_recordings({"ids": [RID, second.name], "keep_originals": True})
    assert _pending(tmp_path) is None and "pending_analysis" not in library.read_meta(second)
    assert "analysis" not in _restart(app).recover()


def test_refused_merge_leaves_the_parts_analysis_alone(state, tmp_path, monkeypatch):
    from meet import merge

    second = tmp_path / "recordings" / "2026-10-01_10-30"
    second.mkdir()
    (second / "sys.opus").write_bytes(b"x")
    _transcript(second)
    job = state.make_analysis(RID)

    def refuse(root, folders, keep_originals):
        raise merge.MergeError("Неизвестно время начала записи")

    monkeypatch.setattr(merge, "create", refuse)
    with pytest.raises(control.BadRequest):
        state.merge_recordings({"ids": [RID, second.name], "keep_originals": True})
    assert state.llm_queue.get(job["id"]).state in (jobs.QUEUED, jobs.RUNNING)
    assert _pending(tmp_path) is not None


def test_recover_leaves_analysis_to_a_pending_retranscription(app, tmp_path, monkeypatch):
    import time as _time

    _installed(monkeypatch, claude_code=True)
    folder = _folder(tmp_path)
    library.write_meta(folder, {"pending_transcribe": _time.time() + 60,
                                "pending_analysis": {"at": _time.time(), "manual": False}})
    st = _restart(app)
    st.queue._ensure_worker = lambda: None
    done = st.recover()
    assert done["queued"] == [RID] and "analysis" not in done
    assert st.llm_queue.listing() == [] and _pending(tmp_path) is None


def test_fresh_analysis_clears_a_leftover_mark(state, tmp_path):
    _write_analysis(_folder(tmp_path))
    state._mark_analysis(_folder(tmp_path), True)
    state._auto_analyze(_folder(tmp_path))
    assert _pending(tmp_path) is None


def test_recover_handles_marks_on_old_recordings(app, tmp_path, monkeypatch):
    import time as _time

    _installed(monkeypatch, claude_code=True)
    old = tmp_path / "recordings" / "2020-01-01_10-00"
    old.mkdir()
    (old / "sys.opus").write_bytes(b"x")
    _transcript(old)
    library.write_meta(old, {"pending_analysis": {"at": _time.time(), "manual": True}})
    st = _restart(app)
    assert st.recover()["analysis"] == [old.name]


# --- обновившийся с 0.2.x: разовое предложение (analysis.consent) ---------------------


def _upgraded_config(tmp_path):
    """Конфиг 0.2.x: секции analysis нет — авто-анализ ждёт ответа."""
    path = tmp_path / "meet" / "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("analysis")
    data["version"] = 2
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_upgraded_user_is_not_analysed_until_the_answer(state, app, tmp_path):
    _upgraded_config(tmp_path)
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    assert _analyze_jobs(state) == []
    assert state.settings()["analysis"]["consent"] == "pending"


def test_granted_turns_auto_on_and_analyses_this_meeting(state, app, tmp_path):
    _upgraded_config(tmp_path)
    got = state.analysis_consent(RID, {"answer": "granted"})
    assert got["analysis"]["consent"] == "granted" and got["analysis"]["auto"] is True
    assert len(_analyze_jobs(state)) == 1
    # Дальше — как у всех: после расшифровки ставится сам, не спрашивая.
    from meet import settings as settings_mod
    assert settings_mod.load().analysis.consent == "granted"


def test_declined_keeps_auto_off_and_queues_nothing(state, app, tmp_path):
    _upgraded_config(tmp_path)
    got = state.analysis_consent(RID, {"answer": "declined"})
    assert got["analysis"] == {**got["analysis"], "consent": "declined", "auto": False}
    assert _analyze_jobs(state) == []
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    assert _analyze_jobs(state) == []
    assert state.settings()["analysis"]["consent"] == "declined"


def test_consent_answer_is_validated(state, tmp_path):
    _upgraded_config(tmp_path)
    with pytest.raises(control.BadRequest):
        state.analysis_consent(RID, {"answer": "pending"})
    with pytest.raises(control.BadRequest):
        state.analysis_consent(RID, {})
    assert state.settings()["analysis"]["consent"] == "pending"


def test_granted_for_a_short_meeting_does_not_force_its_analysis(state, app, tmp_path):
    _upgraded_config(tmp_path)
    _transcript(_folder(tmp_path), end=30.0)
    state.analysis_consent(RID, {"answer": "granted"})
    assert _analyze_jobs(state) == []
