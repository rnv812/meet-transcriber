"""Выгрузка в базу знаний в резиденте: кнопка, предпросмотр, автоматика после
расшифровки и итогов, уведомление о сбое. Данные — выдуманные."""

import json
import threading
from pathlib import Path

import pytest

from meet import control, jobs, kb_export, library, tray, tray_control

RID = "2026-09-30_10-15"


def _write_config(tmp_path, export=None, **extra) -> None:
    path = tmp_path / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(tmp_path / "recordings"),
                      "voices_dir": str(tmp_path / "voices")},
        "assistant": {"knowledge_dir": None, "notes_dir": None},
        "export": {"meetings_dir": None, **(export or {})},
        **extra,
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    _write_config(tmp_path)
    folder = tmp_path / "recordings" / RID
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    library.write_meta(folder, {"title": "Планирование спринта"})
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Анна", "text": "Начнём."}]})
    return tray.TrayApp()


@pytest.fixture
def state(app):
    release = threading.Event()

    def spawn(job, on_line):
        release.wait(timeout=5)
        return 0

    queue = jobs.JobQueue(app.bus, spawn=spawn)
    llm_queue = jobs.JobQueue(app.bus, spawn=spawn)
    st = tray_control.TrayControl(app, queue=queue, llm_queue=llm_queue)
    st._background = lambda fn, name=None: fn()  # выгрузка в тестах — сразу, в этом потоке
    try:
        yield st
    finally:
        release.set()
        queue.stop()
        llm_queue.stop()


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    path.mkdir()
    return path


def _done(app, kind, folder):
    app.bus.emit(jobs.JOB_DONE, job={"id": "j1", "kind": kind, "folder": str(folder),
                                     "state": "done"})


def _folder(tmp_path):
    return tmp_path / "recordings" / RID


# --- кнопка и предпросмотр -----------------------------------------------------------


def test_kb_export_without_meetings_dir_is_400(state):
    with pytest.raises(control.BadRequest, match="Папка для встреч не задана"):
        state.kb_export(RID)


def test_kb_export_writes_and_returns_path(state, tmp_path, vault):
    _write_config(tmp_path, {"meetings_dir": str(vault)})
    got = state.kb_export(RID)
    assert got["path"] == str(vault / "2026-09-30 - Планирование спринта")
    assert got["files"] == ["Транскрипт.md"]
    assert state.recording(RID)["kb_export"]["path"] == got["path"]


def test_kb_export_unknown_recording(state):
    assert state.kb_export("../вне") == {"error": "записи нет"}


def test_old_notes_route_is_the_kb_export(state, tmp_path, vault):
    _write_config(tmp_path, {"meetings_dir": str(vault)})
    assert state.to_notes(RID)["path"].endswith("2026-09-30 - Планирование спринта")


def test_preview_uses_latest_recording_and_overrides(state):
    got = state.export_preview({"folder_template": "{year}/{title}"})
    assert got == {"folder": "2026/Планирование спринта", "files": ["Транскрипт.md", "Итоги.md"],
                   "error": None}
    assert state.export_preview({"folder_template": "/{title}"})["error"]


def test_patch_with_bad_template_is_400(state):
    with pytest.raises(control.BadRequest, match="«..»"):
        state.patch_settings({"export": {"folder_template": "../{title}"}})


def test_snapshot_publishes_meetings_dir_for_the_shell(state, tmp_path, vault):
    assert state.snapshot()["meetings_dir"] is None
    _write_config(tmp_path, {"meetings_dir": str(vault)})
    assert state.snapshot()["meetings_dir"] == str(vault)
    assert state.snapshot()["kb_export_failed"] is None


def test_assistant_info_no_longer_mentions_notes(state):
    assert "notes_dir" not in state.assistant()


# --- автоматика ------------------------------------------------------------------------


def test_auto_export_after_transcription(app, state, tmp_path, vault):
    _write_config(tmp_path, {"meetings_dir": str(vault)})
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    assert (vault / "2026-09-30 - Планирование спринта" / "Транскрипт.md").exists()


def test_playback_is_mixed_in_background_after_transcription_or_import(
        app, state, tmp_path, _no_background_playback_mix):
    """Первое «▶» без ожидания: сведение для плеера ставится сразу после
    расшифровки и импорта (итоги дорожек не меняют)."""
    folder = _folder(tmp_path)
    _done(app, jobs.TRANSCRIBE, folder)
    _done(app, jobs.IMPORT, folder)
    _done(app, jobs.SUMMARY, folder)
    assert _no_background_playback_mix == [folder, folder]


def test_auto_export_after_import(app, state, tmp_path, vault):
    _write_config(tmp_path, {"meetings_dir": str(vault)})
    _done(app, jobs.IMPORT, _folder(tmp_path))
    assert (vault / "2026-09-30 - Планирование спринта").is_dir()


def test_no_auto_export_when_switched_off_or_not_set(app, state, tmp_path, vault):
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))  # папка не задана
    _write_config(tmp_path, {"meetings_dir": str(vault), "auto_export": False})
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    _done(app, jobs.SUMMARY, _folder(tmp_path))
    _done(app, jobs.ASK, _folder(tmp_path))
    assert list(vault.iterdir()) == []


def test_summary_reexports_when_auto(app, state, tmp_path, vault):
    _write_config(tmp_path, {"meetings_dir": str(vault)})
    (_folder(tmp_path) / "summary.md").write_text("# Итоги\n", encoding="utf-8")
    _done(app, jobs.SUMMARY, _folder(tmp_path))
    assert (vault / "2026-09-30 - Планирование спринта" / "Итоги.md").exists()


def test_summary_reexports_a_manually_exported_meeting(app, state, tmp_path, vault):
    _write_config(tmp_path, {"meetings_dir": str(vault), "auto_export": False})
    target = Path(state.kb_export(RID)["path"])
    library.write_meta(_folder(tmp_path), {"title": "Ретроспектива"})
    (_folder(tmp_path) / "summary.md").write_text("# Итоги\n", encoding="utf-8")
    _done(app, jobs.SUMMARY, _folder(tmp_path))
    assert (target / "Итоги.md").exists()
    assert [p.name for p in vault.iterdir()] == [target.name]


def test_auto_export_failure_is_logged_remembered_and_notified_once(
        app, state, tmp_path, vault, monkeypatch):
    lines = []
    monkeypatch.setattr(app, "log", lines.append)
    _write_config(tmp_path, {"meetings_dir": str(vault)})

    def broken(folder, cfg, **kw):
        kb_export._remember_error(Path(folder), OSError("диск недоступен"))
        raise OSError("диск недоступен")

    monkeypatch.setattr(kb_export, "export_recording", broken)
    _done(app, jobs.TRANSCRIBE, _folder(tmp_path))
    failed = state.snapshot()["kb_export_failed"]
    assert failed["folder"] == RID and "диск недоступен" in failed["error"]
    assert any("базу знаний" in line and "диск недоступен" in line for line in lines)
    assert "диск недоступен" in state.recording(RID)["kb_export"]["error"]
    # Итоги той же встречи снова не выгрузились — второго уведомления нет.
    _done(app, jobs.SUMMARY, _folder(tmp_path))
    assert state.snapshot()["kb_export_failed"] == failed


def test_broken_settings_in_job_subscriber_only_log(app, state, monkeypatch):
    lines = []
    monkeypatch.setattr(app, "log", lines.append)

    def broken(*a, **k):
        raise RuntimeError("config.json сломан")

    monkeypatch.setattr(tray_control.settings, "load", broken)
    _done(app, jobs.TRANSCRIBE, "C:/rec/" + RID)  # не бросает в очередь задач
    assert any("config.json сломан" in line for line in lines)
    assert app.bus.failures == 0


# --- переименование встречи ----------------------------------------------------------


def test_rename_moves_exported_folder_and_reexports(app, state, tmp_path, vault, monkeypatch):
    lines = []
    monkeypatch.setattr(app, "log", lines.append)
    _write_config(tmp_path, {"meetings_dir": str(vault), "auto_export": False})
    old = Path(state.kb_export(RID)["path"])
    state.update_recording(RID, {"title": "Ретроспектива"})
    new = vault / "2026-09-30 - Ретроспектива"
    assert not old.exists() and new.is_dir()
    assert "# Ретроспектива" in (new / "Транскрипт.md").read_text(encoding="utf-8")
    assert state.recording(RID)["kb_export"]["path"] == str(new)
    assert any("переименована" in line and new.name in line for line in lines)


def test_rename_with_foreign_files_reexports_into_old_folder(app, state, tmp_path, vault):
    _write_config(tmp_path, {"meetings_dir": str(vault)})
    old = Path(state.kb_export(RID)["path"])
    (old / "Заметки.md").write_text("своё", encoding="utf-8")
    state.update_recording(RID, {"title": "Ретроспектива"})
    assert [p.name for p in vault.iterdir()] == [old.name]
    assert "# Ретроспектива" in (old / "Транскрипт.md").read_text(encoding="utf-8")


def test_rename_of_never_exported_meeting_exports_nothing(app, state, tmp_path, vault):
    _write_config(tmp_path, {"meetings_dir": str(vault)})
    state.update_recording(RID, {"title": "Ретроспектива"})
    assert list(vault.iterdir()) == []
