"""«Улучшить расшифровку» в резиденте: API, одна задача на запись, применение
одним шагом истории (с отказом, пока запись занята), правила и термины по
желанию, автоматическая постановка, отметка для перезапуска.

Модель не вызывается: задачи ставятся в очередь с подменённым spawn,
`detect.available` подменён. Данные выдуманы.
"""

import json
import threading
from pathlib import Path

import pytest

import meet.llm as llm
from meet import control, improve, jobs, library, paths, settings, tray, tray_control
from meet.llm import detect

RID = "2026-10-02_14-00"


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
        "analysis": {"auto": False},
    }
    for name, value in sections.items():
        data[name] = {**data.get(name, {}), **value}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


SEGMENTS = [
    {"start": 0.0, "end": 4.0, "speaker": "Спикер 1", "text": "Апи сервиса отвечает медленно."},
    {"start": 4.0, "end": 900.0, "speaker": "Спикер 2", "text": "Значит, смотрим кафка и апи шлюза."},
]


def _transcript(folder: Path, backend="gigaam") -> None:
    library.write_transcript(folder, {"version": 1, "created_at": "2026-10-02T15:00:00",
                                      "asr": {"backend": backend, "device": "cpu"},
                                      "segments": json.loads(json.dumps(SEGMENTS))})


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


def _improve_jobs(st):
    return [j for j in st.llm_queue.listing() if j["kind"] == jobs.IMPROVE]


def _proposal(folder: Path) -> dict:
    data = library.read_transcript_full(folder)
    pairs = [{"find": "апи", "replace": "API", "kind": "term", "confidence": 0.9, "segments": [0, 1]},
             {"find": "кафка", "replace": "Kafka", "kind": "term", "confidence": 0.9, "segments": [1]},
             {"find": "медленно", "replace": "медленнее", "kind": "fix", "confidence": 0.6, "segments": [0]}]
    doc = {"version": 1, "model": "fake", "created_at": 1.0, "fingerprint": improve.fingerprint(data),
           "segments": 2, "groups": improve.build_groups(data, pairs)}
    improve.write(folder, doc)
    return doc


def _events(app, kind):
    seen = []
    app.bus.subscribe(lambda e: seen.append(e.data) if e.kind == kind else None)
    return seen


def test_state_none_with_hint_then_one_queued_job(state, tmp_path):
    got = state.improve(RID)
    assert got == {"state": "none", "hint": True}
    job = state.make_improve(RID)
    assert job["kind"] == jobs.IMPROVE
    assert state.make_improve(RID)["id"] == job["id"]
    assert len(_improve_jobs(state)) == 1
    got = state.improve(RID)
    assert got["state"] in ("queued", "running") and got["job"]["id"] == job["id"] and got["hint"] is False
    # Запуск улучшения — подсказка своё сделала.
    assert library.read_meta(_folder(tmp_path))["improve_hint"] == "done"
    assert library.read_meta(_folder(tmp_path))["pending_improve"]["manual"] is True
    assert state.improve("../..") == {"error": "записи нет"}


def test_no_provider_or_transcription_running_is_409(state, monkeypatch, tmp_path):
    _installed(monkeypatch)
    with pytest.raises(control.Conflict, match="Подключите"):
        state.make_improve(RID)
    _installed(monkeypatch, claude_code=True)
    state.queue.submit(jobs.TRANSCRIBE, str(_folder(tmp_path)), {})
    with pytest.raises(control.Conflict, match="расшифровки"):
        state.make_improve(RID)


def test_dismiss_hides_the_hint(state):
    assert state.improve(RID)["hint"] is True
    assert state.improve_dismiss(RID) == {"ok": True}
    assert state.improve(RID)["hint"] is False


def test_ready_proposal_without_places(state, tmp_path):
    _proposal(_folder(tmp_path))
    got = state.improve(RID)
    assert got["state"] == "ready" and got["hint"] is False
    groups = got["proposal"]["groups"]
    assert [(g["find"], g["count"]) for g in groups] == [("апи", 2), ("кафка", 1), ("медленно", 1)]
    assert all("occ" not in g for g in groups)


def test_apply_one_step_rules_and_terms_only_on_request(state, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "hotwords_path", lambda: tmp_path / "hotwords.txt")
    folder = _folder(tmp_path)
    doc = _proposal(folder)
    seen = _events(state.tray, tray_control.IMPROVE_UPDATED)
    terms = [g["id"] for g in doc["groups"] if g["kind"] == "term"]
    reply = state.improve_apply(RID, {"groups": terms})
    assert reply["changed"] == 3 and [g["from"] for g in reply["groups"]] == ["апи", "кафка"]
    assert reply["step"]["ops"][0]["scope"] == "ai"
    assert "rules" not in reply and "terms" not in reply
    assert settings.load().asr.replacements == ()
    assert not (tmp_path / "hotwords.txt").exists()
    texts = [s["text"] for s in library.read_transcript(folder)["segments"]]
    assert texts == ["API сервиса отвечает медленно.", "Значит, смотрим Kafka и API шлюза."]
    assert seen[-1] == {"id": RID, "state": "none"}
    assert state.improve(RID)["state"] == "none"
    # Отмена — обычным шагом истории.
    state.speakers_undo(RID, {"expect_step": reply["step"]["id"]})
    assert library.read_transcript(folder)["segments"][0]["text"] == "Апи сервиса отвечает медленно."

    doc = _proposal(folder)
    fix = next(g["id"] for g in doc["groups"] if g["kind"] == "fix")
    reply = state.improve_apply(RID, {"groups": [doc["groups"][0]["id"], fix], "add_rules": True,
                                      "add_terms": True, "created_at": doc["created_at"]})
    # Исправления обычных слов не становятся ни правилами, ни терминами.
    assert reply["rules"] == {"added": [{"from": "апи", "to": "API"}]}
    assert reply["terms"] == {"added": ["API"]}
    assert settings.load().asr.replacements == ({"from": "апи", "to": "API"},)
    assert "API" in (tmp_path / "hotwords.txt").read_text(encoding="utf-8")


def test_apply_marks_the_analysis_stale_and_takes_extra_places(state, app, tmp_path):
    from meet import analysis

    folder = _folder(tmp_path)
    data = library.read_transcript(folder)
    analysis.write(folder, {"version": 1, "model": "fake", "created_at": 1.0,
                            "fingerprint": analysis.fingerprint(data), "features": ["title"], "title": "x"})
    seen = _events(app, tray_control.ANALYSIS_UPDATED)
    library.write_transcript(folder, {"version": 1, "created_at": "2026-10-02T15:00:00", "segments": [
        {"start": 0.0, "end": 4.0, "speaker": "Спикер 1", "text": "Пишем в кафка."},
        {"start": 4.0, "end": 900.0, "speaker": "Спикер 2", "text": "Франц Кафка — писатель."}]})
    data = library.read_transcript_full(folder)
    analysis.write(folder, {"version": 1, "model": "fake", "created_at": 1.0,
                            "fingerprint": analysis.fingerprint(data), "features": ["title"], "title": "x"})
    doc = {"version": 1, "model": "fake", "created_at": 2.0, "fingerprint": improve.fingerprint(data), "segments": 2,
           "groups": improve.build_groups(data, [{"find": "кафка", "replace": "Kafka", "kind": "term",
                                                  "confidence": 0.9, "segments": [0]}])}
    improve.write(folder, doc)
    gid = doc["groups"][0]["id"]
    with pytest.raises(control.Conflict, match="обновился"):
        state.improve_apply(RID, {"groups": [gid], "created_at": 1.0})
    reply = state.improve_apply(RID, {"groups": [gid], "extra": {gid: [0]}, "created_at": 2.0})
    assert reply["changed"] == 2
    texts = [x["text"] for x in library.read_transcript(folder)["segments"]]
    assert texts == ["Пишем в Kafka.", "Франц Kafka — писатель."]
    assert {"id": RID, "state": "stale"} in seen


def test_apply_refused_while_the_recording_is_transcribed(state, tmp_path):
    folder = _folder(tmp_path)
    doc = _proposal(folder)
    state.queue.submit(jobs.TRANSCRIBE, str(folder), {})
    with pytest.raises(control.Conflict, match="расшифровка"):
        state.improve_apply(RID, {"groups": [doc["groups"][0]["id"]]})
    assert library.read_transcript(folder)["segments"][0]["text"].startswith("Апи")


def test_apply_stale_proposal_is_409(state, tmp_path):
    folder = _folder(tmp_path)
    doc = _proposal(folder)
    state.text_apply(RID, {"find": "сервиса", "replace": "сервера", "scope": "all"})
    with pytest.raises(control.Conflict):
        state.improve_apply(RID, {"groups": [doc["groups"][0]["id"]]})


def test_auto_improve_only_when_enabled(state, app, tmp_path):
    folder = _folder(tmp_path)
    app.bus.emit(jobs.JOB_DONE, job={"id": "t1", "kind": jobs.TRANSCRIBE, "folder": str(folder), "state": "done"})
    assert _improve_jobs(state) == []
    settings.patch({"analysis": {"improve_auto": True}})
    app.bus.emit(jobs.JOB_DONE, job={"id": "t2", "kind": jobs.TRANSCRIBE, "folder": str(folder), "state": "done"})
    assert len(_improve_jobs(state)) == 1
    assert library.read_meta(folder)["pending_improve"]["manual"] is False


def test_auto_improve_skips_short_meetings_and_clears_an_old_error(state, app, tmp_path):
    folder = _folder(tmp_path)
    settings.patch({"analysis": {"improve_auto": True}})
    improve.mark_failed(folder, "прошлая расшифровка")
    library.write_transcript(folder, {"version": 1, "segments": [{"start": 0, "end": 30, "text": "Коротко."}]})
    app.bus.emit(jobs.JOB_DONE, job={"id": "t1", "kind": jobs.TRANSCRIBE, "folder": str(folder), "state": "done"})
    assert _improve_jobs(state) == []  # короче auto_record.min_call_seconds — модель не зовём
    assert "improve_error" not in library.read_meta(folder)  # ошибка была о старом тексте
    assert "pending_improve" not in library.read_meta(folder)


def test_no_mark_without_a_model_even_during_a_recording(state, app, tmp_path, monkeypatch):
    folder = _folder(tmp_path)
    settings.patch({"analysis": {"improve_auto": True}})
    _installed(monkeypatch)
    monkeypatch.setattr(state, "_busy_now", lambda: {"x"})
    state._auto_improve(folder)
    assert "pending_improve" not in library.read_meta(folder)


def test_retranscription_discards_the_old_proposal(state, app, tmp_path):
    folder = _folder(tmp_path)
    _proposal(folder)
    library.write_transcript(folder, {"version": 1, "segments": [{"start": 0, "end": 1, "text": "Новый текст."}]})
    app.bus.emit(jobs.JOB_DONE, job={"id": "t1", "kind": jobs.TRANSCRIBE, "folder": str(folder), "state": "done"})
    assert not (folder / improve.IMPROVE_JSON).exists()


def test_pending_mark_survives_restart_and_cancel_clears_it(state, tmp_path, app):
    folder = _folder(tmp_path)
    job = state.make_improve(RID)
    state.llm_queue.stop()  # выход резидента: задача убита остановкой — отметка остаётся
    if state.llm_queue._thread is not None:
        state.llm_queue._thread.join(timeout=10)
    assert library.read_meta(folder)["pending_improve"]["manual"] is True
    # Перезапуск: новая очередь, отметка в meta.json осталась.
    release = threading.Event()
    fresh = tray_control.TrayControl(app, queue=jobs.JobQueue(app.bus, spawn=lambda j, f: 0),
                                     llm_queue=jobs.JobQueue(app.bus, spawn=lambda j, f: release.wait(5) and 0))
    fresh._background = lambda fn, name=None: fn()
    try:
        done = fresh.recover()
        assert done.get("improve") == [RID]
        again = [j for j in fresh.llm_queue.listing() if j["kind"] == jobs.IMPROVE]
        assert len(again) == 1 and again[0]["id"] != job["id"]
        # Отмена человеком — после следующего перезапуска не вернётся.
        fresh.cancel_job(again[0]["id"])
        assert "pending_improve" not in library.read_meta(folder)
    finally:
        release.set()
        fresh.queue.stop()
        fresh.llm_queue.stop()


def test_auto_mark_dropped_on_restart_when_setting_is_off(state, tmp_path):
    folder = _folder(tmp_path)
    library.write_meta(folder, {"pending_improve": {"at": 1e12, "manual": False}})
    assert "improve" not in state.recover(now=1e12)
    assert "pending_improve" not in library.read_meta(folder)


def test_delete_drops_the_improve_job(state, tmp_path):
    state.make_improve(RID)
    state.delete_recording(RID)
    assert not _folder(tmp_path).exists()
    assert all(j["state"] == "cancelled" for j in _improve_jobs(state))


def test_failed_job_marks_the_error_and_emits(state, app, tmp_path):
    folder = _folder(tmp_path)
    seen = _events(app, tray_control.IMPROVE_UPDATED)
    library.write_meta(folder, {"pending_improve": {"at": 1.0, "manual": True}})
    app.bus.emit(jobs.JOB_FAILED, job={"id": "i1", "kind": jobs.IMPROVE, "folder": str(folder),
                                       "state": "failed", "error": "процесс упал", "started_at": 5.0})
    got = state.improve(RID)
    assert got["state"] == "failed" and got["error"] == "процесс упал"
    assert seen == [{"id": RID, "state": "failed"}]
    assert "pending_improve" not in library.read_meta(folder)


def test_routes_exist():
    found = [(m, p.pattern) for m, p, _ in control._PATTERNS]
    assert ("GET", r"^/recordings/([^/]+)/improve$") in found
    assert ("POST", r"^/recordings/([^/]+)/improve$") in found
    assert ("POST", r"^/recordings/([^/]+)/improve/apply$") in found
    assert ("POST", r"^/recordings/([^/]+)/improve/dismiss$") in found


def test_apply_with_users_target_remembers_it_not_the_ais(state, tmp_path, monkeypatch):
    """ИИ предложил «API», человек вписал «REST API»: в тексте, в правилах для
    будущих встреч и в терминах — вписанное; отмена — обычным шагом истории."""
    monkeypatch.setattr(paths, "hotwords_path", lambda: tmp_path / "hotwords.txt")
    folder = _folder(tmp_path)
    doc = _proposal(folder)
    api = next(g["id"] for g in doc["groups"] if g["find"] == "апи")
    reply = state.improve_apply(RID, {"groups": [api], "targets": {api: "  REST API "}, "add_rules": True,
                                      "add_terms": True, "created_at": doc["created_at"]})
    assert reply["groups"] == [{"from": "апи", "to": "REST API", "kind": "term", "count": 2, "edited": True}]
    assert reply["rules"] == {"added": [{"from": "апи", "to": "REST API"}]}
    assert settings.load().asr.replacements == ({"from": "апи", "to": "REST API"},)
    assert reply["terms"] == {"added": ["REST API"]}
    assert "REST API" in (tmp_path / "hotwords.txt").read_text(encoding="utf-8")
    texts = [s["text"] for s in library.read_transcript(folder)["segments"]]
    assert texts == ["REST API сервиса отвечает медленно.", "Значит, смотрим кафка и REST API шлюза."]
    state.speakers_undo(RID, {"expect_step": reply["step"]["id"]})
    assert library.read_transcript(folder)["segments"][0]["text"] == "Апи сервиса отвечает медленно."


def test_apply_with_empty_users_target_is_400_and_changes_nothing(state, tmp_path):
    folder = _folder(tmp_path)
    doc = _proposal(folder)
    api = next(g["id"] for g in doc["groups"] if g["find"] == "апи")
    with pytest.raises(control.BadRequest, match="апи"):
        state.improve_apply(RID, {"groups": [api], "targets": {api: "  "}})
    assert library.read_transcript(folder)["segments"][0]["text"] == "Апи сервиса отвечает медленно."
    assert settings.load().asr.replacements == ()
    assert improve.read(folder) is not None
