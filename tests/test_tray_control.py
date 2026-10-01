"""Адаптер трея для control API — против настоящего объекта TrayApp.

Смысл этих тестов: снимок состояния и команды панели должны биться с полями и
методами трея, а не с их представлением в голове автора. Аудио и pystray здесь
не нужны — TrayApp собирается без них.
"""

import json
import time

import pytest

from meet import events, jobs, library, settings, tray, tray_control, watch


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
    assert snap["auto_record"]["grace_minutes"] == watch.GRACE_S / 60


def test_snapshot_reports_the_package_version(control_state):
    # Оболочка сверяет её со своей: резидент прежней версии после обновления
    # она штатно гасит и поднимает свой.
    import importlib.metadata
    try:
        expected = importlib.metadata.version("meet-transcriber")
    except importlib.metadata.PackageNotFoundError:
        expected = None
    assert control_state.snapshot()["version"] == expected


def test_version_is_none_without_the_package(monkeypatch):
    import importlib.metadata

    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    tray_control.app_version.cache_clear()
    monkeypatch.setattr(importlib.metadata, "version", missing)
    try:
        assert tray_control.app_version() is None
    finally:
        tray_control.app_version.cache_clear()


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


def test_snapshot_reports_last_stop(control_state, app):
    """Ключ есть всегда (None до первой остановки): по его наличию оболочка
    отличает резидент, который сообщает причину, от старого."""
    snap = control_state.snapshot()
    assert "last_stop" in snap and snap["last_stop"] is None
    app.last_stop = {"folder": "C:/rec/a", "reason": "discarded", "at": 1.0}
    snap = control_state.snapshot()
    assert snap["last_stop"] == {"folder": "C:/rec/a", "reason": "discarded", "at": 1.0}
    snap["last_stop"]["reason"] = "saved"
    assert app.last_stop["reason"] == "discarded"  # снимок — копия


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
    got = control_state.patch_settings({"auto_record": {"grace_minutes": 5}})
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


def test_devices_says_pinning_is_supported(control_state):
    """Микрофон и вывод можно выбрать по имени (recording.mic_device /
    output_device); null — системные, за которыми запись следит."""
    got = control_state.devices(probe=lambda: {"available": True,
                                               "system": {"name": "Колонки"},
                                               "mic": {"name": "Микрофон"},
                                               "inputs": [], "outputs": []})
    assert got["pinning"] is True and got["system"]["name"] == "Колонки"


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
    assert got["available"] is False


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


def test_track_path_playback_is_the_mix_of_both_sides(with_recordings, app, monkeypatch):
    """Плеер карточки играет `playback` — сведённые sys+mic, а не дорожку по имени спикера."""
    from meet import playback

    made = []

    def fake_playback(folder):
        made.append(folder)
        return folder / playback.PLAYBACK_NAME

    monkeypatch.setattr(playback, "playback_path", fake_playback)
    state = tray_control.TrayControl(app)
    got = state.track_path("2026-08-18_11-00", "playback")
    assert got == with_recordings.resolve() / playback.PLAYBACK_NAME
    assert made == [with_recordings.resolve()]
    assert state.track_path("../../windows", "playback") is None


def test_track_path_playback_failure_is_unavailable(with_recordings, app, monkeypatch):
    from meet import playback
    from meet.control import Unavailable

    def broken(folder):
        raise RuntimeError("ffmpeg не найден")

    monkeypatch.setattr(playback, "playback_path", broken)
    with pytest.raises(Unavailable, match="ffmpeg"):
        tray_control.TrayControl(app).track_path("2026-08-18_11-00", "playback")


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

        def active_for(self, folder, kinds):
            return None

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


def test_export_sanitizes_filename(with_recordings, app, monkeypatch):
    """Export должен безопасно создавать имена файлов: заменять опасные
    символы, использовать recording_id как fallback при пустом названии."""
    from meet import library

    # Setup: recording with meta.json title that has unsafe characters
    library.write_meta(with_recordings, {"title": "Итоги: 1/2"})
    library.write_transcript(with_recordings, {
        "version": 1,
        "segments": [
            {"start": 0.0, "end": 1.0, "speaker": "Спикер", "text": "Привет"},
        ],
    })
    monkeypatch.setattr(app, "cfg", tray._auto_config())

    state = tray_control.TrayControl(app)
    result = state.export("2026-08-18_11-00", "txt")

    # Filename should have unsafe chars replaced with underscore
    assert result["filename"] == "Итоги_ 1_2.txt"
    # Content should contain the segment text
    assert "Привет" in result["content"]


def test_export_of_old_transcript_uses_display_names(control_state, tmp_path,
                                                     monkeypatch):
    """Старый транскрипт хранит сырые SPEAKER_XX — экспорт показывает их так
    же, как редактор: «Спикер N»."""
    from meet import library

    folder = _saved_folder(tmp_path)
    library.write_transcript(folder, {"segments": [
        {"start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "а"},
        {"start": 1, "end": 2, "speaker": "SPEAKER_01", "text": "б"}]})
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    content = control_state.export(folder.name, "txt")["content"]
    assert "Спикер 1: а" in content and "Спикер 2: б" in content
    assert "SPEAKER_" not in content


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
    assert control_state.update_recording("2026-09-30_16-04", {"title": "я" * 300})["title"] == "я" * 200
    # Пустое название — вернуть автоматическое (из транскрипта или даты).
    reverted = control_state.update_recording("2026-09-30_16-04", {"title": "  "})
    assert reverted["title"] is None
    assert "title" not in library.read_meta(folder)
    assert control_state.update_recording("2026-09-30_16-04", {"title": None})["title"] is None
    assert control_state.update_recording("../../etc", {"title": "x"}) == {
        "error": "записи нет"}


def test_search_finds_text_across_recordings(control_state, monkeypatch, tmp_path):
    from meet import search

    search.clear_cache()
    root = tmp_path / "recordings"
    folder = root / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 3.0, "end": 4.0, "speaker": "Анна", "text": "Бюджет утвердили."}]})
    monkeypatch.setattr(control_state, "_root", lambda: root)
    got = control_state.search("бюджета")
    assert [i["id"] for i in got["items"]] == ["2026-09-30_16-04"]
    assert got["items"][0]["hits"][0]["ranges"] == [[0, 6]]
    assert control_state.search("") == {"items": []}


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

    def active_for(self, folder, kinds):
        return None

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


@pytest.fixture
def voices_state(control_state, tmp_path, monkeypatch):
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / "Демьян.json").write_text('{"samples": []}', encoding="utf-8")
    monkeypatch.setattr(control_state, "_voices", lambda: voices)
    return control_state, voices


def _png_bytes():
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


def test_set_avatar_not_an_image_is_bad_request(voices_state):
    from meet import control

    state, voices = voices_state
    with pytest.raises(control.BadRequest, match="не изображение"):
        state.set_avatar("Демьян", b"not an image")
    assert sorted(p.name for p in voices.iterdir()) == ["Демьян.json"]


def test_rename_to_path_trick_is_bad_request(voices_state):
    from meet import control

    state, _ = voices_state
    with pytest.raises(control.BadRequest):
        state.person_action("Демьян", "rename", {"to": "a/b"})


def test_merge_into_self_is_bad_request(voices_state):
    from meet import control

    state, voices = voices_state
    with pytest.raises(control.BadRequest):
        state.person_action("Демьян", "merge", {"into": "Демьян"})
    assert (voices / "Демьян.json").exists()


def test_set_avatar_unknown_person(voices_state):
    state, _ = voices_state
    assert state.set_avatar("Никто", _png_bytes()) == {"error": "человека нет"}


def test_shutdown_saves_recording_and_requests_exit(control_state, app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "stop_recording", lambda discard=False, hook=True:
                        calls.append(("stop", discard)))
    monkeypatch.setattr(app, "request_exit", lambda: calls.append("exit"))
    app.recording = True
    assert control_state.shutdown() == {"ok": True}
    assert calls == [("stop", False), "exit"]


def test_state_endpoint_survives_unavailable_recordings_drive(app, monkeypatch, tmp_path):
    """Папка записей на отключённом диске: /state отвечает 200 с
    disk_free_gb = null, а не рвёт соединение (OSError из disk_usage глотался
    веткой «клиент ушёл», и панель оставалась без состояния)."""
    import urllib.request

    from meet import control, engine

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))

    def unavailable(path):
        raise FileNotFoundError(2, "Не удаётся найти указанный путь", str(path))

    monkeypatch.setattr(engine.shutil, "disk_usage", unavailable)
    srv = control.ControlServer(tray_control.TrayControl(app, queue=_Queue()))
    srv.start(publish=False)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/state",
            headers={"Authorization": f"Bearer {srv.token}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
            body = json.loads(r.read().decode("utf-8"))
    finally:
        srv.stop()
    assert body["disk_free_gb"] is None and body["status"] == "idle"


def test_naming_speakers_rejects_unsafe_names(with_recordings, app, tmp_path):
    """Имя уходит в имя файла базы голосов: `..\\..\\x` писал бы мимо папки
    голосов. Отказ — до любых изменений: ни транскрипт, ни база не тронуты."""
    from meet import control, library

    original = {"version": 1, "segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Спикер 1", "text": "раз"}]}
    library.write_transcript(with_recordings, original)
    (with_recordings / "2026-08-18_11-00_speakers.json").write_text(json.dumps({
        "model": "m", "source": str(with_recordings), "date": "2026-08-18",
        "speakers": [{"label": "SPEAKER_00", "display": "Спикер 1",
                      "embedding": [1.0, 0.0]}]}, ensure_ascii=False), encoding="utf-8")
    before = sorted(p.name for p in tmp_path.rglob("*"))
    state = tray_control.TrayControl(app)
    for bad in (r"..\..\x", "Демьян: ПМ", "x" * 81):
        with pytest.raises(control.BadRequest):
            state.name_speakers("2026-08-18_11-00", {"Спикер 1": bad})
    assert library.read_transcript(with_recordings) == original
    assert sorted(p.name for p in tmp_path.rglob("*")) == before
    assert not (tmp_path.parent / "x.json").exists()  # мимо папки голосов


def test_person_rename_and_merge_rewrite_library_transcripts(voices_state, monkeypatch, tmp_path):
    """Статистика людей считается по транскриптам библиотеки — rename/merge из
    окна обязаны переписать имя и там, иначе у человека обнулится речь."""
    from meet import library

    state, voices = voices_state
    root = tmp_path / "recordings"
    folder = _recording(tmp_path)
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 4.0, "speaker": "Демьян", "text": "раз"},
        {"start": 4.0, "end": 6.0, "speaker": "Пётр", "text": "два"}]})
    (voices / "Пётр.json").write_text('{"samples": []}', encoding="utf-8")
    monkeypatch.setattr(state, "_root", lambda: root)
    assert state.person_action("Демьян", "rename", {"to": "Демьян Петров"}) == {"ok": True}
    assert state.person_action("Пётр", "merge", {"into": "Демьян Петров"}) == {"ok": True}
    speakers = [s["speaker"] for s in library.read_transcript(folder)["segments"]]
    assert speakers == ["Демьян Петров", "Демьян Петров"]
    items = state.people()["items"]
    assert [(p["name"], p["meetings"], p["seconds"]) for p in items] == [("Демьян Петров", 1, 6)]



def _blocked_queue(app):
    """Настоящая очередь, чья задача «идёт», пока её не отпустят."""
    import threading

    release = threading.Event()

    def spawn(job, on_line):
        release.wait(timeout=5)
        return 0

    return jobs.JobQueue(app.bus, spawn=spawn), release


def test_transcribe_does_not_queue_duplicates(with_recordings, app):
    queue, release = _blocked_queue(app)
    state = tray_control.TrayControl(app, queue=queue)
    try:
        first = state.transcribe("2026-08-18_11-00")
        second = state.transcribe("2026-08-18_11-00")
        assert second["id"] == first["id"]
        assert len(queue.listing()) == 1
    finally:
        release.set()
        queue.stop()


def test_saved_recording_already_in_queue_is_not_queued_again(with_recordings, app):
    queue, release = _blocked_queue(app)
    state = tray_control.TrayControl(app, queue=queue)
    try:
        state.transcribe("2026-08-18_11-00")
        app.on_saved(str(with_recordings), tray_control.MANUAL, True)
        assert len(queue.listing()) == 1
    finally:
        release.set()
        queue.stop()


def test_transcribe_of_trackless_import_retries_the_import(with_recordings, app, tmp_path):
    """Импорт упал до копии (файл был недоступен) — «Расшифровать» повторяет
    импорт целиком: без дорожки расшифровывать нечего."""
    from meet import library

    folder = tmp_path / "recordings" / "2026-09-01_10-00_import"
    folder.mkdir()
    library.write_meta(folder, {"source": "import", "original_path": str(tmp_path / "a.mp3")})
    queue = _Queue()
    state = tray_control.TrayControl(app, queue=queue)
    state.transcribe(folder.name)
    assert queue.submitted == [(jobs.IMPORT, str(folder.resolve()))]
    (folder / "source.mp3").write_bytes(b"x")
    state.transcribe(folder.name)
    assert queue.submitted[-1] == (jobs.TRANSCRIBE, str(folder.resolve()))


def test_set_avatar_save_failure_is_an_error_not_a_dropped_reply(voices_state, monkeypatch):
    """OSError из обработчика control API глотается как «клиент ушёл» — ответа
    не было бы вовсе. Сбой записи файла должен дойти до панели ошибкой."""
    from meet import people

    state, _ = voices_state

    def full_disk(*a, **k):
        raise OSError(28, "На диске недостаточно места")

    monkeypatch.setattr(people, "set_avatar", full_disk)
    with pytest.raises(RuntimeError, match="не удалось сохранить"):
        state.set_avatar("Демьян", _png_bytes())


def test_saved_recording_is_queued_even_if_meta_cannot_be_written(app, tmp_path, monkeypatch):
    """meta.json — пометка «откуда запись», расшифровка важнее: сбой записи
    метаданных (антивирус держит файл, диск отвалился) не должен её отменять."""
    from meet import library

    def locked(folder, updates):
        raise PermissionError(13, "Отказано в доступе", str(folder / "meta.json"))

    monkeypatch.setattr(library, "write_meta", locked)
    queue = _Queue()
    tray_control.TrayControl(app, queue=queue)
    folder = _saved_folder(tmp_path)
    app.on_saved(str(folder), tray_control.AUTO, True)
    assert queue.submitted == [(jobs.TRANSCRIBE, str(folder))]


def test_auto_record_toggle_applies_live_and_persists(control_state, app):
    assert app.cfg["enabled"] is True
    snap = control_state.set_auto_record({"enabled": False})
    assert snap["auto_record"]["enabled"] is False
    assert app.cfg["enabled"] is False
    assert settings.load().auto_record.enabled is False


def test_auto_record_toggle_keeps_live_state_when_saving_fails(control_state, app,
                                                               monkeypatch):
    """Не сохранилось — не применяем и вживую: иначе трей и config.json
    разошлись бы, и после перезапуска автозапись «сама» включилась бы обратно."""
    def broken(updates):
        raise OSError("диск только для чтения")

    monkeypatch.setattr(settings, "patch", broken)
    with pytest.raises(OSError):
        control_state.set_auto_record({"enabled": False})
    assert app.cfg["enabled"] is True


def test_auto_record_toggle_needs_bool(control_state):
    import pytest
    from meet import control
    with pytest.raises(control.BadRequest):
        control_state.set_auto_record({"enabled": "да"})


def test_delete_recording_removes_folder(control_state, tmp_path, monkeypatch):
    folder = _saved_folder(tmp_path)
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    assert control_state.delete_recording(folder.name) == {"ok": True}
    assert not folder.exists()


def test_delete_waits_for_the_player_to_release_the_files(control_state, tmp_path, monkeypatch):
    """Плеер открытой карточки держит playback.opus: удаление ждёт, пока его
    отпустят (на Windows открытый файл не удалить — запись снесло бы наполовину)."""
    import threading
    import time as _time

    from meet import playback

    folder = _saved_folder(tmp_path)
    (folder / playback.PLAYBACK_NAME).write_bytes(b"mixed")
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    released = []
    held = threading.Event()

    def stream():
        with playback.using(folder):
            held.set()
            _time.sleep(0.3)
            released.append(_time.monotonic())

    threading.Thread(target=stream).start()
    assert held.wait(2)
    assert control_state.delete_recording(folder.name) == {"ok": True}
    assert released, "удаление началось, пока поток плеера держал файл"
    assert not folder.exists()


def test_delete_does_not_wait_forever(control_state, tmp_path, monkeypatch):
    from meet import playback

    folder = _saved_folder(tmp_path)
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    waited = []
    monkeypatch.setattr(playback, "wait_idle", lambda f, timeout: waited.append(timeout) or False)
    assert control_state.delete_recording(folder.name) == {"ok": True}
    assert waited == [tray_control.DELETE_WAIT_S] and tray_control.DELETE_WAIT_S <= 3


def test_delete_unknown_recording(control_state, tmp_path, monkeypatch):
    folder = _saved_folder(tmp_path)
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    assert control_state.delete_recording("nope") == {"error": "записи нет"}


@pytest.mark.parametrize("bad_id", ["..", "../x", "../recordings", "{absolute}"])
def test_delete_never_escapes_the_recordings_root(control_state, tmp_path,
                                                  monkeypatch, bad_id):
    """id приходит из сети: `..` или абсолютный путь в нём снёс бы с диска
    родителя папки записей или что угодно ещё."""
    folder = _saved_folder(tmp_path)
    root = folder.parent
    outside = tmp_path / "x"
    outside.mkdir()
    (outside / "keep.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(control_state, "_root", lambda: root)
    bad_id = bad_id.replace("{absolute}", str(outside))
    assert control_state.delete_recording(bad_id) == {"error": "записи нет"}
    assert root.is_dir() and folder.is_dir()
    assert (outside / "keep.txt").is_file()
    assert tmp_path.is_dir()


def test_delete_refuses_current_recording(app, tmp_path, monkeypatch):
    from meet import control

    state = tray_control.TrayControl(app)
    folder = _saved_folder(tmp_path)
    monkeypatch.setattr(state, "_root", lambda: folder.parent)
    monkeypatch.setattr(app, "recording", True)
    monkeypatch.setattr(app, "_current_folder", lambda: str(folder))
    with pytest.raises(control.BadRequest, match="запись ещё идёт"):
        state.delete_recording(folder.name)
    assert folder.exists()


def test_delete_refuses_while_transcribing(app, tmp_path, monkeypatch):
    from meet import control
    queue = jobs.JobQueue(spawn=lambda job, on_line: __import__("time").sleep(1) or 0)
    state = tray_control.TrayControl(app, queue=queue)
    folder = _saved_folder(tmp_path)
    monkeypatch.setattr(state, "_root", lambda: folder.parent)
    state.transcribe(folder.name, {})
    with pytest.raises(control.BadRequest, match="расшифровка"):
        state.delete_recording(folder.name)
    assert folder.exists()
    queue.stop()


def test_hotwords_roundtrip(control_state, monkeypatch, tmp_path):
    from meet import paths
    target = tmp_path / "hotwords.txt"
    monkeypatch.setattr(paths, "hotwords_path", lambda: target)
    assert control_state.get_hotwords() == {"text": "", "budget": 400, "used": 0}
    reply = control_state.put_hotwords({"text": "SIEM\nSOC\n# комментарий\nCMDB"})
    assert target.read_text(encoding="utf-8").startswith("SIEM")
    assert reply == {"text": "SIEM\nSOC\n# комментарий\nCMDB", "budget": 400,
                     "used": len("SIEM, SOC, CMDB")}
    assert control_state.get_hotwords() == reply


def test_hotwords_budget_and_count_match_what_transcription_uses(control_state,
                                                                  monkeypatch, tmp_path):
    """Счётчик в настройках обязан совпадать с тем, что реально уйдёт в
    распознавание: тот же бюджет и тот же дедуп повторов."""
    from meet import paths, transcribe

    target = tmp_path / "hotwords.txt"
    monkeypatch.setattr(paths, "hotwords_path", lambda: target)
    text = "SIEM\nSOC\nSIEM  # повтор\nCMDB\nSOC"
    reply = control_state.put_hotwords({"text": text})
    assert reply["budget"] == transcribe.HOTWORDS_CHAR_BUDGET
    assert reply["used"] == len(transcribe._load_hotwords(None, target))
    assert reply["used"] == len("SIEM, SOC, CMDB")


def test_resident_import_stays_light():
    """Список слов делит бюджет с расшифровкой, но резидент не тянет её
    модуль (numpy, ASR): минимальная установка для записи их не содержит."""
    import subprocess
    import sys

    code = ("import sys, meet.tray, meet.tray_control; "
            "print('meet.transcribe' in sys.modules, 'numpy' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True)
    assert out.stdout.split() == ["False", "False"]


def test_hotwords_rejects_non_text(control_state, monkeypatch, tmp_path):
    from meet import control, paths
    monkeypatch.setattr(paths, "hotwords_path", lambda: tmp_path / "h.txt")
    with pytest.raises(control.BadRequest):
        control_state.put_hotwords({"text": 5})


def test_recordings_query_filters(control_state, tmp_path, monkeypatch):
    folder = _saved_folder(tmp_path)
    from meet import library
    library.write_transcript(folder, {"title": "Созвон", "segments": []})
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    assert len(control_state.recordings(q="созв")["items"]) == 1
    assert control_state.recordings(q="zzz")["items"] == []


def test_person_card_unknown(control_state):
    assert control_state.person("Никто") == {"error": "человека нет"}


def test_old_transcript_is_served_with_display_names_and_can_be_named(control_state,
                                                                      tmp_path, monkeypatch):
    from meet import library

    folder = _saved_folder(tmp_path)
    library.write_transcript(folder, {"segments": [
        {"start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "а"},
        {"start": 1, "end": 2, "speaker": "SPEAKER_01", "text": "б"}]})
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    monkeypatch.setattr(control_state, "_enroll", lambda f, pairs: ([], None))
    served = control_state.recording(folder.name)["transcript"]["segments"]
    assert [s["speaker"] for s in served] == ["Спикер 1", "Спикер 2"]
    assert [s["speaker"] for s in control_state.transcript(folder.name)["segments"]] == [
        "Спикер 1", "Спикер 2"]
    control_state.name_speakers(folder.name, {"Спикер 2": "Матвей"})
    stored = library.read_transcript(folder)["segments"]
    assert [s["speaker"] for s in stored] == ["Спикер 1", "Матвей"]


def test_name_speakers_accepts_raw_label_keys(control_state, tmp_path, monkeypatch):
    from meet import library

    folder = _saved_folder(tmp_path)
    library.write_transcript(folder, {"segments": [
        {"start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "а"},
        {"start": 1, "end": 2, "speaker": "SPEAKER_01", "text": "б"}]})
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    seen = {}
    monkeypatch.setattr(control_state, "_enroll",
                        lambda f, pairs: (seen.update(pairs) or [], None))
    control_state.name_speakers(folder.name, {"SPEAKER_01": "Матвей"})
    stored = library.read_transcript(folder)["segments"]
    assert [s["speaker"] for s in stored] == ["Спикер 1", "Матвей"]
    assert seen == {"Спикер 2": "Матвей"}


# --- токен Hugging Face ------------------------------------------------------

HF = "hf_TRAY_secret_456"
OK = {"ok": True, "reason": "ok", "message": "Доступ есть"}
BAD = {"ok": False, "reason": "invalid_token", "message": "Неверный токен"}


def test_hf_status_without_token(control_state):
    assert control_state.hf_status() == {"configured": False, "source": None, "check": None}


def test_hf_token_saved_only_when_check_passes(control_state, monkeypatch, memory_keyring):
    from meet import models

    seen = []
    monkeypatch.setattr(models, "check_hf_access",
                        lambda token, **kw: seen.append(token) or BAD)
    assert control_state.set_hf_token({"token": f" {HF} "}) == BAD
    assert seen == [HF]
    assert memory_keyring.store == {}
    # кэш — о сохранённом токене; неудачная проверка нового его не меняет
    assert control_state.hf_status() == {"configured": False, "source": None, "check": None}

    monkeypatch.setattr(models, "check_hf_access", lambda token, **kw: OK)
    assert control_state.set_hf_token({"token": HF}) == OK
    assert memory_keyring.store[("meet", "huggingface")] == HF
    status = control_state.hf_status()
    assert status == {"configured": True, "source": "keyring", "check": OK}
    assert HF not in json.dumps(status)


def test_hf_token_empty_is_bad_request(control_state):
    from meet import control

    for body in ({}, {"token": ""}, {"token": "   "}, {"token": 5}, None):
        with pytest.raises(control.BadRequest):
            control_state.set_hf_token(body)


def test_hf_token_delete(control_state, monkeypatch, memory_keyring):
    from meet import models

    monkeypatch.setattr(models, "check_hf_access", lambda token, **kw: OK)
    control_state.set_hf_token({"token": HF})
    assert control_state.clear_hf_token() == {
        "configured": False, "source": None, "check": None}
    assert memory_keyring.store == {}


def test_hf_check_rechecks_stored_token(control_state, monkeypatch):
    from meet import control, credentials, models

    with pytest.raises(control.BadRequest):
        control_state.check_hf_token()
    credentials.set_hf_token(HF)
    seen = []
    monkeypatch.setattr(models, "check_hf_access",
                        lambda token, **kw: seen.append(token) or OK)
    assert control_state.check_hf_token() == OK
    assert seen == [HF]
    assert control_state.hf_status()["check"] == OK


def test_hf_routes_over_http(app, monkeypatch, memory_keyring):
    import urllib.error
    import urllib.request

    from meet import control, models

    monkeypatch.setattr(models, "check_hf_access", lambda token, **kw: OK)
    srv = control.ControlServer(tray_control.TrayControl(app, queue=_Queue()))
    srv.start(publish=False)

    def call(method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {srv.token}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    try:
        assert call("GET", "/hf/status") == (
            200, {"configured": False, "source": None, "check": None})
        assert call("POST", "/hf/token", {"token": ""})[0] == 400
        assert call("POST", "/hf/token", {"token": HF}) == (200, OK)
        status = call("GET", "/hf/status")
        assert status == (200, {"configured": True, "source": "keyring", "check": OK})
        assert call("POST", "/hf/check", {}) == (200, OK)
        assert call("DELETE", "/hf/token")[1]["configured"] is False
    finally:
        srv.stop()
    assert memory_keyring.store == {}


def test_hf_check_cache_resets_when_token_changes_elsewhere(control_state, monkeypatch):
    """PATCH /settings с новым токеном (прежнее окно) — старая проверка уже
    не о нём; неудачный POST нового токена не трогает кэш сохранённого."""
    from meet import models

    monkeypatch.setattr(models, "check_hf_access", lambda token, **kw: OK)
    control_state.set_hf_token({"token": HF})
    assert control_state.hf_status()["check"] == OK
    monkeypatch.setattr(models, "check_hf_access", lambda token, **kw: BAD)
    control_state.set_hf_token({"token": "hf_other"})
    assert control_state.hf_status()["check"] == OK
    control_state.patch_settings({"integrations": {"hf_token": ""}})  # пустое — не смена
    assert control_state.hf_status()["check"] == OK
    control_state.patch_settings({"integrations": {"hf_token": "hf_new"}})
    assert control_state.hf_status() == {"configured": True, "source": "keyring",
                                         "check": None}
    control_state.clear_hf_token()
    assert control_state.hf_status()["check"] is None


# --- проверка устройства и «выбранного нет» ---------------------------------


def test_device_test_runs_probe_in_subprocess_helper(control_state):
    calls = []

    def probe(kind, name):
        calls.append((kind, name))
        return {"ok": True, "peak": 0.42, "device": name or "Микрофон", "fallback": False}

    got = control_state.test_device({"kind": "mic", "name": "USB-микрофон"}, probe=probe)
    assert got["peak"] == 0.42 and got["device"] == "USB-микрофон"
    assert calls == [("mic", "USB-микрофон")]
    got = control_state.test_device({"kind": "output", "name": None}, probe=probe)
    assert calls[-1] == ("output", None)


def test_device_test_refused_while_recording(control_state, app):
    from meet.control import Conflict

    app.recording = True
    with pytest.raises(Conflict, match="Идёт запись"):
        control_state.test_device({"kind": "mic", "name": None},
                                  probe=lambda k, n: pytest.fail("не должен звать"))


def test_device_test_refused_while_live(control_state, monkeypatch):
    from meet.control import Conflict

    monkeypatch.setattr(control_state.live, "busy", lambda: True)
    with pytest.raises(Conflict):
        control_state.test_device({"kind": "output", "name": None},
                                  probe=lambda k, n: pytest.fail("не должен звать"))


@pytest.mark.parametrize("body", [None, {}, {"kind": "камера"}, {"kind": "mic", "name": 5}])
def test_device_test_rejects_bad_body(control_state, body):
    from meet.control import BadRequest

    with pytest.raises(BadRequest):
        control_state.test_device(body, probe=lambda k, n: pytest.fail("не должен звать"))


def test_device_test_failure_is_a_readable_unavailable_error(control_state):
    """Сбой проверки — не ошибка запроса (400), а недоступность (503)."""
    from meet.control import Unavailable

    with pytest.raises(Unavailable, match="Не удалось проверить устройство: занято"):
        control_state.test_device({"kind": "mic", "name": None},
                                  probe=lambda k, n: {"ok": False, "error": "занято"})


def test_second_concurrent_device_test_is_refused(control_state):
    """Две проверки разом (двойной клик) — вторая 409, первая доходит."""
    import threading

    from meet.control import Conflict

    entered, release = threading.Event(), threading.Event()
    results: list = []

    def slow(kind, name):
        entered.set()
        release.wait(5)
        return {"ok": True, "peak": 0.1, "device": "Микрофон"}

    first = threading.Thread(target=lambda: results.append(
        control_state.test_device({"kind": "mic", "name": None}, probe=slow)))
    first.start()
    assert entered.wait(5)
    with pytest.raises(Conflict, match="Проверка устройства уже идёт"):
        control_state.test_device({"kind": "mic", "name": None}, probe=slow)
    release.set()
    first.join(5)
    assert results and results[0]["device"] == "Микрофон"
    # после первой — снова можно
    got = control_state.test_device({"kind": "mic", "name": None},
                                    probe=lambda k, n: {"ok": True, "peak": 0.0,
                                                        "device": "Микрофон"})
    assert got["ok"] is True


def _fake_run(monkeypatch, result=None, raises=None):
    import subprocess

    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(args, 0, stdout=result, stderr="")

    monkeypatch.setattr(tray_control.subprocess, "run", run)
    return calls


def test_check_device_runs_probe_subprocess_with_args(monkeypatch):
    # ASCII-JSON (ensure_ascii) читается при любой кодировке stdout ребёнка
    calls = _fake_run(monkeypatch, '{"ok": true, "peak": 0.3, "device": "USB-\\u043c"}\n')
    got = tray_control._check_device("mic", "USB-микрофон")
    assert got == {"ok": True, "peak": 0.3, "device": "USB-м"}
    args, kw = calls[0]
    assert args[1:3] == ["-m", "meet.devices_probe"]
    assert args[3:] == ["--check", "mic", "--seconds", "2.0", "--name", "USB-микрофон"]
    assert kw["env"]["PYTHONIOENCODING"] == "utf-8"
    assert kw["timeout"] > tray_control.DEVICE_CHECK_S
    tray_control._check_device("output", None)
    assert "--name" not in calls[1][0]


def test_probe_timeout_says_device_did_not_answer(monkeypatch):
    import subprocess

    _fake_run(monkeypatch, raises=subprocess.TimeoutExpired(["python", "-m", "secret"], 22))
    got = tray_control._check_device("mic", None)
    assert got == {"ok": False, "error": "Устройство не ответило"}


def test_probe_failure_shows_only_exception_type(monkeypatch):
    _fake_run(monkeypatch, raises=OSError("C:/путь/python.exe -m meet.devices_probe"))
    got = tray_control._probe_devices()
    assert got == {"available": False, "error": "OSError"}


def test_probe_list_gets_utf8_env_too(monkeypatch):
    calls = _fake_run(monkeypatch, '{"available": true}')
    tray_control._probe_devices()
    assert calls[0][1]["env"]["PYTHONIOENCODING"] == "utf-8"


def test_device_test_never_touches_portaudio_in_process(control_state, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "pyaudiowpatch", None)
    got = control_state.test_device({"kind": "mic", "name": None},
                                    probe=lambda k, n: {"ok": True, "peak": 0.0,
                                                        "device": "Микрофон"})
    assert got["device"] == "Микрофон"


def test_snapshot_reports_device_fallback_while_recording(control_state, app):
    assert control_state.snapshot()["devices_fallback"] == []
    app.recording = True
    control_state.bus.emit(events.RECORD_DEVICE_FALLBACK, track="mic.opus", role="mic",
                           wanted="USB-микрофон", device="Микрофон")
    assert control_state.snapshot()["devices_fallback"] == [
        {"kind": "mic", "name": "USB-микрофон", "device": "Микрофон"}]
    control_state.bus.emit(events.RECORD_STOPPED, folder="x", duration_s=1.0)
    assert control_state.snapshot()["devices_fallback"] == []


def test_snapshot_fallback_clears_when_device_returns(control_state, app):
    app.recording = True
    for role, wanted in (("mic", "USB-микрофон"), ("output", "Наушники")):
        control_state.bus.emit(events.RECORD_DEVICE_FALLBACK, track="x", role=role,
                               wanted=wanted, device="системное")
    assert [f["kind"] for f in control_state.snapshot()["devices_fallback"]] == ["mic", "output"]
    control_state.bus.emit(events.RECORD_DEVICE_PINNED, track="mic.opus", role="mic",
                           wanted="USB-микрофон", device="USB-микрофон")
    assert control_state.snapshot()["devices_fallback"] == [
        {"kind": "output", "name": "Наушники", "device": "системное"}]
    # пропал снова — снова в снимке, без дублей
    for _ in range(2):
        control_state.bus.emit(events.RECORD_DEVICE_FALLBACK, track="x", role="mic",
                               wanted="USB-микрофон", device="системное")
    assert [f["kind"] for f in control_state.snapshot()["devices_fallback"]] == ["output", "mic"]


def test_snapshot_reports_live_mode_fallback(control_state, monkeypatch):
    fallback = [{"kind": "mic", "name": "USB-микрофон", "device": "Микрофон"}]
    monkeypatch.setattr(control_state.live, "devices_fallback", lambda: fallback)
    assert control_state.snapshot()["devices_fallback"] == fallback


def test_browser_auto_recording_gets_the_call_title(app, tmp_path):
    tray_control.TrayControl(app, queue=_Queue())
    folder = _saved_folder(tmp_path)
    app.recording_title = "Dion — Планёрка отдела"
    app.on_saved(str(folder), tray_control.AUTO, True)
    assert library.read_meta(folder)["title"] == "Dion — Планёрка отдела"


def test_call_title_never_overwrites_a_rename(app, tmp_path):
    tray_control.TrayControl(app, queue=_Queue())
    folder = _saved_folder(tmp_path)
    library.write_meta(folder, {"title": "Своё название"})
    app.recording_title = "Dion — Планёрка отдела"
    app.on_saved(str(folder), tray_control.AUTO, True)
    assert library.read_meta(folder)["title"] == "Своё название"


def test_manual_recording_ignores_call_title(app, tmp_path):
    tray_control.TrayControl(app, queue=_Queue())
    folder = _saved_folder(tmp_path)
    app.recording_title = "Dion — Планёрка отдела"
    app.on_saved(str(folder), tray_control.MANUAL, True)
    assert "title" not in library.read_meta(folder)


def test_snapshot_shows_browsers_and_browser_call(control_state, app):
    app.signals.browser_call = {"exe": "chrome.exe", "site": "Dion", "title": "Dion — Встреча"}
    snap = control_state.snapshot()["auto_record"]
    assert snap["browsers"] == []
    assert snap["browser"] == {"exe": "chrome.exe", "site": "Dion"}


def test_auto_recording_after_the_wait_is_trimmed_before_transcription(app, tmp_path, monkeypatch):
    import json as _json

    from meet import tail

    queue = _Queue()
    state = tray_control.TrayControl(app, queue=queue)
    state._background = lambda fn: fn()
    folder = _saved_folder(tmp_path)
    (folder / "events.jsonl").write_text(_json.dumps({"kind": "record.started", "at": 1000.0}) + "\n",
                                         encoding="utf-8")
    order = []
    monkeypatch.setattr(tail, "trim", lambda path: order.append(("trim", queue.submitted[:])) or 330.0)
    app.call_end_at = 1300.0
    app.on_saved(str(folder), tray_control.AUTO, True)
    assert order == [("trim", [])]  # обрезка — до постановки расшифровки
    assert queue.submitted == [(jobs.TRANSCRIBE, str(folder))]
    events = [_json.loads(x) for x in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[-1] == {"kind": "record.call_end", "at": 1300.0, "wait_s": 600.0}


def test_failed_trim_still_transcribes(app, tmp_path, monkeypatch):
    from meet import tail

    queue = _Queue()
    state = tray_control.TrayControl(app, queue=queue)
    state._background = lambda fn: fn()
    folder = _saved_folder(tmp_path)

    def broken(path):
        raise RuntimeError("ffmpeg не обрезал sys.opus")

    monkeypatch.setattr(tail, "trim", broken)
    app.call_end_at = 1300.0
    app.on_saved(str(folder), tray_control.AUTO, True)
    assert queue.submitted == [(jobs.TRANSCRIBE, str(folder))]


def test_manual_recording_is_not_trimmed(app, tmp_path, monkeypatch):
    from meet import tail

    queue = _Queue()
    tray_control.TrayControl(app, queue=queue)
    monkeypatch.setattr(tail, "trim", lambda path: 1 / 0)
    app.call_end_at = 1300.0
    app.on_saved(str(_saved_folder(tmp_path)), tray_control.MANUAL, True)
    assert len(queue.submitted) == 1


# --- вкладка «Агент»: файлы для Claude Code / Codex в папке встречи ----------


def _agent_folder(tmp_path, control_state, monkeypatch):
    from meet import library

    folder = _saved_folder(tmp_path)
    library.write_meta(folder, {"title": "Планирование спринта"})
    library.write_transcript(folder, {"segments": [
        {"start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "Начнём."},
        {"start": 65, "end": 66, "speaker": "Анна", "text": "Готово."}]})
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    return folder


def test_agent_context_writes_transcript_md(control_state, tmp_path, monkeypatch):
    """transcript.md — тот же Markdown, что и экспорт: название, имена
    спикеров (сырые SPEAKER_XX — «Спикер N»), таймкоды."""
    folder = _agent_folder(tmp_path, control_state, monkeypatch)
    got = control_state.agent_context(folder.name)
    assert got == {"folder": str(folder), "files": ["transcript.md"]}
    text = (folder / "transcript.md").read_text(encoding="utf-8")
    assert "# Планирование спринта" in text
    assert "Спикер 1" in text and "Анна" in text and "SPEAKER_" not in text
    assert "01:05" in text
    assert text == control_state.export(folder.name, "md")["content"]


def test_agent_context_lists_summary_and_rewrites_atomically(control_state, tmp_path,
                                                             monkeypatch):
    folder = _agent_folder(tmp_path, control_state, monkeypatch)
    (folder / "summary.md").write_text("## Итоги\n", encoding="utf-8")
    (folder / "transcript.md").write_text("старое", encoding="utf-8")
    got = control_state.agent_context(folder.name)
    assert got["files"] == ["transcript.md", "summary.md"]
    assert "старое" not in (folder / "transcript.md").read_text(encoding="utf-8")
    assert sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp")) == []


def test_agent_context_needs_a_transcript(control_state, tmp_path, monkeypatch):
    folder = _saved_folder(tmp_path)
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    assert control_state.agent_context(folder.name) == {"error": "транскрипта нет"}
    assert control_state.agent_context("..") == {"error": "записи нет"}
    assert not (folder / "transcript.md").exists()
