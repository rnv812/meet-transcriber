"""Объединение встреч в резиденте: запрос, отказы, задача сборки звука,
расшифровка следом, удаление исходных и выгрузка в базу знаний. Данные —
выдуманные."""

import json
import os
from pathlib import Path

import pytest

from meet import control, jobs, kb_export, library, merge, tray, tray_control

A, B, C = "2026-09-30_10-00", "2026-09-30_10-40", "2026-09-30_12-00"


def _write_config(tmp_path, export=None) -> None:
    path = tmp_path / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(tmp_path / "recordings"),
                      "voices_dir": str(tmp_path / "voices")},
        "export": {"meetings_dir": None, **(export or {})},
    }, ensure_ascii=False), encoding="utf-8")


class FakeQueue:
    """Очередь без подпроцессов: что поставлено и что «идёт» над папкой."""

    def __init__(self):
        self.submitted: list[jobs.Job] = []
        self.busy: dict[str, str] = {}

    def submit(self, kind, folder, options=None):
        job = jobs.Job(id=f"j{len(self.submitted)}", kind=kind, folder=str(folder))
        self.submitted.append(job)
        return job

    def active_for(self, folder, kinds):
        kind = self.busy.get(os.path.normcase(str(Path(folder).resolve())))
        return jobs.Job(id="busy", kind=kind, folder=str(folder)) if kind in kinds else None

    def listing(self):
        return []

    def cancel(self, job_id):
        return False

    def stop(self):
        pass


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _write_config(tmp_path)
    root = tmp_path / "recordings"
    for name, title in ((A, "Планёрка"), (B, None), (C, "Другая встреча")):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "sys.opus").write_bytes(b"x")
        (folder / "mic.opus").write_bytes(b"x")
        if title:
            library.write_meta(folder, {"title": title})
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
    st._background = lambda fn: fn()  # в тестах — сразу, в этом потоке
    return st


def _key(folder: Path) -> str:
    return os.path.normcase(str(folder.resolve()))


def _done(app, kind, folder):
    app.bus.emit(jobs.JOB_DONE, job={"id": "x", "kind": kind, "folder": str(folder), "state": "done"})


# --- запрос -------------------------------------------------------------------------


def test_merge_creates_the_folder_and_queues_the_sound_job(state, queue, root):
    got = state.merge_recordings({"ids": [B, A]})
    folder = root / got["recording"]
    assert got["recording"] == f"{A}_merged"
    assert got["job"]["kind"] == jobs.MERGE
    assert [(j.kind, j.folder) for j in queue.submitted] == [(jobs.MERGE, str(folder))]
    meta = library.read_meta(folder)
    assert meta["merged_from"] == [A, B] and meta["title"] == "Планёрка"
    assert meta["merge"]["keep_originals"] is False
    # пока объединение не завершено, его части не отдаются во второе и не удаляются
    with pytest.raises(control.BadRequest, match="входит в объединение «Планёрка»"):
        state.merge_recordings({"ids": [A, C], "keep_originals": True})
    with pytest.raises(control.BadRequest, match="входит в объединение"):
        state.delete_recording(B)


@pytest.mark.parametrize("body, error", [
    ({"ids": [A]}, "минимум две"),
    ({"ids": "a,b"}, "ids"),
    ({}, "ids"),
    ({"ids": [A, "нет-такой"]}, "записи нет"),
    ({"ids": [A, "../вне"]}, "записи нет"),
])
def test_merge_refuses_bad_requests(state, body, error):
    with pytest.raises(control.BadRequest, match=error):
        state.merge_recordings(body)


def test_merge_refuses_a_recording_being_written(state, app, root, monkeypatch):
    monkeypatch.setattr(app, "recording", True)
    monkeypatch.setattr(app, "_current_folder", lambda: str(root / B))
    with pytest.raises(control.BadRequest, match="запись ещё идёт"):
        state.merge_recordings({"ids": [A, B]})
    assert not list(root.glob("*_merged*"))


def test_merge_refuses_a_recording_being_transcribed(state, queue, root):
    queue.busy[_key(root / B)] = jobs.TRANSCRIBE
    with pytest.raises(control.BadRequest, match="идёт расшифровка"):
        state.merge_recordings({"ids": [A, B]})


# --- задачи следом -------------------------------------------------------------------------


def test_sound_ready_queues_a_normal_transcription(state, app, queue, root):
    folder = root / state.merge_recordings({"ids": [A, B]})["recording"]
    _done(app, jobs.MERGE, folder)
    assert [j.kind for j in queue.submitted] == [jobs.MERGE, jobs.TRANSCRIBE]
    assert queue.submitted[-1].folder == str(folder)


def test_retry_of_a_merge_without_sound_rebuilds_the_sound(state, queue, root):
    folder = root / state.merge_recordings({"ids": [A, B]})["recording"]
    state.transcribe(folder.name)
    assert queue.submitted[-1].kind == jobs.MERGE


def _merged(state, root, **body):
    folder = root / state.merge_recordings({"ids": [A, B], **body})["recording"]
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Вы", "text": "Начнём."}]})
    library.update_meta(folder, lambda m: {**m, "merge": {**m["merge"], "state": "merged"}})
    return folder


def test_after_transcription_the_originals_are_deleted(state, app, root):
    folder = _merged(state, root)
    _done(app, jobs.TRANSCRIBE, folder)
    assert not (root / A).exists() and not (root / B).exists()
    assert (root / C).exists()
    info = library.read_meta(folder)["merge"]
    assert info["state"] == "done" and info["deleted"] == [A, B]
    card = state.recording(folder.name)
    assert card["merge"] == {"parts": 2, "state": "done", "deleted": True, "kb_left": []}


def test_keep_originals_leaves_them(state, app, root):
    folder = _merged(state, root, keep_originals=True)
    _done(app, jobs.TRANSCRIBE, folder)
    assert (root / A).exists() and (root / B).exists()
    assert library.read_meta(folder)["merge"]["deleted"] == []


def test_busy_original_keeps_all_originals(state, app, queue, root):
    folder = _merged(state, root)
    queue.busy[_key(root / B)] = jobs.TRANSCRIBE
    _done(app, jobs.TRANSCRIBE, folder)
    assert (root / A).exists() and (root / B).exists()
    info = library.read_meta(folder)["merge"]
    assert info["deleted"] == [] and "идёт расшифровка" in info["kept_reason"]


def test_second_transcription_does_not_repeat_the_cleanup(state, app, root, monkeypatch):
    folder = _merged(state, root)
    _done(app, jobs.TRANSCRIBE, folder)
    monkeypatch.setattr(state, "_remove", lambda f: 1 / 0)
    _done(app, jobs.TRANSCRIBE, folder)  # перерасшифровка — исходных уже нет, и трогать нечего


def test_originals_exported_to_the_knowledge_base_bring_the_merged_one_there(
        state, app, root, tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_config(tmp_path, {"meetings_dir": str(vault), "auto_export": False})
    library.write_meta(root / B, {"kb_export": {"path": str(vault / "2026-09-30 - Старая часть")}})
    exported = []

    def export(folder, cfg):
        # исходные ещё в библиотеке: их папки в базе знаний заняты
        assert (root / B).exists()
        exported.append(Path(folder))
        return {"path": "x"}

    monkeypatch.setattr(kb_export, "export_recording", export)
    folder = _merged(state, root)
    _done(app, jobs.TRANSCRIBE, folder)
    assert exported == [folder]
    assert not (root / B).exists()
    info = state.recording(folder.name)["merge"]
    assert info["kb_left"] == [str(vault / "2026-09-30 - Старая часть")]


def test_merge_route_is_wired(state):
    assert ("POST", "/recordings/merge") in control._ROUTES


def test_originals_survive_unless_the_sound_was_merged(state, app, root):
    """Звук не собран до конца (сборку сорвало, остались случайные дорожки) —
    расшифровка такой папки не повод удалять исходные."""
    folder = root / state.merge_recordings({"ids": [A, B]})["recording"]
    (folder / "sys.opus").write_bytes(b"x")  # полдорожки от сорванной сборки
    library.write_transcript(folder, {"version": 1, "segments": []})
    _done(app, jobs.TRANSCRIBE, folder)
    assert (root / A).exists() and (root / B).exists()
    assert library.read_meta(folder)["merge"]["state"] == "pending"


def test_pending_merge_with_stray_tracks_rebuilds_the_sound(state, queue, root):
    folder = root / state.merge_recordings({"ids": [A, B]})["recording"]
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    state.transcribe(folder.name)
    assert queue.submitted[-1].kind == jobs.MERGE


def test_after_the_merge_is_done_its_parts_are_free_again(state, app, root):
    folder = _merged(state, root, keep_originals=True)
    _done(app, jobs.TRANSCRIBE, folder)
    assert state.delete_recording(A) == {"ok": True}
