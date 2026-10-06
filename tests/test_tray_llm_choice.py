"""Выбор модели у действий карточки в резиденте (U3): параметр `provider`
маршрутов проверяется по включённым моделям, уходит в задачу только этой
задачи, автоматическая работа идёт моделью по умолчанию, ничто не переходит
молча на другую модель. Модель не вызывается: spawn и `detect.available`
подменены. Данные выдуманы."""

import json
import threading
import time
from pathlib import Path

import pytest

import meet.llm as llm
from meet import analysis, control, improve, jobs, library, settings, tray, tray_control
from meet.llm import detect

RID = "2026-10-06_10-00"
LONG = 900.0


def _write_config(tmp_path, **sections) -> None:
    path = tmp_path / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(tmp_path / "recordings"),
                      "voices_dir": str(tmp_path / "voices")},
        # Внутренняя модель по умолчанию, Claude Code — для встреч, которые можно отдать наружу.
        "llm": {"provider": "openai-compatible", "local_model": "qwen3",
                "enabled": ["claude-code", "openai-compatible"]},
        "assistant": {"knowledge_dir": None, "notes_dir": None},
        "export": {"meetings_dir": None},
        "analysis": {"auto": True},
    }
    for name, value in sections.items():
        data[name] = {**data.get(name, {}), **value}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _folder(tmp_path) -> Path:
    return tmp_path / "recordings" / RID


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    _write_config(tmp_path)
    folder = _folder(tmp_path)
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 5.0, "speaker": "Ольга", "text": "Начнём с беты."},
        {"start": 5.0, "end": LONG, "speaker": "SPEAKER_01", "text": "Решили выпустить бету в пятницу."}]})
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
    _installed(monkeypatch, claude_code=True, openai_compatible=True)
    try:
        yield st
    finally:
        release.set()
        queue.stop()
        llm_queue.stop()


def _llm_jobs(st, kind):
    return [j for j in st.llm_queue.listing() if j["kind"] == kind]


# --- маршруты и проверка выбора ------------------------------------------------------


def test_routes_pass_the_body():
    """POST итогов, анализа, улучшения и названия принимают тело {"provider"}."""
    calls = []

    class State:
        def __getattr__(self, name):
            return lambda *a: calls.append((name, a)) or {}

    handler = type("H", (), {"_body": lambda self: {"provider": "codex"}})()
    server = type("S", (), {"state": State()})()
    for method, pattern, fn in control._PATTERNS:
        if method != "POST":
            continue
        for path in ("summary", "analysis", "improve", "title/suggest", "ask"):
            if pattern.pattern == rf"^/recordings/([^/]+)/{path}$":
                import meet.control as c

                orig = c._server_of
                c._server_of = lambda h: server
                try:
                    fn(handler, {}, RID)
                finally:
                    c._server_of = orig
    names = {name: args for name, args in calls}
    for name in ("make_summary", "make_analysis", "make_improve", "suggest_title", "ask"):
        assert names[name] == (RID, {"provider": "codex"}), name


@pytest.mark.parametrize("action", ["make_summary", "make_analysis", "make_improve"])
def test_chosen_provider_goes_into_this_job_only(state, action):
    job = getattr(state, action)(RID, {"provider": "claude-code"})
    assert job["provider"] == "claude-code"
    queued = state.llm_queue.get(job["id"])
    assert queued.options == {"provider": "claude-code"}
    assert "--provider=claude-code" in jobs.worker_argv(queued)


@pytest.mark.parametrize("action", ["make_summary", "make_analysis", "make_improve"])
def test_without_choice_the_job_uses_the_default(state, action):
    job = getattr(state, action)(RID)
    assert "provider" not in job
    assert not any(a.startswith("--provider") for a in jobs.worker_argv(state.llm_queue.get(job["id"])))


@pytest.mark.parametrize("action", ["make_summary", "make_analysis", "make_improve", "suggest_title"])
def test_choice_must_be_known_and_enabled(state, action):
    with pytest.raises(control.BadRequest, match="не включена"):
        getattr(state, action)(RID, {"provider": "codex"})
    with pytest.raises(control.BadRequest, match="неизвестная модель"):
        getattr(state, action)(RID, {"provider": "gpt"})
    with pytest.raises(control.BadRequest):
        getattr(state, action)(RID, {"provider": 3})
    assert state.llm_queue.listing() == []


@pytest.mark.parametrize("action", ["make_summary", "make_analysis", "make_improve", "suggest_title"])
def test_unavailable_choice_is_a_conflict_not_the_default(state, monkeypatch, action):
    """Claude Code не найден — 409 с причиной; модель по умолчанию (найдена) не подставляется."""
    _installed(monkeypatch, openai_compatible=True)
    monkeypatch.setattr(tray_control, "_suggest_title", lambda *a: pytest.fail("без вызова модели"))
    with pytest.raises(control.Conflict, match="Claude Code"):
        getattr(state, action)(RID, {"provider": "claude-code"})
    assert state.llm_queue.listing() == []


def test_ask_takes_the_choice(state):
    job = state.ask(RID, {"question": "Что решили?", "provider": "claude-code"})
    assert state.llm_queue.get(job["id"]).options == {"question": "Что решили?", "provider": "claude-code"}


def test_summary_queued_by_another_model_is_a_conflict(state):
    first = state.make_summary(RID)
    assert state.make_summary(RID)["id"] == first["id"]
    with pytest.raises(control.Conflict, match="другой моделью"):
        state.make_summary(RID, {"provider": "claude-code"})


def test_manual_choice_replaces_a_queued_background_analysis(state, app, tmp_path):
    """Фоновый анализ моделью по умолчанию ещё ждёт — просьба человека
    «Claude Code» ставит его задачу вместо фоновой."""
    state.llm_queue.submit(jobs.SUMMARY, str(tmp_path / "занято"))  # первая задача идёт
    background, created = state._queue_analysis(_folder(tmp_path), low=True)
    assert created
    mine = state.make_analysis(RID, {"provider": "claude-code"})
    assert mine["id"] != background.id
    alive = [j for j in _llm_jobs(state, jobs.ANALYZE) if j["state"] in ("queued", "running")]
    assert [j["id"] for j in alive] == [mine["id"]]
    assert library.read_meta(_folder(tmp_path))["pending_analysis"]["provider"] == "claude-code"


def test_running_analysis_by_another_model_is_a_conflict(state, tmp_path):
    job = state.make_analysis(RID)
    state.llm_queue.get(job["id"]).state = jobs.RUNNING
    with pytest.raises(control.Conflict, match="другой моделью"):
        state.make_analysis(RID, {"provider": "claude-code"})


def test_rerun_after_a_running_chosen_analysis_keeps_its_model(state, tmp_path):
    folder = _folder(tmp_path)
    job = state.make_analysis(RID, {"provider": "claude-code"})
    state.llm_queue.get(job["id"]).state = jobs.RUNNING
    state.make_analysis(RID, {"provider": "claude-code"})  # пока идёт — «повторить после»
    state.llm_queue.get(job["id"]).state = jobs.DONE
    state._analysis_finished(folder, jobs.DONE)
    again = [j for j in _llm_jobs(state, jobs.ANALYZE) if j["state"] == "queued"]
    assert len(again) == 1 and again[0]["provider"] == "claude-code"


def test_automatic_analysis_uses_the_default(state, app, tmp_path):
    app.bus.emit(jobs.JOB_DONE, job={"id": "t", "kind": jobs.TRANSCRIBE, "folder": str(_folder(tmp_path)),
                                     "state": "done"})
    got = _llm_jobs(state, jobs.ANALYZE)
    assert len(got) == 1 and "provider" not in got[0]


def test_recover_keeps_the_chosen_model(app, tmp_path, monkeypatch):
    _installed(monkeypatch, claude_code=True, openai_compatible=True)
    library.write_meta(_folder(tmp_path), {
        "pending_analysis": {"at": time.time(), "manual": True, "provider": "claude-code"},
        "pending_improve": {"at": time.time(), "manual": True, "provider": "claude-code"}})
    release = threading.Event()
    st = tray_control.TrayControl(app, queue=jobs.JobQueue(app.bus, spawn=lambda j, o: 0),
                                  llm_queue=jobs.JobQueue(app.bus, spawn=lambda j, o: release.wait(5) and 0))
    st._background = lambda fn, name=None: fn()
    try:
        st.recover()
        listed = st.llm_queue.listing()
        assert sorted(j["kind"] for j in listed) == [jobs.ANALYZE, jobs.IMPROVE]
        assert all(j["provider"] == "claude-code" for j in listed)
    finally:
        release.set()
        st.queue.stop()
        st.llm_queue.stop()


def test_recover_drops_a_choice_that_was_disabled_since(app, tmp_path, monkeypatch):
    _installed(monkeypatch, claude_code=True, openai_compatible=True)
    _write_config(tmp_path, llm={"enabled": ["openai-compatible"]})
    library.write_meta(_folder(tmp_path), {
        "pending_analysis": {"at": time.time(), "manual": True, "provider": "claude-code"}})
    st = tray_control.TrayControl(app, queue=jobs.JobQueue(app.bus, spawn=lambda j, o: 0),
                                  llm_queue=jobs.JobQueue(app.bus, spawn=lambda j, o: 0))
    st._background = lambda fn, name=None: fn()
    try:
        st.recover()
        assert st.llm_queue.listing() == []
        assert "pending_analysis" not in library.read_meta(_folder(tmp_path))
    finally:
        st.queue.stop()
        st.llm_queue.stop()


# --- «Предложить название» ---------------------------------------------------------


def _fresh_analysis(folder, origin=None):
    data = library.read_transcript(folder)
    doc = {"version": 1, "model": "x", "created_at": 1.0, "fingerprint": analysis.fingerprint(data),
           "features": ["title"], "title": "Бета в пятницу"}
    if origin:
        doc["llm"] = origin
    analysis.write(folder, doc)


def test_suggest_title_with_choice_calls_that_model(state, tmp_path, monkeypatch):
    _fresh_analysis(_folder(tmp_path), {"provider": "openai-compatible", "model": "qwen3"})
    seen = []
    monkeypatch.setattr(tray_control, "_suggest_title", lambda folder, *rest: seen.append(rest) or {
        "title": "Запуск беты", "from": "model", "llm": {"provider": "claude-code", "model": "sonnet"}})
    got = state.suggest_title(RID, {"provider": "claude-code"})
    assert got["title"] == "Запуск беты" and seen == [("claude-code",)]
    # Без выбора — название из свежего анализа, с его моделью.
    assert state.suggest_title(RID) == {"title": "Бета в пятницу", "from": "analysis",
                                        "llm": {"provider": "openai-compatible", "model": "qwen3"}}


def test_suggest_title_subprocess_gets_the_provider(monkeypatch, tmp_path):
    seen = {}

    class Done:
        stdout = '{"title": "x", "from": "model"}\n'
        stderr = ""

    def run(argv, **kw):
        seen["argv"] = argv
        return Done()

    monkeypatch.setattr(tray_control.subprocess, "run", run)
    tray_control._suggest_title(tmp_path, "claude-code")
    assert seen["argv"][-2:] == [str(tmp_path), "--provider=claude-code"]
    tray_control._suggest_title(tmp_path)
    assert seen["argv"][-1] == str(tmp_path)


def test_accepted_suggestion_keeps_its_model(state, tmp_path):
    origin = {"provider": "claude-code", "model": "sonnet"}
    got = state.update_recording(RID, {"title": "Запуск беты", "title_source": "ai", "title_llm": origin})
    assert got["title_llm"] == origin
    assert library.read_meta(_folder(tmp_path))["title_llm"] == origin
    # Мусор вместо модели не сохраняется; переименование человеком подпись снимает.
    got = state.update_recording(RID, {"title": "Ещё", "title_source": "ai", "title_llm": {"provider": "gpt"}})
    assert got["title_llm"] is None
    state.update_recording(RID, {"title": "Запуск беты", "title_source": "ai", "title_llm": origin})
    got = state.update_recording(RID, {"title": "Моё"})
    assert got["title_llm"] is None and "title_llm" not in library.read_meta(_folder(tmp_path))


def test_analysis_title_carries_the_analysis_model(state, tmp_path):
    _write_config(tmp_path, assistant={"auto_title": True})
    folder = _folder(tmp_path)
    _fresh_analysis(folder, {"provider": "claude-code", "model": "opus"})
    state._analysis_finished(folder, jobs.DONE)
    assert library.read_meta(folder)["title_llm"] == {"provider": "claude-code", "model": "opus"}


def test_summary_title_carries_the_summary_model(state, app, tmp_path):
    _write_config(tmp_path, assistant={"auto_title": True})
    folder = _folder(tmp_path)
    origin = {"provider": "openai-compatible", "model": "qwen3"}
    library.write_meta(folder, {"summary_title": {"title": "Итоги беты", "at": 1.0, "llm": origin}})
    state._summary_title(folder)
    assert library.read_meta(folder)["title_llm"] == origin


# --- сведения для окна -----------------------------------------------------------------


def test_assistant_lists_enabled_models_with_privacy(state, monkeypatch):
    monkeypatch.setattr(state._providers, "get", lambda cfg: ("openai-compatible", False))
    got = state.assistant()
    assert got["setting"] == "openai-compatible"
    assert got["enabled"] == ["claude-code", "openai-compatible"]
    models = {m["provider"]: m for m in got["models"]}
    assert set(models) == {"claude-code", "openai-compatible"}
    assert models["openai-compatible"]["local"] is True and models["openai-compatible"]["default"] is True
    assert models["openai-compatible"]["label"] == "Локальная модель (qwen3)"
    assert models["claude-code"]["local"] is False and models["claude-code"]["available"] is True


def test_provider_cache_key_follows_the_enabled_list(tmp_path):
    a = settings.Settings.from_raw({"llm": {"provider": "auto", "enabled": ["codex"]}})
    b = settings.Settings.from_raw({"llm": {"provider": "auto", "enabled": ["claude-code"]}})
    assert tray_control.ProviderCache._key_of(a) != tray_control.ProviderCache._key_of(b)


def test_provider_installed_follows_auto_and_choice(monkeypatch):
    cfg = settings.Settings.from_raw({"llm": {"provider": "auto", "enabled": ["openai-compatible"]}})
    _installed(monkeypatch, claude_code=True)
    assert tray_control._provider_installed(cfg) is False  # «Авто» — только из включённых
    assert tray_control._provider_installed(cfg, "claude-code") is True
    _installed(monkeypatch, openai_compatible=True)
    assert tray_control._provider_installed(cfg) is True
    assert tray_control._provider_installed(cfg, "claude-code") is False


def test_improve_proposal_is_public_with_its_model(state, tmp_path):
    folder = _folder(tmp_path)
    data = library.read_transcript_full(folder)
    improve.write(folder, {"version": improve.VERSION, "model": "claude-code:sonnet",
                           "llm": {"provider": "claude-code", "model": "sonnet"}, "created_at": 1.0,
                           "fingerprint": improve.fingerprint(data), "segments": 2, "groups": []})
    got = state.improve(RID)
    assert got["state"] == "ready"
    assert got["proposal"]["llm"] == {"provider": "claude-code", "model": "sonnet"}


# --- fix round 1 -----------------------------------------------------------------------


def test_explicit_default_choice_is_pinned_on_the_job(state, tmp_path):
    """Явный выбор модели по умолчанию закрепляется за задачей (N1): сменят
    модель по умолчанию, пока задача ждёт, — встреча всё равно уйдёт выбранной."""
    state.llm_queue.submit(jobs.SUMMARY, str(tmp_path / "занято"))  # наша — ждёт
    job = state.make_analysis(RID, {"provider": "openai-compatible"})
    assert job["provider"] == "openai-compatible"
    assert "--provider=openai-compatible" in jobs.worker_argv(state.llm_queue.get(job["id"]))


def test_queued_default_job_is_pinned_by_an_explicit_pick(state, tmp_path):
    state.llm_queue.submit(jobs.SUMMARY, str(tmp_path / "занято"))
    background = state.make_summary(RID)
    pinned = state.make_summary(RID, {"provider": "openai-compatible"})
    assert pinned["id"] != background["id"] and pinned["provider"] == "openai-compatible"
    assert state.llm_queue.get(background["id"]).state == jobs.CANCELLED


def test_running_default_job_is_the_same_model_not_a_conflict(state):
    """Идёт задача по умолчанию — выбор той же модели из списка не «другая модель» (M4)."""
    first = state.make_summary(RID)
    state.llm_queue.get(first["id"]).state = jobs.RUNNING
    assert state.make_summary(RID, {"provider": "openai-compatible"})["id"] == first["id"]
    job = state.make_analysis(RID)
    state.llm_queue.get(job["id"]).state = jobs.RUNNING
    assert state.make_analysis(RID, {"provider": "openai-compatible"})["id"] == job["id"]
    with pytest.raises(control.Conflict, match="другой моделью"):
        state.make_analysis(RID, {"provider": "claude-code"})


def test_manual_choice_replaces_a_queued_background_improvement(state, tmp_path):
    state.llm_queue.submit(jobs.SUMMARY, str(tmp_path / "занято"))
    background, created = state._queue_improve(_folder(tmp_path), low=True)
    assert created
    mine = state.make_improve(RID, {"provider": "claude-code"})
    alive = [j for j in _llm_jobs(state, jobs.IMPROVE) if j["state"] in ("queued", "running")]
    assert [j["id"] for j in alive] == [mine["id"]] and mine["provider"] == "claude-code"
    assert state.llm_queue.get(background.id).state == jobs.CANCELLED


def test_started_job_is_not_killed_by_a_replacement_race(state, monkeypatch):
    """Задача успела начаться между проверкой и снятием — 409, а не убийство (M3)."""
    job = state.make_analysis(RID)
    monkeypatch.setattr(state.llm_queue, "cancel_queued", lambda job_id: False)
    with pytest.raises(control.Conflict, match="другой моделью"):
        state.make_analysis(RID, {"provider": "claude-code"})
    assert state.llm_queue.get(job["id"]).state in (jobs.QUEUED, jobs.RUNNING)


def test_deferral_keeps_the_chosen_model_against_an_automatic_request(state, tmp_path, monkeypatch):
    """Отложенный до конца записи выбор человека не стирается фоновой просьбой (M2)."""
    folder = _folder(tmp_path)
    monkeypatch.setattr(state, "_busy_now", lambda: {state._key(folder)})
    state._defer_analysis(folder, True, "claude-code")
    state._defer_analysis(folder, False)
    assert library.read_meta(folder)["pending_analysis"]["provider"] == "claude-code"
    with state._analysis_lock:
        state._improve_deferred[state._key(folder)] = (folder, "claude-code")
    _write_config(tmp_path, analysis={"auto": True, "improve_auto": True})
    state._auto_improve(folder)
    assert state._improve_deferred[state._key(folder)] == (folder, "claude-code")
    monkeypatch.setattr(state, "_busy_now", lambda: set())
    state._flush_deferred_analysis()
    queued = {j["kind"]: j.get("provider") for j in state.llm_queue.listing()}
    assert queued == {jobs.ANALYZE: "claude-code", jobs.IMPROVE: "claude-code"}


def test_recover_drops_a_disabled_choice_for_improvement_too(app, tmp_path, monkeypatch):
    _installed(monkeypatch, claude_code=True, openai_compatible=True)
    _write_config(tmp_path, llm={"enabled": ["openai-compatible"]})
    library.write_meta(_folder(tmp_path), {
        "pending_improve": {"at": time.time(), "manual": True, "provider": "claude-code"}})
    st = tray_control.TrayControl(app, queue=jobs.JobQueue(app.bus, spawn=lambda j, o: 0),
                                  llm_queue=jobs.JobQueue(app.bus, spawn=lambda j, o: 0))
    try:
        st.recover()
        assert st.llm_queue.listing() == []
        assert "pending_improve" not in library.read_meta(_folder(tmp_path))
    finally:
        st.queue.stop()
        st.llm_queue.stop()


def test_failed_state_carries_the_model_for_retry(state, tmp_path):
    folder = _folder(tmp_path)
    analysis.mark_failed(folder, "таймаут", "claude-code")
    improve.mark_failed(folder, "таймаут", "claude-code")
    assert state.analysis(RID)["provider"] == "claude-code"
    assert state.improve(RID)["provider"] == "claude-code"


def test_dead_worker_failure_keeps_the_jobs_model(state, tmp_path):
    folder = _folder(tmp_path)
    state._analysis_finished(folder, jobs.FAILED, job={"error": "убит", "started_at": time.time() + 10,
                                                       "provider": "claude-code"})
    assert library.read_meta(folder)["analysis_error"]["provider"] == "claude-code"


def test_default_unavailable_but_chosen_model_still_works(state, monkeypatch):
    """Модель по умолчанию недоступна — выбранная доступная всё равно работает (I4)."""
    _installed(monkeypatch, claude_code=True)
    with pytest.raises(control.Conflict):
        state.make_analysis(RID)
    assert state.make_analysis(RID, {"provider": "claude-code"})["provider"] == "claude-code"
