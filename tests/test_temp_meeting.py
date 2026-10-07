"""«Остановить без сохранения» и «Временная встреча с ассистентом».

Резидент — настоящие TrayApp + TrayControl + LiveControl с заглушкой ребёнка
(`test_live_control.resident`); запись — поддельный record(), который, как
настоящий, создаёт папку внутри переданного ему корня и держит там lock.
Провайдеры не вызываются: `llm.forget_session` подменён и только записывает,
что его просили забыть.
"""

import json
import os
import subprocess
import time
import types
from datetime import datetime
from pathlib import Path

import pytest

from meet import events, jobs, library, llm, recorder, temp_meeting, tray, tray_control
from meet.assist import chatlog

# Заглушка ребёнка и фикстуры резидента — общие с test_live_control.
from test_live_control import _Queue, _wait_for, resident  # noqa: F401  (фикстуры pytest)

NAME = "2026-10-07_15-00"
SID = "5f0c1a7e-0000-4000-8000-00000000abcd"
OLD_SID = "5f0c1a7e-0000-4000-8000-00000000ef01"


@pytest.fixture
def forgotten(monkeypatch):
    calls: list = []

    def forget(provider, session_id):
        calls.append((provider, session_id))
        return 1

    monkeypatch.setattr(llm, "forget_session", forget)
    return calls


@pytest.fixture
def hooks(monkeypatch):
    calls: list = []
    monkeypatch.setattr(tray, "_run_post_hook", lambda folder: calls.append(folder))
    return calls


@pytest.fixture
def res(resident, monkeypatch, tmp_path):  # noqa: F811
    """Резидент, чья поддельная запись пишет туда, куда ей сказали: в
    библиотеку или в папку сеанса временной встречи."""

    def fake_record(out_root, stop_event=None, *, bus, pcm_tap=None):
        root = Path(out_root)
        # Как настоящая запись: новая папка, занятое имя — `_2`, `_3`…
        folder = recorder.new_folder(root, now=datetime(2026, 10, 7, 15, 0))
        (folder / "sys.opus").write_bytes(b"x")
        (folder / "mic.opus").write_bytes(b"x")
        lock = root / recorder.LOCK_NAME
        lock.write_text(json.dumps({"pid": os.getpid(), "folder": str(folder)}), encoding="utf-8")
        feed = pcm_tap.begin(2)
        feed.configure(0, "sys.opus", 48000, 2)
        feed.configure(1, "mic.opus", 16000, 1)
        bus.emit(events.RECORD_STARTED, folder=str(folder))
        stop_event.wait(60)
        feed.end()
        lock.unlink(missing_ok=True)
        return folder

    monkeypatch.setattr(tray, "record", fake_record)
    monkeypatch.setattr(resident.tray, "_current_folder",
                        types.MethodType(tray.TrayApp._current_folder, resident.tray))
    resident.library = tmp_path / "recordings"
    resident.discarded = []
    resident.bus.subscribe(lambda e: resident.discarded.append(e.data)
                           if e.kind == events.RECORD_DISCARDED else None)
    return resident


def _old_recording(root: Path) -> Path:
    """Обычная запись в библиотеке — чтобы списки были не пустыми."""
    folder = root / "2026-10-01_09-00"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "mic.opus").write_bytes(b"x")
    return folder


def _assistant_data(folder: Path, group: str | None = None) -> None:
    """Что оставляет ассистент: журнал чата, вложения, разбор материала, сеансы
    агента (действующий и прежний, после смены id)."""
    log = chatlog.ChatLog(folder)
    log.set_session_id("claude-code", OLD_SID)
    log.set_session_id("claude-code", SID)
    log.set_session_id("codex", "019a-thread")
    files = folder / "assistant" / "files"
    files.mkdir(parents=True, exist_ok=True)
    (files / "shot.png").write_bytes(b"png")
    materials = folder / "assistant" / "materials"
    materials.mkdir(parents=True, exist_ok=True)
    (materials / "doc.md").write_text("секрет", encoding="utf-8")
    (folder / "assistant" / "chat.jsonl").write_text("{}\n", encoding="utf-8")
    (folder / "live_transcript.md").write_text("реплика\n", encoding="utf-8")
    if group:
        library.write_meta(folder, {"group": group})


def _start(res, temporary: bool) -> Path:
    reply = res.live_start({"temporary": True} if temporary else None)
    assert reply["starting"] is True
    _wait_for(lambda: res.snapshot()["live"]["active"])
    return Path(res.snapshot()["folder"])


def _ids(listing) -> list[str]:
    items = listing["items"] if isinstance(listing, dict) else listing
    return [item["id"] for item in items]

def _ids_all(res) -> list[str]:
    return _ids(res.recordings())


def _link(link: Path, target: Path) -> None:
    """Ссылка на папку: на Windows — junction (прав не нужно), иначе symlink."""
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True)
        if done.returncode != 0:
            pytest.skip("junction не создаётся")
    else:
        os.symlink(target, link, target_is_directory=True)


def _wait_kept(res, name: str = NAME) -> Path:
    moved = res.library / name
    _wait_for(lambda: res.snapshot()["last_stop"] is not None
              and res.snapshot()["last_stop"]["folder"] == str(moved), timeout=10)
    return moved


# --- «Остановить без сохранения» --------------------------------------------


def test_discard_deletes_the_recording_and_assistant_data_and_forgets_sessions(
        res, forgotten, hooks):
    folder = _start(res, temporary=False)
    assert folder.parent == res.library
    _assistant_data(folder)
    reply = res.stop_recording(discard=True)
    assert reply["action"] == "cancelled"
    assert not folder.exists()
    assert set(forgotten) == {("claude-code", SID), ("claude-code", OLD_SID),
                              ("codex", "019a-thread")}
    assert res.queue.submitted == [] and res.llm_queue.submitted == []
    assert hooks == []
    assert res.snapshot()["last_stop"]["reason"] == "discarded"
    assert NAME not in _ids_all(res)
    assert res.discarded and res.discarded[-1]["folder"] == str(folder)
    _wait_for(lambda: not res.live.busy())
    assert res.live.status()["error"] is None


def test_discard_with_a_busy_file_deletes_the_rest_and_hides_the_leftover(
        res, forgotten, hooks):
    """Файл держит другая программа: всё остальное удалено, остаток спрятан из
    библиотеки (переименован в `.<id>.deleting-…` или, если Windows не даёт
    переименовать папку с открытым файлом, помечен) и доудаляется, когда файл
    отпустят."""
    if os.name != "nt":
        pytest.skip("занятый файл не мешает удалению вне Windows")
    folder = _start(res, temporary=False)
    _assistant_data(folder)
    (folder / "assistant" / "zz_secret.md").write_text("секрет", encoding="utf-8")
    held = open(folder / "assistant" / "chat.jsonl", "rb")
    try:
        res.stop_recording(discard=True)
        left = [p for p in res.library.iterdir() if p.name == NAME or p.name.startswith(f".{NAME}.deleting-")]
        assert len(left) == 1
        files = sorted(str(p.relative_to(left[0])) for p in left[0].rglob("*") if p.is_file())
        assert str(Path("assistant") / "chat.jsonl") in files
        assert not any(name.endswith((".opus", "zz_secret.md", "sessions.json", "doc.md"))
                       for name in files)
        assert NAME not in _ids_all(res) and res.queue.submitted == []
    finally:
        held.close()
    if left[0].name == NAME:
        _wait_for(lambda: not left[0].exists(), timeout=10)  # доудалил в фоне
    else:
        assert library.leftover_deletions(res.library) == left


def test_discarded_leftover_is_finished_on_the_next_start(res, forgotten):
    """Остаток с отметкой (не удалился и не переименовался) — следующий запуск
    доудаляет, а до того его нет в библиотеке."""
    leftover = res.library / "2026-10-02_10-00"
    leftover.mkdir(parents=True)
    (leftover / "mic.opus").write_bytes(b"x")
    (leftover / library.DISCARDED_MARK).write_text("{}", encoding="utf-8")
    assert "2026-10-02_10-00" not in _ids_all(res)
    temp_meeting.root().mkdir(parents=True, exist_ok=True)
    res._sweep_temporary()
    assert not leftover.exists()


def test_rmtree_deletes_everything_it_can_past_a_busy_file(tmp_path):
    if os.name != "nt":
        pytest.skip("занятый файл не мешает удалению вне Windows")
    d = tmp_path / "rec"
    (d / "assistant").mkdir(parents=True)
    for name in ("assistant/chat.jsonl", "assistant/zz_secret.md", "mic.opus", "sys.opus"):
        (d / name).write_text("x", encoding="utf-8")
    held = open(d / "assistant" / "chat.jsonl", "rb")
    try:
        assert temp_meeting._rmtree(d, tries=3, pause=0) is False
        assert sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file()) == [
            str(Path("assistant") / "chat.jsonl")]
    finally:
        held.close()
    assert temp_meeting._rmtree(d, tries=1, pause=0) is True


def test_discard_also_forgets_the_agent_tab_project(res, forgotten, hooks, tmp_path):
    """Вкладка «Агент» работала в папке записи: папка проекта Claude Code
    (сеансы, память) и строки history.jsonl этой папки удаляются; чужие — нет."""
    from meet.llm import claude

    folder = _start(res, temporary=False)
    config = Path(os.environ["CLAUDE_CONFIG_DIR"])
    project = config / "projects" / claude.project_dir_name(folder)
    (project / "memory").mkdir(parents=True)
    (project / "memory" / "MEMORY.md").write_text("о встрече", encoding="utf-8")
    (project / f"{SID}.jsonl").write_text(json.dumps({"cwd": str(folder)}) + "\n", encoding="utf-8")
    other = config / "projects" / "D--other"
    other.mkdir(parents=True)
    history = config / "history.jsonl"
    mine = json.dumps({"display": "что решили?", "project": str(folder)}, ensure_ascii=False)
    theirs = json.dumps({"display": "чужое", "project": "D:\\other"}, ensure_ascii=False)
    history.write_bytes((theirs + "\r\n" + mine + "\n" + theirs + "\n").encode("utf-8"))
    res.stop_recording(discard=True)
    assert not project.exists() and other.exists()
    assert history.read_bytes() == (theirs + "\r\n" + theirs + "\n").encode("utf-8")


# --- временная встреча ------------------------------------------------------


def test_temporary_meeting_lives_outside_the_library(res, forgotten, hooks):
    _old_recording(res.library)
    folder = _start(res, temporary=True)
    session = folder.parent
    assert session.parent == temp_meeting.root()
    assert temp_meeting.SESSION_RE.fullmatch(session.name)
    assert not str(folder).startswith(str(res.library))
    snap = res.snapshot()
    assert snap["temporary"] is True and snap["source"] == "live"
    assert snap["live"]["folder"] == str(folder)
    if os.name != "nt":
        assert (session.stat().st_mode & 0o777) == 0o700
    _assistant_data(folder, group="g-temp")
    # Ни в списке (и «Последней записи» панели), ни в поиске, ни в группах и фильтрах.
    assert _ids_all(res) == ["2026-10-01_09-00"]
    assert NAME not in _ids(res.search("реплика"))
    assert res.facets()["total"] == 1
    groups = res.groups()
    assert groups["none"] == 1 and groups["unknown"] == []
    # Lock — в папке сеанса, не в библиотеке.
    assert not (res.library / recorder.LOCK_NAME).exists()
    assert (session / recorder.LOCK_NAME).exists()
    res.stop_recording()


def test_stopping_a_temporary_meeting_leaves_nothing(res, forgotten, hooks):
    folder = _start(res, temporary=True)
    session = folder.parent
    _assistant_data(folder)
    res.live_stop()
    _wait_for(lambda: not res.tray.recording and not session.exists())
    _wait_for(lambda: not res.live.busy() and not res.live.finishing())
    assert not temp_meeting.root().exists() or list(temp_meeting.root().iterdir()) == []
    assert set(forgotten) == {("claude-code", SID), ("claude-code", OLD_SID),
                              ("codex", "019a-thread")}
    # Ни расшифровки, ни анализа, ни названия, ни хука, ни выгрузки.
    assert res.queue.submitted == [] and res.llm_queue.submitted == []
    assert hooks == []
    assert not res.library.exists() or list(res.library.iterdir()) == []
    snap = res.snapshot()
    assert snap["temporary"] is False
    assert snap["last_stop"]["reason"] == "temporary"
    assert res.discarded[-1] == {"folder": str(folder), "temporary": True}
    assert res.live.status()["error"] is None


def test_temporary_meeting_paths_stay_out_of_the_logs(res, forgotten, hooks):
    lines: list[str] = []
    real = res.tray.log
    res.tray.log = lambda message: (lines.append(message), real(message))
    folder = _start(res, temporary=True)
    res.stop_recording()
    _wait_for(lambda: not res.live.busy() and not res.live.finishing())
    assert lines and not any(folder.parent.name in line or NAME in line for line in lines)


def test_any_stop_path_drops_a_temporary_meeting(res, forgotten, hooks):
    """«Стоп» в окне (`/recording/stop`) и выход резидента — тоже конец
    временной встречи: она не сохраняется ни одним путём."""
    folder = _start(res, temporary=True)
    res.stop_recording()
    assert not folder.parent.exists()
    assert res.queue.submitted == [] and hooks == []
    folder = _start(res, temporary=True)
    res.shutdown()
    assert not folder.parent.exists()
    assert res.queue.submitted == [] and hooks == []


def test_a_normal_recording_after_a_temporary_one_never_touches_tmp_meetings(
        res, forgotten, hooks, monkeypatch):
    monkeypatch.setattr(tray_control.TrayControl, "_live_title", lambda self, folder: None)
    folder = _start(res, temporary=True)
    res.stop_recording()
    stranger = temp_meeting.root() / "0123456789ab"
    stranger.mkdir(parents=True)
    (stranger / "mine.txt").write_text("x", encoding="utf-8")
    assert res.tray.start_recording(tray_control.MANUAL)
    _wait_for(lambda: (res.library / NAME).exists())
    assert res.snapshot()["temporary"] is False
    assert Path(res.tray._current_folder()).parent == res.library
    res.stop_recording()
    assert (res.library / NAME).is_dir()  # обычная запись — в библиотеке и цела
    assert (stranger / "mine.txt").exists()
    assert not folder.parent.exists()


def test_a_failed_temporary_start_leaves_a_harmless_flag(res, forgotten, hooks, monkeypatch):
    """Запись временной встречи не началась (нет устройства): флаг временной
    остаётся до следующего старта, но обычная запись его сбрасывает и в
    библиотеку пишет как обычная."""
    def broken(out_root, stop_event=None, *, bus, pcm_tap=None):
        raise SystemExit("нет устройства")

    real = tray.record
    monkeypatch.setattr(tray, "record", broken)
    res.tray.start_recording(tray_control.LIVE, temporary=True)
    _wait_for(lambda: res.tray.thread is None or not res.tray.thread.is_alive())
    monkeypatch.setattr(tray, "record", real)
    res.tray.recording = False  # тикер снял бы флаг сам (_collect_error)
    assert res.tray.start_recording(tray_control.MANUAL)
    assert res.tray.temporary is False and res.tray.temp_session is None
    _wait_for(lambda: (res.library / NAME).exists())
    res.stop_recording()
    assert (res.library / NAME).is_dir()


def test_keep_turns_a_temporary_meeting_into_a_normal_recording(res, forgotten, hooks,
                                                               monkeypatch):
    monkeypatch.setattr(tray_control.TrayControl, "_live_title", lambda self, folder: None)
    folder = _start(res, temporary=True)
    session = folder.parent
    _assistant_data(folder)
    reply = res.keep_recording()
    assert reply["action"] == "kept" and reply["temporary"] is False
    assert (session / temp_meeting.KEEP_MARK).exists()
    began = time.monotonic()
    res.stop_recording()
    assert time.monotonic() - began < 5  # перенос — в фоне, «Стоп» его не ждёт
    moved = _wait_kept(res)
    assert moved.is_dir() and (moved / "assistant" / "chat.jsonl").exists()
    _wait_for(lambda: not session.exists())
    _wait_for(lambda: res.queue.submitted == [(jobs.TRANSCRIBE, str(moved))])
    _wait_for(lambda: hooks == [str(moved)])  # хук — следом, в том же фоновом переносе
    assert library.read_meta(moved)["source"] == "live"
    assert res.snapshot()["last_stop"]["reason"] == "saved"
    assert forgotten == []  # обычная встреча: сеанс агента продолжится после неё
    assert NAME in _ids_all(res)


def test_keep_takes_a_free_name_in_the_library_and_never_nests(res, forgotten, hooks,
                                                               monkeypatch):
    monkeypatch.setattr(tray_control.TrayControl, "_live_title", lambda self, folder: None)
    busy = res.library / NAME
    busy.mkdir(parents=True)
    (busy / "mic.opus").write_bytes(b"x")
    _start(res, temporary=True)
    res.keep_recording()
    res.stop_recording()
    moved = _wait_kept(res, f"{NAME}_2")
    assert (moved / "sys.opus").exists()
    assert not (busy / NAME).exists()  # не внутрь существующей
    _wait_for(lambda: res.queue.submitted == [(jobs.TRANSCRIBE, str(moved))])


def test_keep_waits_for_a_recording_thread_that_outlived_the_join(res, tmp_path, monkeypatch):
    """Поток записи не успел за join: перенос ждёт его, потом — как обычно."""
    import threading

    monkeypatch.setattr(tray_control.TrayControl, "_live_title", lambda self, folder: None)
    session = temp_meeting.new_session()
    folder = session / NAME
    result: dict = {}

    def late():
        time.sleep(0.3)
        folder.mkdir()
        (folder / "mic.opus").write_bytes(b"x")
        result["folder"] = folder

    thread = threading.Thread(target=late)
    thread.start()
    temp_meeting.mark_keep(session)
    res.tray._finish_kept(session, result, thread, tray_control.LIVE, False)
    moved = res.library / NAME
    assert (moved / "mic.opus").exists() and not session.exists()
    assert res.tray.last_stop["folder"] == str(moved)


def test_keep_needs_a_temporary_meeting(res):
    from meet import control

    with pytest.raises(control.BadRequest, match="Временная встреча не идёт"):
        res.keep_recording()
    _start(res, temporary=False)
    with pytest.raises(control.BadRequest):
        res.keep_recording()
    res.stop_recording(discard=True)


def test_discarding_a_kept_temporary_meeting_still_deletes_it(res, forgotten, hooks):
    folder = _start(res, temporary=True)
    _assistant_data(folder)
    res.keep_recording()
    res.stop_recording(discard=True)
    assert not folder.parent.exists()
    assert not (res.library / NAME).exists()
    assert res.queue.submitted == [] and hooks == []
    assert ("claude-code", SID) in forgotten
    assert res.snapshot()["last_stop"]["reason"] == "discarded"


# --- перенос в библиотеку -------------------------------------------------------


def _temp_recording(name: str = NAME) -> Path:
    session = temp_meeting.new_session()
    folder = session / name
    (folder / "assistant").mkdir(parents=True)
    (folder / "a.opus").write_text("a", encoding="utf-8")
    (folder / "assistant" / "b.jsonl").write_text("b", encoding="utf-8")
    return folder


def test_move_picks_a_free_name_when_the_target_appears(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    lib = tmp_path / "lib"
    existing = lib / NAME
    existing.mkdir(parents=True)
    (existing / "other.opus").write_text("o", encoding="utf-8")
    folder = _temp_recording()
    moved = temp_meeting.move_to_library(folder, lib, sleep=lambda s: None)
    assert moved == lib / f"{NAME}_2" and (moved / "a.opus").exists()
    assert sorted(p.name for p in existing.iterdir()) == ["other.opus"]


def test_cross_device_move_copies_beside_then_renames_and_never_nests(tmp_path, monkeypatch):
    """Другой том: копия в `.<имя>.partial-…`, переименование на место. Источник
    не удалился (занят) — сеанс помечен `moved`: уборка его только удалит, не
    перенесёт второй раз; вложенной копии нет."""
    import errno

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    lib = tmp_path / "lib"
    folder = _temp_recording()
    session = folder.parent
    temp_meeting.mark_keep(session)
    real_direct = temp_meeting._move_direct

    def direct(src, dst):
        if Path(src) == folder:
            raise OSError(errno.EXDEV, "cross-device")
        return real_direct(src, dst)

    monkeypatch.setattr(temp_meeting, "_move_direct", direct)
    real_rmtree = temp_meeting._rmtree
    monkeypatch.setattr(temp_meeting, "_rmtree",
                        lambda p, **kw: False if Path(p) == folder else real_rmtree(p, **kw))
    moved = temp_meeting.move_to_library(folder, lib, sleep=lambda s: None)
    assert moved == lib / NAME
    assert sorted(str(p.relative_to(lib)) for p in lib.rglob("*")) == [
        NAME, str(Path(NAME) / "a.opus"), str(Path(NAME) / "assistant"),
        str(Path(NAME) / "assistant" / "b.jsonl")]
    assert (session / temp_meeting.MOVED_MARK).exists() and not temp_meeting.is_kept(session)
    # Следующий запуск: остаток только удаляется — ни второго переноса, ни забывания.
    monkeypatch.setattr(temp_meeting, "_rmtree", real_rmtree)
    calls = []
    done = temp_meeting.sweep(library_root=lib, forget=lambda *a: calls.append(a),
                              forget_project=lambda f: None)
    assert done["kept"] == [] and not session.exists() and calls == []
    assert sorted(p.name for p in lib.iterdir()) == [NAME]


def test_sweep_removes_partial_copies_from_the_library(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    lib = tmp_path / "lib"
    partial = lib / f".{NAME}{temp_meeting.PARTIAL_MARK}deadbeef"
    partial.mkdir(parents=True)
    (partial / "a.opus").write_text("a", encoding="utf-8")
    temp_meeting.root().mkdir(parents=True)
    temp_meeting.sweep(library_root=lib)
    assert not partial.exists()


# --- после сбоя ---------------------------------------------------------------


def _leftover(name: str, *, keep: bool = False, age_s: float = 3600) -> Path:
    session = temp_meeting.root() / name
    folder = session / NAME
    folder.mkdir(parents=True)
    (folder / "mic.opus").write_bytes(b"x")
    _assistant_data(folder)
    if keep:
        temp_meeting.mark_keep(session)
    past = time.time() - age_s
    os.utime(session, (past, past))
    return session


def test_leftovers_of_a_crash_are_removed_on_start(res, forgotten, monkeypatch):
    monkeypatch.setattr(tray_control.TrayControl, "_live_title", lambda self, folder: None)
    gone = _leftover("aaaaaaaaaaaa")
    kept = _leftover("bbbbbbbbbbbb", keep=True)
    fresh = _leftover("cccccccccccc", age_s=-60)  # начата уже этим резидентом
    res._sweep_temporary()
    assert not gone.exists()
    assert ("claude-code", SID) in forgotten
    assert not kept.exists() and (res.library / NAME / "mic.opus").exists()
    assert fresh.exists()
    assert NAME in _ids_all(res)
    # Сохранённую как обычную — и обработать, как обычную.
    assert res.queue.submitted == [(jobs.TRANSCRIBE, str(res.library / NAME))]
    assert library.read_meta(res.library / NAME)["source"] == "live"


def test_recover_in_background_sweeps_temporary_meetings(res, forgotten, monkeypatch):
    gone = _leftover("dddddddddddd")
    monkeypatch.setattr(res, "_background", lambda fn, name="": fn())
    res.recover_in_background()
    assert not gone.exists()


def test_sweep_skips_the_meeting_in_progress(res, forgotten):
    folder = _start(res, temporary=True)
    past = time.time() - 3600
    os.utime(folder.parent, (past, past))
    temp_meeting.sweep(skip=lambda: res.tray.temp_session)
    assert folder.exists()
    res.stop_recording()


def test_sweep_only_takes_generated_session_names(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    odd = [temp_meeting.root() / name for name in ("notes", "ABCDEF012345", "abc", "0123456789abc")]
    for path in odd:
        path.mkdir(parents=True)
        (path / "keep.txt").write_text("x", encoding="utf-8")
    good = temp_meeting.root() / "0123456789ab"
    good.mkdir()
    temp_meeting.sweep(forget=lambda *a: 0, forget_project=lambda f: None)
    assert all(p.exists() for p in odd) and not good.exists()


@pytest.mark.parametrize("where", ["inside", "same", "contains"])
def test_sweep_refuses_when_the_library_overlaps_the_tmp_root(tmp_path, monkeypatch, where):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    root = temp_meeting.root()
    session = root / "0123456789ab"
    (session / NAME).mkdir(parents=True)
    lib = {"inside": root / "lib", "same": root, "contains": tmp_path / "data"}[where]
    assert temp_meeting.conflicts_with_library(lib)
    done = temp_meeting.sweep(library_root=lib, forget=lambda *a: 0,
                              forget_project=lambda f: None, log=lambda m: None)
    assert done == {"wiped": [], "kept": []} and session.exists()


def test_links_are_never_followed_by_sweep_or_wipe(tmp_path, monkeypatch):
    """Ссылка вместо сеанса и ссылка внутри сеанса ведут в настоящую запись:
    её сеансы агента не забываются, её файлы целы."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    real = tmp_path / "lib" / "2026-10-01_10-00"
    (real / "assistant").mkdir(parents=True)
    (real / "precious.opus").write_text("audio", encoding="utf-8")
    (real / "assistant" / "sessions.json").write_text(json.dumps(
        {"heads": {"agent": {"claude-code": {"id": SID}}}}), encoding="utf-8")
    calls = []
    forget = lambda provider, sid: calls.append((provider, sid)) or 1  # noqa: E731
    temp_meeting.root().mkdir(parents=True)
    _link(temp_meeting.root() / "aaaaaaaaaaaa", real)
    done = temp_meeting.sweep(forget=forget, forget_project=lambda f: None)
    assert calls == [] and done["wiped"] == [] and (real / "precious.opus").exists()
    session = temp_meeting.new_session()
    (session / NAME).mkdir()
    _link(session / NAME / "evil", real)
    _link(session / "evil2", real)
    assert temp_meeting.wipe(session, forget=forget, forget_project=lambda f: None, pause=0)
    assert calls == [] and (real / "precious.opus").exists() and not session.exists()
    # Сама папка — ссылка: wipe отказывается.
    link = tmp_path / "link"
    _link(link, real)
    assert temp_meeting.wipe(link, forget=forget, forget_project=lambda f: None) is False
    assert calls == [] and (real / "precious.opus").exists()


def test_path_like_ids_go_nowhere_through_the_real_forget(tmp_path, monkeypatch):
    """Подложенные sessions.json и meta.json с id-путями — через настоящий
    `llm.forget_session` (Claude Code и Codex — в своих временных папках
    настроек, CLI Codex и OpenCode не найдены): ничего не удаляется."""
    from meet.llm import codex, opencode

    monkeypatch.setattr(codex, "find_codex", lambda: None)
    monkeypatch.setattr(opencode, "find_opencode", lambda: None)
    victim = tmp_path / "victim.txt"
    victim.write_text("x", encoding="utf-8")
    config = Path(os.environ["CLAUDE_CONFIG_DIR"])
    (config / "projects" / "p").mkdir(parents=True)
    (Path(os.environ["CODEX_HOME"]) / "sessions").mkdir(parents=True, exist_ok=True)
    folder = tmp_path / "rec"
    (folder / "assistant").mkdir(parents=True)
    bad = ["../../victim", "..\\..\\victim.txt", "*", SID + "\n", "a/../../victim",
           str(victim), "-rf"]
    (folder / "assistant" / "sessions.json").write_text(json.dumps({
        "heads": {"agent": {p: {"id": b} for p, b in zip(("claude-code", "codex", "opencode"), bad)}},
        "past": [{"provider": p, "id": b} for p in ("claude-code", "codex", "opencode") for b in bad],
    }), encoding="utf-8")
    library.write_meta(folder, {"agent_sessions": {"claude-code": {"id": "../victim"}}})
    assert temp_meeting.forget_sessions(folder) == 0
    assert victim.exists() and (config / "projects" / "p").exists()


# --- модуль ---------------------------------------------------------------------


def test_session_ids_read_current_past_and_agent_tab(tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    _assistant_data(folder)
    library.write_meta(folder, {"agent_sessions": {"opencode": {"at": 1.0, "id": "ses_1"}}})
    assert set(temp_meeting.session_ids(folder)) == {
        ("claude-code", SID), ("claude-code", OLD_SID), ("codex", "019a-thread"),
        ("opencode", "ses_1")}
    (folder / "assistant" / "sessions.json").write_text("{битый", encoding="utf-8")
    assert temp_meeting.session_ids(folder) == [("opencode", "ses_1")]


def test_forget_gaps_name_agent_tab_sessions_without_an_id(tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    assert temp_meeting.forget_gaps(folder) == []
    library.write_meta(folder, {"agent_sessions": {
        "claude-code": {"at": 1.0, "id": SID}, "codex": {"at": 1.0, "id": None},
        "opencode": 1.0}})
    assert temp_meeting.forget_gaps(folder) == ["Codex", "OpenCode"]


def test_chatlog_keeps_replaced_and_forgotten_ids_in_past(tmp_path):
    log = chatlog.ChatLog(tmp_path)
    log.set_session_id("claude-code", "a")
    log.set_session_id("claude-code", "a")
    log.set_session_id("claude-code", "b")
    log.set_session_id("claude-code", None)
    data = json.loads(log.sessions_path.read_text(encoding="utf-8"))
    assert data["past"] == [{"provider": "claude-code", "id": "a"},
                            {"provider": "claude-code", "id": "b"}]
    assert log.session_id("claude-code") is None


def test_wipe_survives_a_failing_forget(tmp_path):
    folder = tmp_path / "rec"
    folder.mkdir()
    _assistant_data(folder)
    logged = []

    def boom(provider, sid):
        raise RuntimeError("CLI упал")

    assert temp_meeting.wipe(folder, forget=boom, forget_project=lambda f: None,
                             log=logged.append, pause=0) is True
    assert not folder.exists()
    assert any("не забыт" in line for line in logged)
    assert not any(SID in line for line in logged)  # id сеансов в журнал не идут


def test_claude_forget_session_removes_its_companions(tmp_path, monkeypatch):
    from meet.llm import claude

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "projects" / "D--x").mkdir(parents=True)
    (tmp_path / "projects" / "D--x" / f"{SID}.jsonl").write_text("{}", encoding="utf-8")
    for d in ("file-history", "session-env"):
        (tmp_path / d / SID).mkdir(parents=True)
    (tmp_path / "todos").mkdir()
    (tmp_path / "todos" / f"{SID}-agent-{SID}.json").write_text("[]", encoding="utf-8")
    (tmp_path / "todos" / "other.json").write_text("[]", encoding="utf-8")
    (tmp_path / "debug").mkdir()
    (tmp_path / "debug" / f"{SID}.txt").write_text("x", encoding="utf-8")
    assert claude.forget_session(SID) == 1
    assert not (tmp_path / "file-history" / SID).exists()
    assert not (tmp_path / "session-env" / SID).exists()
    assert not (tmp_path / "debug" / f"{SID}.txt").exists()
    assert [p.name for p in (tmp_path / "todos").iterdir()] == ["other.json"]


def test_claude_forget_project_keeps_foreign_sessions_and_odd_history(tmp_path, monkeypatch):
    """Имя проекта совпало у разных путей (кодирование с потерями): удаляем
    только свои сеансы. history.jsonl в незнакомом формате не трогаем."""
    from meet.llm import claude

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    folder = tmp_path / "rec 1"
    twin = tmp_path / "rec-1"
    project = tmp_path / "projects" / claude.project_dir_name(folder)
    assert claude.project_dir_name(folder) == claude.project_dir_name(twin)
    project.mkdir(parents=True)
    (project / f"{SID}.jsonl").write_text(json.dumps({"cwd": str(folder)}) + "\n", encoding="utf-8")
    (project / f"{OLD_SID}.jsonl").write_text(json.dumps({"cwd": str(twin)}) + "\n", encoding="utf-8")
    history = tmp_path / "history.jsonl"
    odd = b'{"project": "x"}\nnot json at all\n' + json.dumps({"project": str(folder)}).encode() + b"\n"
    history.write_bytes(odd)
    report = claude.forget_project(folder)
    assert report["project"] is False and report["sessions"] == [SID]
    assert not (project / f"{SID}.jsonl").exists() and (project / f"{OLD_SID}.jsonl").exists()
    assert report["history"] == 0 and history.read_bytes() == odd



# --- I6: новая запись в ту же минуту после остатка «Остановить без сохранения» ---


def test_new_recording_after_a_busy_discard_is_listed_and_survives_cleanup(
        res, forgotten, hooks, monkeypatch):
    """S1 упёрлось в занятый файл — остаток помечен; новая запись в ту же
    минуту ложится в свою папку (`_2`), видна в списке и переживает и
    фоновую доуборку, и уборку при запуске."""
    if os.name != "nt":
        pytest.skip("занятый файл не мешает удалению вне Windows")
    real_finish = temp_meeting.finish_later
    monkeypatch.setattr(temp_meeting, "finish_later",
                        lambda folder, **kw: real_finish(folder, **{**kw, "pause": 0.05,
                                                                    "tries": 200}))
    first = _start(res, temporary=False)
    _assistant_data(first)
    held = open(first / "assistant" / "chat.jsonl", "rb")
    try:
        res.stop_recording(discard=True)
        _wait_for(lambda: not res.live.busy())
        second = _start(res, temporary=False)
        assert second != first and second.name == f"{NAME}_2"
        assert second.name in _ids_all(res)
    finally:
        held.close()
    leftover_gone = lambda: not first.exists() or first.name.startswith(".")  # noqa: E731
    _wait_for(leftover_gone, timeout=10)
    assert (second / "sys.opus").exists() and second.name in _ids_all(res)
    res._sweep_temporary()
    assert (second / "sys.opus").exists() and second.name in _ids_all(res)
    res.stop_recording()
    assert second.is_dir() and second.name in _ids_all(res)



def test_cleanup_never_touches_the_active_folder_or_newer_files(tmp_path, monkeypatch):
    """Остаток с отметкой, в который пишут заново (папка идущей записи или
    файлы новее отметки): отметка снимается, ничего не удаляется."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    lib = tmp_path / "lib"
    active = lib / NAME
    active.mkdir(parents=True)
    (active / "mic.opus").write_bytes(b"x")
    (active / library.DISCARDED_MARK).write_text("{}", encoding="utf-8")
    newer = lib / "2026-10-07_16-00"
    newer.mkdir()
    (newer / library.DISCARDED_MARK).write_text("{}", encoding="utf-8")
    past = time.time() - 60
    os.utime(newer / library.DISCARDED_MARK, (past, past))
    (newer / "sys.opus").write_bytes(b"x")  # записан после отметки
    stale = lib / "2026-10-07_17-00"
    stale.mkdir()
    (stale / "old.opus").write_bytes(b"x")
    os.utime(stale / "old.opus", (past, past))
    (stale / library.DISCARDED_MARK).write_text("{}", encoding="utf-8")
    temp_meeting.sweep_discarded(lib, active=lambda: str(active))
    assert (active / "mic.opus").exists() and not (active / library.DISCARDED_MARK).exists()
    assert (newer / "sys.opus").exists() and not (newer / library.DISCARDED_MARK).exists()
    assert not stale.exists()  # настоящий остаток — доудалён
    # Фоновая доуборка — то же правило.
    (active / library.DISCARDED_MARK).write_text("{}", encoding="utf-8")
    temp_meeting.finish_later(active, pause=0, tries=3, active=lambda: str(active),
                              sleep=lambda s: None)
    _wait_for(lambda: not (active / library.DISCARDED_MARK).exists())
    assert (active / "mic.opus").exists()


def test_recorder_never_reuses_an_existing_folder(tmp_path):
    now = datetime(2026, 10, 7, 15, 0)
    first = recorder.new_folder(tmp_path, now=now)
    (first / library.DISCARDED_MARK).write_text("{}", encoding="utf-8")
    second = recorder.new_folder(tmp_path, now=now)
    third = recorder.new_folder(tmp_path, now=now)
    assert [first.name, second.name, third.name] == [NAME, f"{NAME}_2", f"{NAME}_3"]
    assert list(second.iterdir()) == []
    assert recorder.free_folder_name(tmp_path, now=now).name == f"{NAME}_4"


def test_history_cleanup_skip_is_logged_once(tmp_path, monkeypatch):
    from meet.llm import claude

    monkeypatch.setattr(claude, "_HISTORY_SKIP_LOGGED", False)
    history = tmp_path / "history.jsonl"
    history.write_bytes(b"not json\n")
    logged: list[str] = []
    assert claude.forget_history(history, tmp_path / "rec", log=logged.append) == 0
    assert claude.forget_history(history, tmp_path / "rec", log=logged.append) == 0
    assert len(logged) == 1 and "не трогаю" in logged[0]
