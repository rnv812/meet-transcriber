"""Резидент: незаконченная работа после перезапуска (обрезка, задачи, объединение),
«обрабатывается» во время обрезки, занятая папка при удалении и объединении,
события об изменении записи. Данные выдуманные."""

import json
import time
from pathlib import Path

import pytest

from meet import control, jobs, library, merge, tail, tray, tray_control

A, B = "2026-09-30_10-00", "2026-09-30_10-40"
START = 1_790_000_000.0


def _write_config(tmp_path, recording=None, export=None) -> None:
    path = tmp_path / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(tmp_path / "recordings"),
                      "voices_dir": str(tmp_path / "voices"), **(recording or {})},
        "export": {"meetings_dir": None, **(export or {})},
    }, ensure_ascii=False), encoding="utf-8")


class FakeQueue:
    def __init__(self):
        self.submitted: list[jobs.Job] = []
        self.stopping = False
        self.cancelled: list[str] = []

    def submit(self, kind, folder, options=None):
        job = jobs.Job(id=f"j{len(self.submitted)}", kind=kind, folder=str(folder))
        self.submitted.append(job)
        return job

    def get(self, job_id):
        return next((j for j in self.submitted if j.id == job_id), None)

    def active_for(self, folder, kinds):
        return None

    def listing(self):
        return []

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        return self.get(job_id) is not None

    def stop(self):
        pass


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _write_config(tmp_path)
    root = tmp_path / "recordings"
    for name in (A, B):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "sys.opus").write_bytes(b"x")
        (folder / "mic.opus").write_bytes(b"x")
    return root


@pytest.fixture
def app(root):
    return tray.TrayApp()


@pytest.fixture
def queue():
    return FakeQueue()


@pytest.fixture
def state(app, queue):
    st = tray_control.TrayControl(app, queue=queue, llm_queue=FakeQueue())
    st._background = lambda fn, name=None: fn()
    return st


@pytest.fixture
def seen(app):
    got = []
    app.bus.subscribe(lambda e: got.append((e.kind, dict(e.data))))
    return got


def _now_of(name: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(library._started_at(name)).timestamp()


def _job_event(app, kind, folder, state="done"):
    event = jobs.JOB_DONE if state == "done" else jobs.JOB_FAILED
    app.bus.emit(event, job={"id": "x", "kind": kind, "folder": str(folder), "state": state})


def _auto_recording(folder: Path, transcribe=None):
    started = _now_of(folder.name)
    lines = [{"kind": "record.started", "at": started},
             {"kind": "record.stopped", "at": started + 1200, "duration_s": 1200.0}]
    (folder / "events.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n",
                                         encoding="utf-8")
    tail.write_call_end(folder, started + 300, 600.0, transcribe=transcribe)


# --- отметка «задача не закончена» ------------------------------------------------------


def test_pending_flag_lives_from_submit_to_the_end_of_the_job(state, app, root):
    folder = root / A
    state.transcribe(A)
    assert isinstance(library.read_meta(folder)["pending_transcribe"], float)
    _job_event(app, jobs.TRANSCRIBE, folder, "failed")
    assert "pending_transcribe" not in library.read_meta(folder)


def test_job_killed_by_shutdown_keeps_the_flag(state, app, queue, root):
    folder = root / A
    state.transcribe(A)
    queue.stopping = True  # резидент гасит очередь — задача падает не сама
    _job_event(app, jobs.TRANSCRIBE, folder, "failed")
    assert "pending_transcribe" in library.read_meta(folder)


def test_cancelled_job_is_not_repeated_after_restart(state, root):
    job = state.transcribe(A)
    assert state.cancel_job(job["id"]) == {"ok": True}
    assert "pending_transcribe" not in library.read_meta(root / A)


def test_import_is_marked_too(state, root, tmp_path):
    src = tmp_path / "звонок.mp3"
    src.write_bytes(b"x")
    got = state.import_file({"path": str(src)})
    assert "pending_transcribe" in library.read_meta(root / got["recording"])


# --- восстановление --------------------------------------------------------------------


def test_recover_requeues_interrupted_jobs_of_the_last_week(state, queue, root):
    library.write_meta(root / A, {"pending_transcribe": _now_of(A) + 60})
    library.write_meta(root / B, {"pending_transcribe": _now_of(B) + 60})
    done = state.recover(now=_now_of(B) + 3600)
    assert sorted(done["queued"]) == [A, B]
    assert [(j.kind, Path(j.folder).name) for j in queue.submitted] == [
        (jobs.TRANSCRIBE, A), (jobs.TRANSCRIBE, B)]


def test_recover_skips_older_than_a_week_and_finished_jobs(state, queue, root):
    library.write_meta(root / A, {"pending_transcribe": _now_of(A) + 60})
    assert state.recover(now=_now_of(A) + 8 * 86400)["queued"] == []  # старше недели
    library.write_meta(root / B, {"pending_transcribe": time.time() - 3600})
    library.write_transcript(root / B, {"segments": []})  # расшифровка дописана после отметки
    assert state.recover(now=_now_of(B) + 3600)["queued"] == [A]
    assert [Path(j.folder).name for j in queue.submitted] == [A]
    assert "pending_transcribe" not in library.read_meta(root / B)


def test_recover_trims_an_interrupted_auto_recording_then_transcribes(state, queue, root,
                                                                      monkeypatch):
    folder = root / A
    _auto_recording(folder, transcribe=True)
    trimmed = []
    monkeypatch.setattr(tail, "trim", lambda path: trimmed.append(Path(path).name) or 330.0)
    done = state.recover(now=_now_of(A) + 3600)
    assert done["trim"] == [A] and trimmed == [A]
    assert [(j.kind, Path(j.folder).name) for j in queue.submitted] == [(jobs.TRANSCRIBE, A)]


def test_recover_respects_the_short_call_and_the_setting(state, queue, root, monkeypatch,
                                                         tmp_path):
    _auto_recording(root / A, transcribe=False)  # короткий звонок — не расшифровывали
    _auto_recording(root / B)  # старое событие без отметки — по настройке
    _write_config(tmp_path, recording={"auto_transcribe": False})
    monkeypatch.setattr(tail, "trim", lambda path: None)
    done = state.recover(now=_now_of(B) + 3600)
    assert sorted(done["trim"]) == [A, B]
    assert queue.submitted == []


def test_recover_removes_unfinished_files(state, root):
    (root / A / "sys.opus.part").write_bytes(b"half")
    leftover = root / f".{B}.deleting-1234abcd"
    leftover.mkdir()
    (leftover / "sys.opus").write_bytes(b"x")
    done = state.recover(now=_now_of(B) + 60)
    assert not (root / A / "sys.opus.part").exists() and not leftover.exists()
    assert sorted(done["cleaned"]) == sorted([f"{A}/sys.opus.part", leftover.name])
    assert (root / A / "sys.opus").exists()


def test_recover_leaves_the_folder_being_recorded_alone(state, app, root, monkeypatch):
    (root / A / "sys.opus.part").write_bytes(b"half")
    library.write_meta(root / A, {"pending_transcribe": _now_of(A) + 60})
    monkeypatch.setattr(app, "recording", True)
    monkeypatch.setattr(app, "_current_folder", lambda: str(root / A))
    done = state.recover(now=_now_of(A) + 60)
    assert done == {"trim": [], "queued": [], "cleaned": []}
    assert (root / A / "sys.opus.part").exists()


def test_recover_finishes_a_merge_interrupted_after_transcription(state, root):
    target = merge.create(root, [root / A, root / B], keep_originals=False)
    library.update_meta(target, lambda m: {**m, "merge": {**m["merge"], "state": "merged"}})
    (target / "sys.opus").write_bytes(b"x")
    library.write_transcript(target, {"segments": []})
    state.recover(now=_now_of(A) + 3600)
    assert not (root / A).exists() and not (root / B).exists()
    assert merge.state(target) == "done"


# --- обработка в фоне (обрезка ожидания после звонка) ----------------------------------------


def test_recording_is_busy_while_its_tail_is_trimmed(state, app, root, seen):
    folder = root / A
    state._set_processing(folder, True)
    assert seen[-1] == (tray_control.RECORDING_PROCESSING, {"id": A})
    assert state.snapshot()["processing"] == [str(folder)]
    with pytest.raises(control.Conflict, match="обрабатывается"):
        state.transcribe(A)
    with pytest.raises(control.BadRequest, match="обрабатывается"):
        state.delete_recording(A)
    with pytest.raises(control.BadRequest, match="обрабатывается"):
        state.merge_recordings({"ids": [A, B], "keep_originals": True})
    with pytest.raises(control.Unavailable, match="обрабатывается"):
        state.track_path(A, "playback")
    state._set_processing(folder, False)
    assert seen[-1] == (tray_control.RECORDING_UPDATED, {"id": A})
    assert state.snapshot()["processing"] == []
    assert state.transcribe(A)["kind"] == jobs.TRANSCRIBE


def test_trim_thread_survives_a_failure_and_releases_the_recording(state, app, root,
                                                                   monkeypatch):
    folder = root / A
    lines = []
    monkeypatch.setattr(app, "log", lines.append)
    monkeypatch.setattr(tail, "trim", lambda path: 330.0)

    def broken(path):
        raise OSError("очередь недоступна")

    monkeypatch.setattr(state, "_queue_transcription", broken)
    state._start_trim(folder, True)  # не бросает: фоновый поток
    assert not state._processing_now(folder)
    assert any("обработка записи после остановки не завершена" in line for line in lines)


# --- занятая папка ------------------------------------------------------------------------


def test_delete_of_a_folder_held_by_another_program_is_refused_whole(state, root, monkeypatch):
    def busy(folders, *a, **kw):
        raise library.FolderBusy(library.FOLDER_BUSY)

    monkeypatch.setattr(library, "remove_folders", busy)
    with pytest.raises(control.Conflict, match="закройте её и повторите"):
        state.delete_recording(A)
    assert (root / A / "sys.opus").exists()


def test_merge_names_a_busy_folder_before_anything_starts(state, queue, root, monkeypatch):
    def busy(folders, *a, **kw):
        raise library.FolderBusy(library.FOLDER_BUSY)

    monkeypatch.setattr(library, "wait_removable", busy)
    with pytest.raises(control.Conflict, match="агент в терминале"):
        state.merge_recordings({"ids": [A, B]})
    assert queue.submitted == []
    # исходные остаются — проверять, свободны ли они, незачем
    assert state.merge_recordings({"ids": [A, B], "keep_originals": True})["recording"]


def test_finishing_a_merge_keeps_all_originals_when_one_is_held(state, app, root, monkeypatch):
    target = merge.create(root, [root / A, root / B], keep_originals=False)
    library.update_meta(target, lambda m: {**m, "merge": {**m["merge"], "state": "merged"}})

    def busy(folders, *a, **kw):
        raise library.FolderBusy(library.FOLDER_BUSY)

    monkeypatch.setattr(library, "remove_folders", busy)
    state._finish_merge(target)
    info = library.read_meta(target)["merge"]
    assert info["state"] == "done" and info["deleted"] == []
    assert "агент в терминале" in info["kept_reason"]
    assert (root / A).exists() and (root / B).exists()


def test_finish_merge_never_raises_from_the_background(state, app, root, monkeypatch):
    lines = []
    monkeypatch.setattr(app, "log", lines.append)
    monkeypatch.setattr(state, "_merge_info", lambda f: 1 / 0)
    state._finish_merge(root / A)
    assert any("объединение не завершено" in line for line in lines)


def test_part_of_an_unfinished_merge_tells_the_way_out(state, root):
    merge.create(root, [root / A, root / B], keep_originals=False)
    with pytest.raises(control.BadRequest,
                       match="Дождитесь расшифровки объединённой записи или удалите её"):
        state.delete_recording(A)


# --- выгрузка в базу знаний ----------------------------------------------------------------


def test_export_of_a_deleted_recording_is_not_reported(state, root, tmp_path, monkeypatch):
    from meet import kb_export

    def gone(folder, cfg, **kw):
        library.remove_folders([folder])
        raise OSError("нет файла")

    monkeypatch.setattr(kb_export, "export_recording", gone)
    state._auto_kb_export(root / A)
    assert state.snapshot()["kb_export_failed"] is None


def test_finished_export_tells_the_window(state, root, seen, monkeypatch):
    from meet import kb_export

    monkeypatch.setattr(kb_export, "export_recording",
                        lambda folder, cfg, **kw: {"path": "D:/kb/x", "files": [], "kept": []})
    state._auto_kb_export(root / A)
    assert (tray_control.RECORDING_UPDATED, {"id": A}) in seen


def test_resident_start_runs_recovery_in_background(app, monkeypatch):
    calls = []
    monkeypatch.setattr(tray_control.TrayControl, "recover_in_background",
                        lambda self: calls.append("recover"))

    class Server:
        def __init__(self, state, **kw):
            self.state = state

        def start(self, pid=None):
            pass

    monkeypatch.setattr(control, "ControlServer", Server)
    monkeypatch.setattr(control, "persisted_token", lambda: "t")
    server = app._start_control_api()
    assert server is not None and calls == ["recover"]


def test_recover_trims_one_at_a_time_and_holds_the_rest(state, root, monkeypatch):
    """Обе записи ждут обрезки: пока режется первая, вторая уже «обрабатывается»
    (её не расшифруют и не удалят из-под ffmpeg), а режутся они по очереди."""
    _auto_recording(root / A, transcribe=False)
    _auto_recording(root / B, transcribe=False)
    seen = []

    def trim(path):
        seen.append((Path(path).name, sorted(Path(p).name for p in state.snapshot()["processing"])))
        return None

    monkeypatch.setattr(tail, "trim", trim)
    state.recover(now=_now_of(B) + 3600)
    assert seen == [(A, [A, B]), (B, [B])]
    assert state.snapshot()["processing"] == []
