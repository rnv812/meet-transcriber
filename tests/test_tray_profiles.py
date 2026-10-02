"""Профили людей в резиденте (meet.profiles_control): API, «выключено — задач
нет», одна задача на человека, автоматическое обновление (раз в сутки и
только при новых репликах), отметки для перезапуска, удаление, маршруты и
CLI. Модель не вызывается: spawn подменён, `detect.available` подменён.
Люди и реплики выдуманы."""

import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

import meet.llm as llm
from meet import control, jobs, library, profile_index, profiles, profiles_control, settings, tray, tray_control
from meet.llm import detect

DAY = profiles.AUTO_EVERY_S


def _write_config(tmp_path, **sections) -> None:
    path = tmp_path / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(tmp_path / "recordings"), "voices_dir": str(tmp_path / "voices")},
        "llm": {"provider": "auto"},
        "assistant": {"knowledge_dir": None, "notes_dir": None},
        "export": {"meetings_dir": None},
        "profiles": {"enabled": True},
    }
    for name, value in sections.items():
        data[name] = {**data.get(name, {}), **value}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _voice(tmp_path, name):
    vo = tmp_path / "voices"
    vo.mkdir(parents=True, exist_ok=True)
    (vo / f"{name}.json").write_text(json.dumps({"samples": [{"embedding": [0.1]}]}), encoding="utf-8")


def _meeting(tmp_path, rid, name="Вера", n=6, other="Тимур"):
    folder = tmp_path / "recordings" / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sys.opus").write_bytes(b"x")
    segments = []
    for k in range(n):
        segments.append({"start": k * 20.0, "end": k * 20 + 5.0, "speaker": other, "text": f"Какой у нас вопрос {k}?"})
        segments.append({"start": k * 20 + 6.0, "end": k * 20 + 15.0, "speaker": name,
                         "text": f"Сначала сверим сроки, потом решим {k}."})
    library.write_transcript(folder, {"version": 1, "segments": segments})
    return folder


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    _write_config(tmp_path)
    for name in ("Вера", "Тимур", "Вы"):
        _voice(tmp_path, name)
    for d in range(3):
        _meeting(tmp_path, f"2026-09-{10 + d:02d}_10-00")
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
    monkeypatch.setattr(profiles_control, "DROP_PROFILE_WAIT_S", 0.2)
    monkeypatch.setattr(profile_index, "_registry", {})
    st._profile_index().refresh()  # индекс реплик уже посчитан (первый проход — отдельный тест)
    try:
        yield st
    finally:
        release.set()
        queue.stop()
        llm_queue.stop()


def _profile_jobs(st, states=(jobs.QUEUED, jobs.RUNNING)):
    return [j for j in st.llm_queue.listing() if j["kind"] == jobs.PROFILE and j["state"] in states]


def _pid(tmp_path, name="Вера"):
    return profiles.person_id(name, tmp_path / "voices", create=True)


def _stored(tmp_path, name="Вера", *, age=2 * DAY, signature="старый"):
    pid = _pid(tmp_path, name)
    profiles.write(pid, {"version": 1, "person_id": pid, "updated_at": time.time() - age,
                         "signature": signature, "summary": "По делу.", "sections": {}})
    return pid


def _done(app, folder, kind=jobs.PROFILE, state="done", **extra):
    app.bus.emit(jobs.JOB_DONE if state == "done" else jobs.JOB_FAILED,
                 job={"id": "j1", "kind": kind, "folder": str(folder), "state": state, **extra})


# --- выключено ------------------------------------------------------------------


def test_disabled_means_no_jobs_and_no_tab(state, tmp_path):
    _write_config(tmp_path, profiles={"enabled": False})
    pid = _stored(tmp_path)
    assert state.profile("Вера") == {"enabled": False}
    with pytest.raises(control.Conflict, match="выключены"):
        state.make_profile("Вера")
    with pytest.raises(control.Conflict, match="выключены"):
        state.profile_notes("Вера", {"text": "x"})
    assert state._auto_profiles(tmp_path / "recordings" / "2026-09-12_10-00") == []
    assert _profile_jobs(state) == []
    assert profiles.read(pid) is not None  # сохранённое остаётся
    assert state.profiles_info() == {"enabled": False, "count": 1}


def test_turning_profiles_off_cancels_their_jobs(state, tmp_path):
    job = state.make_profile("Вера")
    state.patch_settings({"profiles": {"enabled": False}})
    assert _profile_jobs(state) == []
    assert state.llm_queue.get(job["id"]).state == jobs.CANCELLED
    pid = profiles.pid_of_path(job["folder"])
    assert "pending" not in profiles.read_state(pid)


# --- API --------------------------------------------------------------------------


def test_profile_state_none_then_one_job_with_a_pending_mark(state, tmp_path):
    got = state.profile("Вера")
    assert got["enabled"] is True and got["state"] == "none" and got["level"] == "full"
    assert got["stats"] == {"turns": 18, "meetings": 3} and got["profile"] is None
    assert got["latest_meeting"] == "2026-09-12_10-00" and got["self"] is False
    job = state.make_profile("Вера")
    assert job["kind"] == jobs.PROFILE and Path(job["folder"]).parent == profiles.profiles_dir()
    assert state.make_profile("Вера")["id"] == job["id"]
    assert len(_profile_jobs(state)) == 1
    pid = profiles.pid_of_path(job["folder"])
    assert profiles.read_state(pid)["pending"]["manual"] is True
    assert state.profile("Вера")["state"] in ("queued", "running")
    assert state.profile("Вы")["self"] is True
    assert state.profile("Никто") == {"error": "человека нет"}


def test_make_profile_refuses_without_data_or_model(state, tmp_path, monkeypatch):
    _voice(tmp_path, "Олег")
    _meeting(tmp_path, "2026-09-13_10-00", name="Олег", n=2)
    got = state.profile("Олег")
    assert got["level"] == "none" and got["note"].startswith("Недостаточно данных: 2 реплики в 1 встрече")
    with pytest.raises(control.Conflict, match="Недостаточно данных"):
        state.make_profile("Олег")
    _installed(monkeypatch)
    with pytest.raises(control.Conflict, match="Подключите"):
        state.make_profile("Вера")
    with pytest.raises(control.BadRequest):
        state.profile("../x")


def test_job_end_clears_the_mark_and_failures_show(state, app, tmp_path):
    job = state.make_profile("Вера")
    pid = profiles.pid_of_path(job["folder"])
    _done(app, job["folder"], state="failed", error="таймаут вызова модели", started_at=time.time() - 1)
    assert "pending" not in profiles.read_state(pid)
    state.llm_queue.cancel(job["id"])
    got = state.profile("Вера")
    assert got["state"] == "failed" and got["error"] == "таймаут вызова модели"
    # профиль новее ошибки — «готов»
    profiles.write(pid, {"version": 1, "updated_at": time.time() + 5, "signature": "x", "sections": {}})
    got = state.profile("Вера")
    assert got["state"] == "ready" and got["has_new"] is True


def test_shutdown_keeps_the_mark(state, app, tmp_path):
    job = state.make_profile("Вера")
    pid = profiles.pid_of_path(job["folder"])
    state._profile_finished(job["folder"], jobs.FAILED, job=job, stopping=True)
    assert profiles.read_state(pid)["pending"]


def test_cancel_from_the_jobs_list_clears_the_mark(state):
    job = state.make_profile("Вера")
    pid = profiles.pid_of_path(job["folder"])
    state.make_profile("Тимур")  # вторая ждёт за первой
    second = _profile_jobs(state)[1]
    assert state.cancel_job(second["id"]) == {"ok": True}
    assert "pending" not in profiles.read_state(profiles.pid_of_path(second["folder"]))
    assert profiles.read_state(pid)["pending"]


def test_notes_delete_and_delete_all(state, tmp_path):
    assert state.profile_notes("Вера", {"text": "Любит письменные итоги"}) == {"notes": "Любит письменные итоги"}
    assert state.profile("Вера")["notes"] == "Любит письменные итоги"
    with pytest.raises(control.BadRequest):
        state.profile_notes("Вера", {})
    pid = _stored(tmp_path)
    job = state.make_profile("Вера")
    assert state.delete_profile("Вера") == {"ok": True}
    assert state.llm_queue.get(job["id"]).state == jobs.CANCELLED
    assert profiles.read(pid) is None and profiles.read_notes(pid) == ""
    _stored(tmp_path, "Тимур")
    state.make_profile("Тимур")
    assert state.delete_profiles() == {"deleted": 1}
    assert _profile_jobs(state) == [] and profiles.count() == 0


def test_deleting_or_merging_a_person_drops_the_job_and_the_profile(state, tmp_path):
    pid = _stored(tmp_path)
    job = state.make_profile("Вера")
    assert state.person_action("Вера", "delete") == {"ok": True}
    assert state.llm_queue.get(job["id"]).state == jobs.CANCELLED
    assert profiles.read(pid) is None
    pid2 = _stored(tmp_path, "Тимур")
    _voice(tmp_path, "Олег")
    state.make_profile("Тимур")
    assert state.person_action("Тимур", "merge", {"into": "Олег"}) == {"ok": True}
    assert profiles.read(pid2) is None and _profile_jobs(state) == []


def test_rename_keeps_the_profile(state, tmp_path):
    pid = _stored(tmp_path)
    assert state.person_action("Вера", "rename", {"to": "Вера Никитина"}) == {"ok": True}
    got = state.profile("Вера Никитина")
    assert got["profile"]["person_id"] == pid and got["stats"]["turns"] == 18


# --- автоматика -----------------------------------------------------------------------


def test_auto_refresh_after_analysis_only_when_due(state, app, tmp_path):
    folder = tmp_path / "recordings" / "2026-09-12_10-00"
    pid = _stored(tmp_path)  # больше суток назад, отпечаток другой
    _stored(tmp_path, "Вы")  # «Вы» — только вручную
    _done(app, folder, kind=jobs.ANALYZE)
    queued = _profile_jobs(state)
    assert [profiles.pid_of_path(j["folder"]) for j in queued] == [pid]
    assert state.llm_queue._low == {queued[0]["id"]}  # фоном
    assert profiles.read_state(pid)["pending"]["manual"] is False
    # вторая попытка в те же сутки — нет (даже если первую сняли)
    state.llm_queue.cancel(queued[0]["id"])
    assert state._auto_profiles(folder) == []


def test_auto_refresh_skips_fresh_unchanged_missing_and_failed_analysis(state, app, tmp_path):
    folder = tmp_path / "recordings" / "2026-09-12_10-00"
    _stored(tmp_path, age=3600)  # обновлялся час назад
    assert state._auto_profiles(folder) == []
    sig = profiles.signature(profiles.collect("Вера", tmp_path / "recordings"))
    _stored(tmp_path, signature=sig)  # новых реплик нет
    assert state._auto_profiles(folder) == []
    assert state._auto_profiles(folder, now=time.time()) == []
    # у Тимура профиля нет — автоматически не составляется
    _stored(tmp_path, signature="другой")
    _done(app, folder, kind=jobs.ANALYZE, state="failed")
    assert _profile_jobs(state) == []
    assert state._auto_profiles(folder) == ["Вера"]


def test_auto_refresh_needs_a_model(state, tmp_path, monkeypatch):
    _stored(tmp_path)
    _installed(monkeypatch)
    assert state._auto_profiles(tmp_path / "recordings" / "2026-09-12_10-00") == []


# --- перезапуск ---------------------------------------------------------------------------


def test_recover_requeues_pending_profiles_once(state, tmp_path):
    vera, timur = _pid(tmp_path), _pid(tmp_path, "Тимур")
    profiles.mark_pending(vera, True, manual=True)
    profiles.mark_pending(timur, True, manual=False)
    gone = "00000000000000aa"
    profiles.mark_pending(gone, True)
    old = _pid(tmp_path, "Вы")
    profiles.update_state(old, lambda s: {"pending": {"at": 1.0, "manual": True}})
    done = state.recover()
    assert sorted(done["profiles"]) == sorted([vera, timur])
    queued = {profiles.pid_of_path(j["folder"]): j["id"] for j in _profile_jobs(state)}
    assert set(queued) == {vera, timur}
    assert queued[timur] in state.llm_queue._low and queued[vera] not in state.llm_queue._low
    assert "pending" not in profiles.read_state(gone) and "pending" not in profiles.read_state(old)
    # второй проход не удваивает
    state.recover()
    assert len(_profile_jobs(state)) == 2


def test_recover_drops_marks_when_disabled_and_keeps_them_without_a_model(state, tmp_path, monkeypatch):
    vera = _pid(tmp_path)
    profiles.mark_pending(vera, True, manual=True)
    _installed(monkeypatch)
    assert "profiles" not in state.recover()
    assert profiles.read_state(vera)["pending"]
    _write_config(tmp_path, profiles={"enabled": False})
    state.recover()
    assert "pending" not in profiles.read_state(vera)


# --- задача и маршруты ------------------------------------------------------------------


def test_worker_argv_and_job_worker(monkeypatch, tmp_path, app):
    from meet import job_worker
    from meet.llm.base import AgentReply

    job = jobs.Job(id="x", kind=jobs.PROFILE, folder=str(tmp_path / "p" / "0123456789abcdef.json"))
    assert jobs.worker_argv(job)[-2:] == ["profile", job.folder]
    pid = _pid(tmp_path)
    reply = json.dumps({"summary": "По делу.", "summary_refs": ["m1#1"], "sections": {
        "style": [{"text": "Начинает со сроков.", "refs": ["m1#1"]}], "values": [], "how_to_talk": [],
        "avoid": [], "topics": []}}, ensure_ascii=False)

    async def runner(prompt, **kwargs):
        return AgentReply(text=reply)

    monkeypatch.setattr(llm, "resolve", lambda cfg: ("fake", runner))
    lines = []
    monkeypatch.setattr(job_worker, "_emit", lines.append)
    assert job_worker.main(["profile", str(profiles.profile_path(pid))]) == 0
    assert lines[-1]["kind"] == "job.result"
    assert profiles.read(pid)["sections"]["style"][0]["refs"][0]["m"] == "2026-09-12_10-00"
    _write_config(tmp_path, profiles={"enabled": False})
    assert job_worker.main(["profile", str(profiles.profile_path(pid))]) == 3
    assert "выключены" in profiles.read_state(pid)["error"]["error"]


class _FakeState:
    def __init__(self):
        self.bus = __import__("meet.events", fromlist=["EventBus"]).EventBus()
        self.calls = []

    def snapshot(self):
        return {"status": "idle"}

    def __getattr__(self, name):
        if name in ("profile", "make_profile", "delete_profile", "profile_notes", "profiles_info",
                    "delete_profiles"):
            def call(*args):
                self.calls.append((name, *args))
                return {"ok": name}
            return call
        raise AttributeError(name)


def test_control_routes(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    fake = _FakeState()
    srv = control.ControlServer(fake)
    srv.start(pid=4243)
    try:
        def call(path, method="GET", payload=None):
            body = json.dumps(payload).encode("utf-8") if payload is not None else None
            req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", data=body, method=method)
            req.add_header("Authorization", f"Bearer {srv.token}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))

        who = "/voices/%D0%92%D0%B5%D1%80%D0%B0"
        assert call(f"{who}/profile") == {"ok": "profile"}
        assert call(f"{who}/profile", "POST", {}) == {"ok": "make_profile"}
        assert call(f"{who}/profile", "DELETE") == {"ok": "delete_profile"}
        assert call(f"{who}/profile/notes", "PUT", {"text": "x"}) == {"ok": "profile_notes"}
        assert call("/profiles") == {"ok": "profiles_info"}
        assert call("/profiles", "DELETE") == {"ok": "delete_profiles"}
        assert ("profile", "Вера") in fake.calls and ("profile_notes", "Вера", {"text": "x"}) in fake.calls
    finally:
        srv.stop(pid=4243)


# --- CLI ------------------------------------------------------------------------------------


def _cli(argv):
    from meet import cli

    return cli.main(argv)


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    rec, vo = tmp_path / "recordings", tmp_path / "voices"
    (data / "config.json").write_text(json.dumps({
        "version": 4, "recording": {"out_dir": str(rec), "voices_dir": str(vo)},
        "llm": {"provider": "auto"}, "profiles": {"enabled": True}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    _voice(tmp_path, "Вера")
    for d in range(3):
        _meeting(tmp_path, f"2026-09-{10 + d:02d}_10-00")
    return {"data": data, "tmp": tmp_path}


def test_cli_profile_refresh_inline_then_show(cli_env, capsys, monkeypatch):
    from meet.llm.base import AgentReply

    reply = json.dumps({"summary": "По делу.", "summary_refs": ["m1#1"], "sections": {
        "style": [{"text": "Начинает со сроков.", "refs": ["m1#1"]}], "values": [], "how_to_talk": [],
        "avoid": [], "topics": []}}, ensure_ascii=False)

    async def runner(prompt, **kwargs):
        return AgentReply(text=reply)

    monkeypatch.setattr(llm, "resolve", lambda cfg: ("fake", runner))
    assert _cli(["profile", "Вера"]) == 0
    assert "пока нет" in capsys.readouterr().out
    assert _cli(["profile", "Вера", "--refresh", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["via_app"] is False and got["profile"]["summary"] == "По делу."
    assert _cli(["profile", "Вера"]) == 0
    assert "Начинает со сроков." in capsys.readouterr().out
    assert _cli(["profile", "Никто"]) == 1
    assert "нет «Никто»" in capsys.readouterr().err


def test_cli_profile_disabled_and_via_the_app(cli_env, capsys, monkeypatch):
    calls, states = [], [{"state": "queued"}, {"state": "ready", "profile": {"summary": "По делу.",
                                                                              "meetings": 3, "turns": 18,
                                                                              "updated_at": 0, "sections": {}},
                                               "notes": ""}]

    def fake_request(path, method="GET", payload=None, timeout=5.0):
        calls.append((method, path))
        return {"id": "p1", "kind": "profile"} if method == "POST" else states.pop(0)

    monkeypatch.setattr(control, "alive", lambda *a, **k: True)
    monkeypatch.setattr(control, "request", fake_request)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert _cli(["profile", "Вера", "--refresh", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["via_app"] is True
    assert calls[0] == ("POST", "/voices/%D0%92%D0%B5%D1%80%D0%B0/profile")
    path = cli_env["data"] / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["profiles"]["enabled"] = False
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert _cli(["profile", "Вера"]) == 1
    assert "выключены" in capsys.readouterr().err


def test_settings_profiles_default_off(tmp_path):
    cfg = settings.load(tmp_path / "config.json")
    assert cfg.profiles.enabled is False
    assert cfg.to_raw()["profiles"] == {"enabled": False, "pcm": True}
    assert settings.patch({"profiles": {"enabled": True}}, tmp_path / "config.json").profiles.enabled is True
    got = settings.patch({"profiles": {"pcm": False}}, tmp_path / "config.json").profiles
    assert got.enabled is True and got.pcm is False


def test_pcm_flag_hides_the_section_and_explains_missing_data(state, tmp_path):
    pid = _pid(tmp_path)
    profiles.write(pid, {"version": 1, "person_id": pid, "updated_at": time.time(), "signature": "x",
                         "sections": {}, "pcm": {"base": {"type": "thinker", "confidence": 0.6,
                                                          "refs": [{"m": "2026-09-12_10-00", "i": 1, "t": 6.0}]}}})
    got = state.profile("Вера")
    assert got["pcm_enabled"] is True and got["profile"]["pcm"]["base"]["type"] == "thinker"
    assert "pcm_note" not in got  # 18 реплик в 3 встречах — достаточно
    _write_config(tmp_path, profiles={"enabled": True, "pcm": False})
    got = state.profile("Вера")
    assert got["pcm_enabled"] is False and "pcm" not in got["profile"]
    _voice(tmp_path, "Олег")
    _meeting(tmp_path, "2026-09-13_10-00", name="Олег", n=8)
    assert state.profile("Олег")["pcm_note"] == \
        "Недостаточно данных: 8 реплик в 1 встрече — нужно от 15 реплик в 3 встречах"


# --- fix round 1 -------------------------------------------------------------------------


def test_cold_index_is_built_in_the_background(state, tmp_path, monkeypatch):
    monkeypatch.setattr(profile_index, "_registry", {})
    started = []
    monkeypatch.setattr(profile_index, "warm_in_background", lambda ix, on_done=None: started.append(ix) or True)
    got = state.profile("Вера")
    assert got["indexing"] is True and got["stats"] is None and started
    # задачу можно поставить и до подсчёта: мало ли реплик — проверит сама задача
    assert state.make_profile("Вера")["kind"] == jobs.PROFILE
    started[0].refresh()
    assert state.profile("Вера")["stats"] == {"turns": 18, "meetings": 3}


def test_get_resolves_refs_and_flags_changed_turns(state, tmp_path):
    from meet import profile_index as pix

    pid = _pid(tmp_path)
    rid = "2026-09-12_10-00"
    seg = library.read_transcript(tmp_path / "recordings" / rid)["segments"][1]
    good = {"m": rid, "i": 1, "t": seg["start"], "h": pix.text_hash(seg["text"]), "q": seg["text"]}
    stale = {**good, "i": 3, "t": 300.0, "h": "0000000000"}
    gone = {**good, "m": "2026-01-01_10-00"}
    profiles.write(pid, {"version": 1, "person_id": pid, "updated_at": time.time(), "signature": "x",
                         "summary": "По делу.", "sections": {
                             "style": [{"text": "Коротко.", "refs": [good, stale]}],
                             "values": [{"text": "Из удалённой встречи.", "refs": [gone]}]},
                         "sources": {rid: {"title": "a"}, "2026-01-01_10-00": {"title": "b"}}})
    got = state.profile("Вера")["profile"]
    refs = got["sections"]["style"][0]["refs"]
    assert "stale" not in refs[0] and refs[1]["stale"] is True
    assert got["sections"]["values"] == [] and list(got["sources"]) == [rid]


def test_hide_endpoint(state, tmp_path):
    pid = _pid(tmp_path)
    profiles.write(pid, {"version": 1, "person_id": pid, "updated_at": time.time(), "signature": "x",
                         "summary": "По делу.", "sections": {"style": [{"text": "Коротко.", "refs": []}]}})
    assert state.hide_statement("Вера", {"text": "По делу.", "hidden": True}) == {"hidden": 1}
    got = state.profile("Вера")
    assert got["profile"]["summary"] == "" and got["hidden"] == 1
    assert state.hide_statement("Вера", {"all": True, "hidden": False}) == {"hidden": 0}
    with pytest.raises(control.BadRequest):
        state.hide_statement("Вера", {"hidden": True})
    _write_config(tmp_path, profiles={"enabled": False})
    with pytest.raises(control.Conflict):
        state.hide_statement("Вера", {"text": "x"})


def test_broken_voice_file_is_a_clear_409(state, tmp_path):
    (tmp_path / "voices" / "Тимур.json").write_text("{битый", encoding="utf-8")
    with pytest.raises(control.Conflict, match="не читается"):
        state.profile_notes("Тимур", {"text": "x"})


# --- fix round 2 -------------------------------------------------------------------------


def test_disabling_profiles_removes_the_index_and_reenabling_rebuilds_it(state, tmp_path, monkeypatch):
    store = profiles.profiles_dir() / profile_index.DIR_NAME
    assert any(store.glob("*.json"))  # индекс посчитан фикстурой
    state.patch_settings({"profiles": {"enabled": False}})
    assert not store.exists() and profile_index._registry == {}
    started = []
    monkeypatch.setattr(profile_index, "warm_in_background", lambda ix, on_done=None: started.append(ix) or True)
    state.patch_settings({"profiles": {"enabled": True}})
    assert state.profile("Вера")["indexing"] is True and started  # заново и в фоне
    started[0].refresh()
    assert any(store.glob("*.json"))
    assert state.delete_profiles() == {"deleted": 0}
    assert not store.exists()


def test_get_reports_a_kept_previous_profile(state, tmp_path):
    pid = _pid(tmp_path)
    profiles.write(pid, {"version": 1, "person_id": pid, "updated_at": time.time() - 60, "signature": "x",
                         "review": {"checked": True, "blocked": 0}, "sections": {}})
    profiles.update_state(pid, lambda s: {**s, "unchecked": {"error": "таймаут", "at": time.time()}})
    assert state.profile("Вера")["kept_previous"] == "таймаут"


def test_startup_sweeps_the_index_when_profiles_are_off(state, tmp_path):
    store = profiles.profiles_dir() / profile_index.DIR_NAME
    assert any(store.glob("*.json"))
    _write_config(tmp_path, profiles={"enabled": False})  # выключили в файле, пока резидент не работал
    state.warm_profiles_index()
    assert not store.exists()
    assert state._profile_index().cancelled  # и до включения не воскресает
