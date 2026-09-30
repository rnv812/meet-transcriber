"""Адаптер трея для control API — против настоящего объекта TrayApp.

Смысл этих тестов: снимок состояния и команды панели должны биться с полями и
методами трея, а не с их представлением в голове автора. Аудио и pystray здесь
не нужны — TrayApp собирается без них.
"""

import json
import time

import pytest

from meet import events, jobs, settings, tray, tray_control, watch


def _write_config(root, data: dict) -> None:
    path = root / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    _write_config(tmp_path, {"auto_record": {"enabled": True,
                                             "processes": ["Zoom.exe"]}})
    return tray.TrayApp()


@pytest.fixture
def control_state(app):
    return tray_control.TrayControl(app)


def test_snapshot_idle(control_state):
    snap = control_state.snapshot()
    assert snap["status"] == "idle"
    assert snap["source"] is None and snap["folder"] is None
    assert snap["elapsed_s"] == 0.0
    assert snap["levels"] == {}
    assert snap["auto_record"]["enabled"] is True
    assert snap["auto_record"]["processes"] == ["Zoom.exe"]
    assert snap["auto_record"]["grace_seconds"] == watch.GRACE_S


def test_snapshot_while_recording_reads_folder_from_lock(
    control_state, app, monkeypatch, tmp_path
):
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    (recordings / tray.LOCK_NAME).write_text(
        json.dumps({"pid": 1, "folder": str(recordings / "2026-08-18_11-00")}),
        encoding="utf-8",
    )
    monkeypatch.setattr(tray, "OUT_ROOT", recordings)
    app.recording = True
    app.source = tray_control.AUTO
    app.started = time.monotonic() - 5.0
    snap = control_state.snapshot()
    assert snap["status"] == "recording"
    assert snap["source"] == "auto"
    assert "2026-08-18_11-00" in snap["folder"]
    assert snap["elapsed_s"] >= 5.0


def test_snapshot_carries_last_levels(control_state, app):
    app.recording = True
    app.started = time.monotonic()
    app.bus.emit(events.RECORD_LEVEL, levels={"mic.opus": 0.4, "sys.opus": 0.1})
    assert control_state.snapshot()["levels"] == {"mic.opus": 0.4, "sys.opus": 0.1}


def test_levels_are_not_shown_when_idle(control_state, app):
    app.bus.emit(events.RECORD_LEVEL, levels={"mic.opus": 0.9})
    assert control_state.snapshot()["levels"] == {}


def test_snapshot_reports_signals_as_unknown_before_first_poll(control_state):
    """None у сигнала значит «ответить нечем», а не «нет» — панель обязана
    различать эти случаи."""
    auto = control_state.snapshot()["auto_record"]
    assert auto["mic"] is None and auto["render"] is None


def test_start_recording_starts_manual(control_state, app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "start_recording", lambda source: calls.append(source) or True)
    got = control_state.start_recording()
    assert got["action"] == "started"
    assert calls == [tray_control.MANUAL]


def test_start_over_auto_recording_adopts_it(control_state, app, monkeypatch):
    """Нажатие в панели поверх автозаписи — то же, что в меню трея: человек
    берёт запись под свою руку, автостоп отключается."""
    app.recording = True
    app.source = tray_control.AUTO
    app.started = time.monotonic()
    monkeypatch.setattr(app, "start_recording", lambda source: False)
    suppressed = []
    monkeypatch.setattr(app.watcher, "suppress", lambda: suppressed.append(True))
    got = control_state.start_recording()
    assert got["action"] == "adopted"
    assert app.source == tray_control.MANUAL and suppressed == [True]


def test_stop_when_not_recording_is_honest(control_state):
    got = control_state.stop_recording()
    assert got["ok"] is False and got["action"] == "not-recording"


def test_stop_calls_tray_and_reports_folder(control_state, app, monkeypatch):
    app.recording = True
    app.started = time.monotonic()
    app.result = {"folder": "C:/rec/2026-08-18_11-00"}
    stopped = []
    monkeypatch.setattr(
        app, "stop_recording",
        lambda discard=False: stopped.append(discard) or setattr(app, "recording", False),
    )
    got = control_state.stop_recording()
    assert got["action"] == "stopped" and stopped == [False]
    assert got["folder"] == "C:/rec/2026-08-18_11-00"


def test_cancel_passes_discard(control_state, app, monkeypatch):
    app.recording = True
    app.started = time.monotonic()
    stopped = []
    monkeypatch.setattr(
        app, "stop_recording",
        lambda discard=False: stopped.append(discard) or setattr(app, "recording", False),
    )
    assert control_state.stop_recording(discard=True)["action"] == "cancelled"
    assert stopped == [True]


def test_settings_roundtrip_through_api(control_state, tmp_path):
    assert control_state.settings()["version"] == settings.SCHEMA_VERSION
    got = control_state.patch_settings({"hooks": {"post_record": True}})
    assert got["settings"]["hooks"]["post_record"] is True
    assert got["restart_required"] == []  # хуки применяются на каждой остановке


def test_auto_record_patch_admits_restart_needed(control_state):
    """Секция auto_record читается один раз при старте резидента — панель
    обязана сказать это, а не делать вид, что применила."""
    got = control_state.patch_settings({"auto_record": {"grace_seconds": 60}})
    assert got["restart_required"] == ["auto_record"]


def test_diagnostics_returns_watch_log_tail(control_state, app):
    app.log("строка для диагностики")
    diag = control_state.diagnostics(lines=5)
    assert any("строка для диагностики" in line for line in diag["watch_log"])
    assert diag["dev_mode"] is True  # тесты идут из репозитория
    assert diag["paths"]["config"].endswith("config.json")


def test_diagnostics_survives_missing_logs(control_state):
    diag = control_state.diagnostics()
    assert diag["record_log"] == []  # записи нет — и файла тоже


def test_tray_log_lines_reach_the_bus(app):
    """Панель показывает решения дежурного живьём, а не вычитывает watch.log."""
    seen = []
    app.bus.subscribe(seen.append)
    app.log("сигналы: микрофон да")
    assert [e.data["text"] for e in seen] == ["сигналы: микрофон да"]
    assert seen[0].data["source"] == "watch"


def test_processes_lists_running_and_marks_selection(control_state):
    """Выбирать клиент конференции надо из реальности, а не вспоминать имя exe."""
    got = control_state.processes()
    assert got["available"] is True
    assert got["selected"] == ["Zoom.exe"]  # из конфига фикстуры
    assert any(name.lower().endswith(".exe") for name in got["running"])
    assert "Dion.exe" in got["known"]


def test_devices_says_pinning_is_not_supported(control_state):
    """Закрепить устройство нельзя осознанно: вотчдог следит за дефолтными
    endpoint'ами и переживает их смену."""
    got = control_state.devices(probe=lambda: {"available": True,
                                               "system": {"name": "Колонки"},
                                               "mic": {"name": "Микрофон"}})
    assert got["pinning"] is False and got["system"]["name"] == "Колонки"


def test_devices_never_touch_portaudio_in_process(control_state, monkeypatch):
    """IMPORTANT: перечисление устройств идёт подпроцессом. Второй PyAudio-инстанс
    в потоке HTTP-сервера рушил PortAudio у записи — резидент падал с segfault."""
    import sys

    monkeypatch.setitem(sys.modules, "pyaudiowpatch", None)  # импорт здесь = падение
    calls = []

    def probe():
        calls.append(True)
        return {"available": True, "system": {"name": "К"}, "mic": {"name": "М"}}

    assert control_state.devices(probe=probe)["available"] is True
    assert calls == [True]


def test_devices_are_cached_between_calls(control_state):
    """Открытая страница настроек не должна плодить подпроцессы пачками."""
    calls = []

    def probe():
        calls.append(True)
        return {"available": True}

    control_state.devices(probe=probe)
    control_state.devices(probe=probe)
    assert len(calls) == 1


def test_broken_probe_is_reported_not_raised(control_state):
    got = control_state.devices(probe=lambda: {"available": False, "error": "нет"})
    assert got["available"] is False and got["pinning"] is False


# --- библиотека, задачи, голоса ------------------------------------------


def _recording(root, name="2026-08-18_11-00"):
    folder = root / "recordings" / name
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"sys")
    (folder / "mic.opus").write_bytes(b"mic")
    return folder


@pytest.fixture
def with_recordings(app, tmp_path, monkeypatch):
    """Настройки указывают на нашу папку записей, задачи — на заглушку."""
    _write_config(tmp_path, {
        "auto_record": {"enabled": True, "processes": ["Zoom.exe"]},
        "recording": {"out_dir": str(tmp_path / "recordings"),
                      "voices_dir": str(tmp_path / "voices")},
    })
    app.cfg = tray._auto_config()
    return _recording(tmp_path)


def test_recordings_listing(with_recordings, app):
    state = tray_control.TrayControl(app, queue=None)
    got = state.recordings()
    assert [item["id"] for item in got["items"]] == ["2026-08-18_11-00"]
    assert got["items"][0]["has_transcript"] is False


def test_recording_detail_includes_transcript(with_recordings, app):
    from meet import library

    library.write_transcript(with_recordings, {
        "version": 1, "title": "Встреча", "segments": [
            {"start": 0.0, "end": 1.0, "speaker": "Спикер 1", "text": "привет"}
        ],
    })
    state = tray_control.TrayControl(app)
    got = state.recording("2026-08-18_11-00")
    assert got["transcript"]["segments"][0]["text"] == "привет"
    assert got["title"] == "Встреча"


def test_unknown_recording_is_reported(with_recordings, app):
    assert "error" in tray_control.TrayControl(app).recording("нет-такой")


def test_path_traversal_is_refused(with_recordings, app):
    """id приходит из сети: `..` в нём открыл бы чтение чего угодно на диске."""
    state = tray_control.TrayControl(app)
    assert "error" in state.recording("../..")
    assert state.track_path("../../windows", "sys") is None


def test_track_path_serves_only_known_tracks(with_recordings, app):
    state = tray_control.TrayControl(app)
    assert state.track_path("2026-08-18_11-00", "sys").name == "sys.opus"
    assert state.track_path("2026-08-18_11-00", "нечто") is None


def test_transcribe_puts_a_job_in_the_queue(with_recordings, app):
    submitted = []

    class FakeQueue:
        def submit(self, kind, folder, options=None):
            submitted.append((kind, folder, options))
            return jobs.Job(id="j1", kind=kind, folder=folder)

        def listing(self, limit=50):
            return []

        def cancel(self, job_id):
            return True

    state = tray_control.TrayControl(app, queue=FakeQueue())
    got = state.transcribe("2026-08-18_11-00", {"speakers": 2})
    assert got["id"] == "j1"
    assert submitted[0][0] == jobs.TRANSCRIBE
    assert submitted[0][2] == {"speakers": 2}
    assert state.cancel_job("j1") == {"ok": True}


def test_saving_transcript_requires_segments(with_recordings, app):
    state = tray_control.TrayControl(app)
    assert "error" in state.save_transcript("2026-08-18_11-00", {"нет": "полей"})
    got = state.save_transcript("2026-08-18_11-00", {"version": 1, "segments": []})
    assert got["ok"] is True


def test_naming_speakers_renames_and_enrolls(with_recordings, app, monkeypatch):
    """Названный спикер попадает в базу голосов — это и есть «обучение клона»."""
    from meet import library, voices

    library.write_transcript(with_recordings, {
        "version": 1, "segments": [
            {"start": 0.0, "end": 1.0, "speaker": "Спикер 1", "text": "раз"},
            {"start": 1.0, "end": 2.0, "speaker": "Спикер 2", "text": "два"},
        ],
    })
    enrolled = []
    monkeypatch.setattr(
        voices, "enroll",
        lambda path, mapping, folder=None: enrolled.append((path, mapping, folder)),
    )
    state = tray_control.TrayControl(app)
    got = state.name_speakers("2026-08-18_11-00", {"Спикер 1": "Демьян Петров"})
    assert got["renamed"] == 1 and got["enrolled"] == ["Демьян Петров"]
    data = library.read_transcript(with_recordings)
    assert data["segments"][0]["speaker"] == "Демьян Петров"
    assert data["segments"][1]["speaker"] == "Спикер 2"
    assert enrolled[0][1] == ["Спикер 1=Демьян Петров"]


def test_naming_survives_missing_voice_sidecar(with_recordings, app, monkeypatch):
    """Расшифровка без эмбеддингов — не ошибка: имена всё равно сохранены."""
    from meet import library, voices

    library.write_transcript(with_recordings, {
        "version": 1,
        "segments": [{"start": 0.0, "end": 1.0, "speaker": "Спикер 1", "text": "раз"}],
    })

    def no_sidecar(*a, **k):
        raise SystemExit("Не найден сайдкар")

    monkeypatch.setattr(voices, "enroll", no_sidecar)
    got = tray_control.TrayControl(app).name_speakers(
        "2026-08-18_11-00", {"Спикер 1": "Демьян"})
    assert got["ok"] is True and got["renamed"] == 1
    assert "сайдкар" in got["voices_error"]
    assert library.read_transcript(with_recordings)["segments"][0]["speaker"] == "Демьян"


def test_devices_error_is_not_cached(control_state):
    """Мгновенный сбой подпроцесса не должен залипать на 15 с — «Сбросить»
    обязано повторить попытку."""
    calls = []

    def failing(): calls.append("fail"); return {"available": False, "error": "нет"}

    control_state.devices(probe=failing)
    control_state.devices(probe=failing)
    assert len(calls) == 2  # не закэшировали ошибку

    ok = {"available": True, "system": {"name": "К"}, "mic": {"name": "М"}}
    control_state.devices(probe=lambda: ok)
    hits = []
    control_state.devices(probe=lambda: hits.append("x") or ok)
    assert hits == []  # успех закэширован


def test_gpu_busy_uses_liveness_not_existence(control_state, app, monkeypatch):
    """gpu_busy — по живости pid, а не по наличию файла (протухший маркер не
    должен показывать «GPU занят» вечно)."""
    from meet import gpu_lock
    monkeypatch.setattr(gpu_lock, "held_by_live_process", lambda: True)
    assert control_state.snapshot()["gpu_busy"] is True
    monkeypatch.setattr(gpu_lock, "held_by_live_process", lambda: False)
    assert control_state.snapshot()["gpu_busy"] is False


def test_snapshot_reports_free_disk_for_recordings(control_state, monkeypatch):
    from meet import engine

    seen = []
    monkeypatch.setattr(engine, "_free_gb", lambda path: seen.append(path) or 12.5)
    snap = control_state.snapshot()
    assert seen == [settings.load().recording.recordings]
    assert isinstance(snap["disk_free_gb"], float) and snap["disk_free_gb"] > 0


def test_update_recording_sets_title(control_state, app, monkeypatch, tmp_path):
    root = tmp_path / "recordings"
    folder = root / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    monkeypatch.setattr(control_state, "_root", lambda: root)
    result = control_state.update_recording("2026-09-30_16-04", {"title": "  Acme  "})
    assert result["title"] == "Acme"
    assert control_state.update_recording("2026-09-30_16-04", {"title": ""}) == {
        "error": "пустое название"}
    assert control_state.update_recording("../../etc", {"title": "x"}) == {
        "error": "записи нет"}


def test_update_recording_rejects_stray_folder(control_state, app, monkeypatch, tmp_path):
    """Папка есть, но это не запись (нет дорожек sys/mic) — не записываем meta.json."""
    root = tmp_path / "recordings"
    stray = root / "2026-09-30_stray"
    stray.mkdir(parents=True)
    # нет дорожек — describe вернёт None
    monkeypatch.setattr(control_state, "_root", lambda: root)
    result = control_state.update_recording("2026-09-30_stray", {"title": "Попытка"})
    assert result == {"error": "записи нет"}
    # meta.json не создался
    assert not (stray / "meta.json").exists()


def _saved_folder(tmp_path, name="2026-09-30_16-04"):
    folder = tmp_path / "recordings" / name
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    return folder


class _Queue:
    def __init__(self):
        self.submitted = []

    def submit(self, kind, folder, options=None):
        self.submitted.append((kind, folder))
        return jobs.Job(id="j1", kind=kind, folder=folder)

    def stop(self):
        pass


def test_saved_recording_is_queued_and_marked(app, tmp_path):
    queue = _Queue()
    tray_control.TrayControl(app, queue=queue)
    folder = _saved_folder(tmp_path)
    app.on_saved(str(folder), tray_control.AUTO, True)
    assert queue.submitted == [(jobs.TRANSCRIBE, str(folder))]
    assert (folder / "meta.json").exists()
    from meet import library
    assert library.read_meta(folder)["source"] == "auto"


def test_short_auto_call_is_not_transcribed(app, tmp_path):
    queue = _Queue()
    tray_control.TrayControl(app, queue=queue)
    app.on_saved(str(_saved_folder(tmp_path)), tray_control.AUTO, False)
    assert queue.submitted == []


def test_auto_transcribe_off_skips_queue(app, tmp_path, monkeypatch):
    queue = _Queue()
    tray_control.TrayControl(app, queue=queue)
    monkeypatch.setattr(settings, "load", lambda path=None: settings.Settings.from_raw(
        {"recording": {"auto_transcribe": False}}))
    app.on_saved(str(_saved_folder(tmp_path)), tray_control.MANUAL, True)
    assert queue.submitted == []


def test_discarded_recording_never_reaches_on_saved(app, tmp_path, monkeypatch):
    """Отменённая запись удалена — ставить её в очередь нельзя."""
    calls = []
    app.on_saved = lambda *a: calls.append(a)
    folder = _saved_folder(tmp_path)
    app.recording = True
    app.source = tray_control.MANUAL
    app.stop_event = __import__("threading").Event()
    app.thread = None
    app.result = {"folder": str(folder)}
    app.stop_recording(discard=True)
    assert calls == []
    assert not folder.exists()


def test_import_file_creates_recording_and_queues(app, tmp_path, monkeypatch):
    queue = _Queue()
    state = tray_control.TrayControl(app, queue=queue)
    root = tmp_path / "recordings"
    monkeypatch.setattr(state, "_root", lambda: root)
    src = tmp_path / "встреча.mp3"
    src.write_bytes(b"media")
    result = state.import_file({"path": str(src)})
    assert result["recording"].endswith("_import")
    assert queue.submitted == [(jobs.IMPORT, str(root / result["recording"]))]


def test_import_missing_or_bad_file_is_an_error(app, tmp_path, monkeypatch):
    state = tray_control.TrayControl(app, queue=_Queue())
    root = tmp_path / "recordings"
    monkeypatch.setattr(state, "_root", lambda: root)
    assert state.import_file({"path": str(tmp_path / "нет.mp3")}) == {"error": "файла нет"}
    doc = tmp_path / "notes.docx"
    doc.write_bytes(b"x")
    assert "формат" in state.import_file({"path": str(doc)})["error"]
    assert not root.exists() or not any(root.iterdir())
