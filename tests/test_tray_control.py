"""Адаптер трея для control API — против настоящего объекта TrayApp.

Смысл этих тестов: снимок состояния и команды панели должны биться с полями и
методами трея, а не с их представлением в голове автора. Аудио и pystray здесь
не нужны — TrayApp собирается без них.
"""

import json
import time

import pytest

from meet import events, jobs, library, live_control, settings, tray, tray_control, watch


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


def test_recordings_come_from_the_card_cache(control_state, tmp_path, monkeypatch):
    # Список — из кэша карточек поиска: повторный запрос не перечитывает папки.
    from pathlib import Path

    from meet import search

    search.clear_cache()
    for name in ("2026-09-28_10-00", "2026-09-30_10-00", "2026-09-29_10-00"):
        _saved_folder(tmp_path, name)
    monkeypatch.setattr(control_state, "_root", lambda: tmp_path / "recordings")
    calls = []
    real = library.describe
    monkeypatch.setattr(library, "describe", lambda f: calls.append(Path(f).name) or real(f))
    first = control_state.recordings()
    assert [i["id"] for i in first["items"]] == [
        "2026-09-30_10-00", "2026-09-29_10-00", "2026-09-28_10-00"]
    assert len(calls) == 3
    calls.clear()
    assert control_state.recordings() == first
    assert calls == []
    assert [i["id"] for i in control_state.recordings(limit=2)["items"]] == [
        "2026-09-30_10-00", "2026-09-29_10-00"]
    library.write_transcript(tmp_path / "recordings" / "2026-09-28_10-00",
                             {"title": "Созвон", "segments": []})
    assert [i["id"] for i in control_state.recordings(q="созв")["items"]] == ["2026-09-28_10-00"]
    assert calls == ["2026-09-28_10-00"]  # перечитана только изменившаяся


def test_recordings_limit_is_up_to_the_caller(control_state, tmp_path, monkeypatch):
    # Окно просит 5000 (старые записи не теряются), трей — 200 по умолчанию.
    from meet import search

    search.clear_cache()
    root = tmp_path / "recordings"
    for i in range(205):
        folder = root / f"2020-01-01_00-00_{i:03d}"
        folder.mkdir(parents=True)
        (folder / "sys.opus").write_bytes(b"x")
    monkeypatch.setattr(control_state, "_root", lambda: root)
    assert len(control_state.recordings()["items"]) == 200
    assert len(control_state.recordings(limit=5000)["items"]) == 205


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


def _downloads(app, release):
    """Загрузки-заглушки: идут, пока не отпустят `release`."""

    def spawn(job, on_line):
        release.wait(timeout=5)
        on_line(json.dumps({"kind": "job.result", "path": job.folder}))
        return 0

    return jobs.KeyedQueues(app.bus, spawn=spawn)


def _wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "не дождались"
        time.sleep(0.02)


def test_models_download_at_once_and_the_same_one_once(app):
    import threading

    release = threading.Event()
    state = tray_control.TrayControl(app, downloads=_downloads(app, release))
    try:
        a = state.download_model({"id": "Systran/faster-whisper-small"})
        b = state.download_model({"id": "gigaam/v3_e2e_rnnt"})
        again = state.download_model({"id": "Systran/faster-whisper-small"})
        assert a["id"] != b["id"] and again["id"] == a["id"]
        _wait_for(lambda: all(state.downloads.get(j["id"]).state == jobs.RUNNING for j in (a, b)))
        listed = {i["id"]: i for i in state.jobs()["items"]}
        assert listed[a["id"]]["kind"] == listed[b["id"]]["kind"] == jobs.DOWNLOAD_MODEL
        assert state.cancel_job(b["id"]) == {"ok": True}
        release.set()
        _wait_for(lambda: state.downloads.get(a["id"]).state == jobs.DONE)
        assert state.downloads.get(b["id"]).state == jobs.CANCELLED
    finally:
        release.set()
        state.downloads.stop()


def test_model_is_not_removed_while_it_downloads(app, monkeypatch):
    import threading

    from meet import models

    removed = []
    monkeypatch.setattr(models, "remove", lambda model_id: removed.append(model_id) or {"ok": True})
    release = threading.Event()
    state = tray_control.TrayControl(app, downloads=_downloads(app, release))
    try:
        state.download_model({"id": "gigaam/v3_e2e_rnnt"})
        got = state.remove_model({"id": "gigaam/v3_e2e_rnnt"})
        assert got == {"ok": False, "error": tray_control.MODEL_DOWNLOADING}
        # Другую модель — можно: её никто не качает.
        assert state.remove_model({"id": "gigaam/v3_e2e_ctc"}) == {"ok": True}
        assert removed == ["gigaam/v3_e2e_ctc"]
    finally:
        release.set()
        state.downloads.stop()


def test_model_download_waits_for_no_engine_install(app):
    from meet import paths
    from meet.control import Conflict

    class Installing:
        def active_for(self, folder, kinds):
            if folder == str(paths.data_dir()) and jobs.INSTALL_ENGINE in kinds:
                return jobs.Job(id="e1", kind=jobs.INSTALL_ENGINE, folder=folder)
            return None

    state = tray_control.TrayControl(app, queue=Installing())
    with pytest.raises(Conflict, match="установка движка"):
        state.download_model({"id": "Systran/faster-whisper-small"})
    assert state.downloads.listing() == []


def test_engine_install_waits_for_no_model_download(app):
    """Зеркало отказа выше: пока качаются модели, движок не ставится — pip
    переставлял бы пакеты, которые грузит подпроцесс загрузки."""
    import threading

    from meet.control import Conflict

    submitted = []

    class Queue:
        def submit(self, kind, folder, options=None):
            submitted.append(kind)
            return jobs.Job(id="e1", kind=kind, folder=folder)

        def active_for(self, folder, kinds):
            return None

    release = threading.Event()
    state = tray_control.TrayControl(app, queue=Queue(), downloads=_downloads(app, release))
    try:
        state.download_model({"id": "gigaam/v3_e2e_rnnt"})
        with pytest.raises(Conflict, match="загрузки моделей"):
            state.install_engine({})
        assert submitted == []
        release.set()
        _wait_for(lambda: not state.downloads.any_active())
        assert state.install_engine({})["kind"] == jobs.INSTALL_ENGINE
        assert submitted == [jobs.INSTALL_ENGINE]
    finally:
        release.set()
        state.downloads.stop()


def test_model_download_after_resident_stop_is_refused(app):
    from meet.control import Conflict

    state = tray_control.TrayControl(app)
    state.downloads.stop()
    with pytest.raises(Conflict, match="останавливается"):
        state.download_model({"id": "Systran/faster-whisper-small"})
    assert state.downloads.listing() == []


def test_saving_transcript_requires_segments(with_recordings, app):
    state = tray_control.TrayControl(app)
    assert "error" in state.save_transcript("2026-08-18_11-00", {"нет": "полей"})
    got = state.save_transcript("2026-08-18_11-00", {"version": 1, "segments": []})
    assert got["ok"] is True


def test_naming_speakers_renames_and_enrolls(with_recordings, app, tmp_path):
    """Названный спикер попадает в базу голосов — это и есть «обучение клона».
    Прежний вход API идёт тем же путём, что панель: шагом истории."""
    from meet import library, voices

    library.write_transcript(with_recordings, {
        "version": 1, "segments": [
            {"start": 0.0, "end": 1.0, "speaker": "Спикер 1", "text": "раз"},
            {"start": 1.0, "end": 2.0, "speaker": "Спикер 2", "text": "два"},
        ],
    })
    (with_recordings / "2026-08-18_11-00_speakers.json").write_text(json.dumps({
        "source": str(with_recordings), "date": "2026-08-18", "speakers": [
            {"label": "SPEAKER_00", "display": "Спикер 1", "embedding": [1.0, 0.0]}]},
        ensure_ascii=False), encoding="utf-8")
    state = tray_control.TrayControl(app)
    got = state.name_speakers("2026-08-18_11-00", {"Спикер 1": "Демьян Петров"})
    assert got["renamed"] == 1 and got["enrolled"] == ["Демьян Петров"]
    data = library.read_transcript(with_recordings)
    assert data["segments"][0]["speaker"] == "Демьян Петров"
    assert data["segments"][1]["speaker"] == "Спикер 2"
    assert list(voices.load_voices(tmp_path / "voices")) == ["Демьян Петров"]
    assert state.speakers("2026-08-18_11-00")["pos"] == 1  # отменяется из панели


def test_naming_survives_missing_voice_sidecar(with_recordings, app):
    """Расшифровка без эмбеддингов — не ошибка: имена всё равно сохранены."""
    from meet import library

    library.write_transcript(with_recordings, {
        "version": 1,
        "segments": [{"start": 0.0, "end": 1.0, "speaker": "Спикер 1", "text": "раз"}],
    })
    got = tray_control.TrayControl(app).name_speakers(
        "2026-08-18_11-00", {"Спикер 1": "Демьян"})
    assert got["ok"] is True and got["renamed"] == 1
    assert "голосовых отпечатков" in got["voices_error"]
    assert library.read_transcript(with_recordings)["segments"][0]["speaker"] == "Демьян"


def test_naming_speakers_refuses_while_the_recording_is_transcribed(with_recordings, app):
    from meet import control, library

    library.write_transcript(with_recordings, {
        "version": 1,
        "segments": [{"start": 0.0, "end": 1.0, "speaker": "Спикер 1", "text": "раз"}],
    })

    class Busy(_Queue):
        def active_for(self, folder, kinds):
            return jobs.Job(id="j1", kind=jobs.TRANSCRIBE, folder=folder)

    with pytest.raises(control.Conflict):
        tray_control.TrayControl(app, queue=Busy()).name_speakers(
            "2026-08-18_11-00", {"Спикер 1": "Демьян"})
    assert library.read_transcript(with_recordings)["segments"][0]["speaker"] == "Спикер 1"


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


def _live_hooks(app, monkeypatch):
    """Резидент, у которого пост-хук только запоминает папку."""
    hooks = []
    monkeypatch.setattr(tray, "_run_post_hook", lambda f: hooks.append(f))
    state = tray_control.TrayControl(app, queue=_Queue())
    state._background = lambda fn, name=None: fn()
    return hooks


def test_recording_with_assistant_runs_post_hook_once(app, tmp_path, monkeypatch):
    """Запись с ассистентом — обычная запись резидента (`source: live`):
    пост-хук зовёт её остановка, один раз."""
    hooks = _live_hooks(app, monkeypatch)
    folder = _saved_folder(tmp_path)
    app.recording = True
    app.source = tray_control.LIVE
    app.stop_event = __import__("threading").Event()
    app.thread = None
    app.result = {"folder": str(folder)}
    app.stop_recording()
    assert hooks == [str(folder)]
    from meet import library
    assert library.read_meta(folder)["source"] == "live"


def test_assistant_events_never_run_the_post_hook(app, tmp_path, monkeypatch):
    """Конец ассистента — не конец записи: хук — только от остановки записи."""
    hooks = _live_hooks(app, monkeypatch)
    folder = _saved_folder(tmp_path)
    app.bus.emit(live_control.LIVE_STOPPED, folder=str(folder), complete=True)
    app.bus.emit(live_control.LIVE_STOPPED, folder=str(folder), complete=False)
    assert hooks == []


def test_attached_live_stop_leaves_post_hook_to_the_recording(app, tmp_path, monkeypatch):
    """Ассистент, подключённый к обычной записи: хук позовёт остановка самой
    записи, второй раз — не нужно."""
    hooks = _live_hooks(app, monkeypatch)
    folder = _saved_folder(tmp_path)
    app.bus.emit(live_control.LIVE_STOPPED, folder=str(folder), complete=True, attached=True)
    assert hooks == []


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


def test_person_role_set_read_and_clear(voices_state, monkeypatch, tmp_path):
    state, voices = voices_state
    monkeypatch.setattr(state, "_root", lambda: tmp_path / "recordings")
    assert state.people()["items"][0]["role"] == ""
    assert state.person("Демьян")["role"] == ""
    assert state.set_role("Демьян", {"role": "  CTO\nAcme "}) == {"ok": True, "role": "CTO Acme"}
    assert state.people()["items"][0]["role"] == "CTO Acme"
    assert state.person("Демьян")["role"] == "CTO Acme"
    assert state.set_role("Демьян", {"role": ""}) == {"ok": True, "role": ""}
    assert state.person("Демьян")["role"] == ""
    assert json.loads((voices / "Демьян.json").read_text(encoding="utf-8")) == {"samples": []}


def test_person_role_errors_are_text(voices_state):
    from meet import control

    state, _ = voices_state
    assert state.set_role("Никто", {"role": "x"}) == {"error": "человека нет"}
    with pytest.raises(control.BadRequest):
        state.set_role("a/b", {"role": "x"})
    with pytest.raises(control.BadRequest, match="role"):
        state.set_role("Демьян", {"role": 5})
    with pytest.raises(control.BadRequest, match="role"):
        state.set_role("Демьян", {})


def test_person_role_survives_rename_merge_and_goes_with_delete(voices_state, monkeypatch, tmp_path):
    state, voices = voices_state
    monkeypatch.setattr(state, "_root", lambda: tmp_path / "recordings")
    (voices / "Пётр.json").write_text('{"samples": []}', encoding="utf-8")
    state.set_role("Демьян", {"role": "заказчик"})
    assert state.person_action("Демьян", "rename", {"to": "Демьян Петров"}) == {"ok": True}
    assert state.person("Демьян Петров")["role"] == "заказчик"
    assert state.person_action("Пётр", "merge", {"into": "Демьян Петров"}) == {"ok": True}
    assert state.person("Демьян Петров")["role"] == "заказчик"
    state.person_action("Демьян Петров", "delete")
    assert state.people()["items"] == []


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
    monkeypatch.setattr(control_state, "_voices", lambda: tmp_path / "voices")
    served = control_state.recording(folder.name)["transcript"]["segments"]
    assert [s["speaker"] for s in served] == ["Спикер 1", "Спикер 2"]
    assert [s["speaker"] for s in control_state.transcript(folder.name)["segments"]] == [
        "Спикер 1", "Спикер 2"]
    got = control_state.name_speakers(folder.name, {"Спикер 2": "Матвей"})
    assert got["renamed"] == 1  # нормализация старых меток — не правка
    stored = library.read_transcript(folder)["segments"]
    assert [s["speaker"] for s in stored] == ["Спикер 1", "Матвей"]


def test_name_speakers_accepts_raw_label_keys(control_state, tmp_path, monkeypatch):
    from meet import library

    folder = _saved_folder(tmp_path)
    library.write_transcript(folder, {"segments": [
        {"start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "а"},
        {"start": 1, "end": 2, "speaker": "SPEAKER_01", "text": "б"}]})
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    monkeypatch.setattr(control_state, "_voices", lambda: tmp_path / "voices")
    control_state.name_speakers(folder.name, {"SPEAKER_01": "Матвей"})
    stored = library.read_transcript(folder)["segments"]
    assert [s["speaker"] for s in stored] == ["Спикер 1", "Матвей"]


def test_opening_the_panel_normalises_old_raw_labels(control_state, tmp_path, monkeypatch):
    from meet import library

    folder = _saved_folder(tmp_path)
    library.write_transcript(folder, {"segments": [
        {"start": 0, "end": 1, "speaker": "SPEAKER_00", "text": "а"}]})
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    got = control_state.speakers(folder.name)
    assert [r["label"] for r in got["speakers"]] == ["Спикер 1"] and got["history"] == []
    assert library.read_transcript(folder)["segments"][0]["speaker"] == "Спикер 1"


def test_voice_base_failure_is_a_conflict_and_changes_nothing(with_recordings, app, monkeypatch):
    from meet import control, voices

    rid = _speaker_meeting(with_recordings)
    before = library.read_transcript(with_recordings)
    monkeypatch.setattr(voices, "enroll_sample", lambda *a, **kw: (_ for _ in ()).throw(OSError("занято")))
    state = tray_control.TrayControl(app, queue=_Queue())
    with pytest.raises(control.Conflict, match="базу голосов"):
        state.speakers_apply(rid, {"ops": [{"type": "rename", "label": "Спикер 2", "to": "Анна"}],
                                   "remember": {"Спикер 2": True}})
    assert library.read_transcript(with_recordings) == before
    assert state.speakers(rid)["pos"] == 0


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


def test_snapshot_carries_the_call_title_while_auto_recording(control_state, app):
    """Панель записи macOS показывает название звонка над таймером — то же,
    что потом станет названием записи: только у автозаписи и пока она идёт."""
    assert control_state.snapshot()["title"] is None
    app.recording = True
    app.source = tray_control.AUTO
    app.recording_title = "Dion — Планёрка отдела"
    assert control_state.snapshot()["title"] == "Dion — Планёрка отдела"
    app.source = tray_control.MANUAL
    assert control_state.snapshot()["title"] is None
    app.recording = False
    app.source = tray_control.AUTO
    assert control_state.snapshot()["title"] is None


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
    state._background = lambda fn, name=None: fn()
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
    assert events[-1] == {"kind": "record.call_end", "at": 1300.0, "wait_s": 600.0, "transcribe": True}


def test_failed_trim_still_transcribes(app, tmp_path, monkeypatch):
    from meet import tail

    queue = _Queue()
    state = tray_control.TrayControl(app, queue=queue)
    state._background = lambda fn, name=None: fn()
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


def test_agent_context_lists_analysis_when_present(control_state, tmp_path, monkeypatch):
    folder = _agent_folder(tmp_path, control_state, monkeypatch)
    (folder / "analysis.json").write_text("{}", encoding="utf-8")
    (folder / "summary.md").write_text("## Итоги\n", encoding="utf-8")
    assert control_state.agent_context(folder.name)["files"] == [
        "transcript.md", "summary.md", "analysis.json"]


def test_agent_context_during_live_uses_the_live_transcript(control_state, tmp_path, monkeypatch):
    """Идёт запись с ассистентом: расшифровки ещё нет, но есть лента живого
    режима — агент получает её как transcript.md с пометкой «черновая»."""
    folder = _saved_folder(tmp_path)
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    (folder / "live_transcript.md").write_text(
        "[00:00:05] Анна: Начнём с бюджета.\n[00:01:10] Олег: Согласен.\n", encoding="utf-8")
    got = control_state.agent_context(folder.name)
    assert got == {"folder": str(folder), "files": ["transcript.md"]}
    text = (folder / "transcript.md").read_text(encoding="utf-8")
    assert "черновая расшифровка живого режима" in text.lower()
    assert "[00:00:05] Анна: Начнём с бюджета." in text
    assert control_state.agent_files(folder.name) == {"files": ["transcript.md"], "live": True}


def test_agent_files_lists_without_writing(control_state, tmp_path, monkeypatch):
    folder = _agent_folder(tmp_path, control_state, monkeypatch)
    assert control_state.agent_files(folder.name) == {"files": ["transcript.md"], "live": False}
    assert not (folder / "transcript.md").exists()
    (folder / "summary.md").write_text("## Итоги\n", encoding="utf-8")
    (folder / "analysis.json").write_text("{}", encoding="utf-8")
    assert control_state.agent_files(folder.name)["files"] == [
        "transcript.md", "summary.md", "analysis.json"]


def test_agent_session_mark_is_written_and_listed(control_state, tmp_path, monkeypatch):
    """Запуск агента помечает папку (meta.json, `agent_sessions`): по метке
    вкладка предлагает «Продолжить прошлую». Без метки поля `sessions` нет."""
    from meet import library

    folder = _agent_folder(tmp_path, control_state, monkeypatch)
    assert "sessions" not in control_state.agent_files(folder.name)
    control_state.agent_context(folder.name)  # прежняя оболочка: без провайдера
    control_state.agent_context(folder.name, {"provider": "строка"})
    assert "agent_sessions" not in library.read_meta(folder)
    control_state.agent_context(folder.name, {"provider": "codex"})
    assert control_state.agent_files(folder.name)["sessions"] == ["codex"]
    control_state.agent_context(folder.name, {"provider": "claude-code"})
    meta = library.read_meta(folder)
    assert set(meta["agent_sessions"]) == {"claude-code", "codex"}
    assert meta["title"] == "Планирование спринта"  # остальная meta на месте
    assert control_state.agent_files(folder.name)["sessions"] == ["claude-code", "codex"]


SID = "0b6f8a52-3c1d-4e2f-9a7b-1c2d3e4f5a6b"


def test_claude_session_id_is_kept_and_returned_for_resume(control_state, tmp_path, monkeypatch):
    """Новый сеанс Claude Code — с id от оболочки (`--session-id`); «Продолжить
    прошлую» получает в ответе тот же id (`--resume <id>`), а не «последний
    разговор в папке», который мог оставить кто-то другой."""
    from meet import library

    folder = _agent_folder(tmp_path, control_state, monkeypatch)
    got = control_state.agent_context(folder.name, {"provider": "claude-code", "session": SID})
    assert "session" not in got  # новый сеанс — отвечать нечем
    assert library.read_meta(folder)["agent_sessions"]["claude-code"]["id"] == SID
    got = control_state.agent_context(folder.name, {"provider": "claude-code", "resume": True})
    assert got["session"] == SID
    assert library.read_meta(folder)["agent_sessions"]["claude-code"]["id"] == SID
    # Негодный id не пишется; Codex — без id (resume --last).
    control_state.agent_context(folder.name, {"provider": "claude-code", "session": "x; rm"})
    assert library.read_meta(folder)["agent_sessions"]["claude-code"]["id"] is None
    assert "session" not in control_state.agent_context(
        folder.name, {"provider": "claude-code", "resume": True})
    # Метка ранней сборки (просто время) — сеанс есть, id нет.
    library.write_meta(folder, {"agent_sessions": {"codex": 1.0}})
    assert control_state.agent_files(folder.name)["sessions"] == ["codex"]
    assert "session" not in control_state.agent_context(folder.name, {"provider": "codex", "resume": True})


def test_agent_files_without_transcript_is_empty(control_state, tmp_path, monkeypatch):
    folder = _saved_folder(tmp_path)
    monkeypatch.setattr(control_state, "_root", lambda: folder.parent)
    assert control_state.agent_files(folder.name) == {"files": [], "live": False}
    assert control_state.agent_files("..") == {"error": "записи нет"}


# --- панель «Спикеры»: применение, откат, отказ во время обработки ------------


def _speaker_meeting(with_recordings):
    library.write_transcript(with_recordings, {
        "version": 1, "created_at": "2026-08-18T12:00:00", "segments": [
            {"start": 0.0, "end": 3.0, "speaker": "Спикер 1", "text": "Начнём."},
            {"start": 3.0, "end": 5.0, "speaker": "Спикер 2", "text": "Согласна."}]})
    (with_recordings / "2026-08-18_11-00_speakers.json").write_text(json.dumps({
        "source": str(with_recordings), "date": "2026-08-18", "speakers": [
            {"label": "SPEAKER_00", "display": "Спикер 1", "embedding": [1.0, 0.0]},
            {"label": "SPEAKER_01", "display": "Спикер 2", "embedding": [0.0, 1.0]}]},
        ensure_ascii=False), encoding="utf-8")
    return with_recordings.name


def _old_call_meeting(folder, tmp_path):
    """Запись звонка версии 0.1.0: без пометок дорожек; «Борис» узнан по базе
    при расшифровке (display сайдкара — «Борис»), «Спикер 2» — нет, «Вы» —
    микрофон владельца."""
    library.write_transcript(folder, {
        "version": 1, "created_at": "2026-08-18T12:00:00", "speakers": {"SPEAKER_00": "Борис"},
        "segments": [
            {"start": 0.0, "end": 3.0, "speaker": "Борис", "text": "Начнём с плана."},
            {"start": 3.5, "end": 6.0, "speaker": "Вы", "text": "Да, давайте."},
            {"start": 6.5, "end": 9.0, "speaker": "Спикер 2", "text": "Я по срокам."},
            {"start": 9.5, "end": 12.0, "speaker": "Борис", "text": "Хорошо."}]})
    (folder / "2026-08-18_11-00_speakers.json").write_text(json.dumps({
        "source": str(folder), "date": "2026-08-18", "speakers": [
            {"label": "SPEAKER_00", "display": "Борис", "embedding": [1.0, 0.0]},
            {"label": "SPEAKER_01", "display": "Спикер 2", "embedding": [0.0, 1.0]}]},
        ensure_ascii=False), encoding="utf-8")
    voices = tmp_path / "voices"
    voices.mkdir(exist_ok=True)
    (voices / "Борис.json").write_text(json.dumps({"samples": [
        {"embedding": [1.0, 0.0], "source": "C:/rec/old", "date": "2026-08-01"}]}), encoding="utf-8")
    return folder.name


def _tracks(folder):
    return [(s.get("speaker"), s.get("track"), s.get("track_source"))
            for s in library.read_transcript(folder)["segments"]]


def test_old_recording_voices_pane_rename_keeps_the_remote_speaker_remote(with_recordings, app, tmp_path):
    """0.1.0, путь (a): человека, узнанного по базе, переименовали в панели
    «Голоса». Его реплики остаются собеседником, голос строки не теряется, а
    открытие панели «Спикеры» ничего не пишет."""
    from meet import segvoices, speaker_split, speakers

    rid = _old_call_meeting(with_recordings, tmp_path)
    state = tray_control.TrayControl(app, queue=_SplitQueue())
    assert state.person_action("Борис", "rename", {"to": "Борис Козлов"}) == {"ok": True}
    side = json.loads((with_recordings / "2026-08-18_11-00_speakers.json").read_text(encoding="utf-8"))
    assert [e["display"] for e in side["speakers"]] == ["Борис Козлов", "Спикер 2"]
    before = (with_recordings / "transcript.json").read_bytes()
    view = state.speakers(rid)
    assert (with_recordings / "transcript.json").read_bytes() == before   # GET не пишет
    row = next(r for r in view["speakers"] if r["label"] == "Борис Козлов")
    assert row["has_voice"] is True
    state.speakers_split_prepare(rid, {"label": "Борис Козлов"})
    assert (with_recordings / "transcript.json").read_bytes() == before
    data, segments, own = speaker_split._load(with_recordings, "Борис Козлов")
    assert {t for _, t, _ in segvoices.needed(with_recordings, segments, own)} == {"sys"}
    # Первая правка пишет дорожки вместе с собой — Борис Козлов остаётся sys.
    state.speakers_relabel(rid, {"idx": [2], "to": "Анна", "count": 4, "labels": ["Спикер 2"]})
    assert _tracks(with_recordings) == [
        ("Борис Козлов", "sys", "inferred"), ("Вы", "mic", "inferred"),
        ("Анна", "sys", "inferred"), ("Борис Козлов", "sys", "inferred")]
    assert speakers.editable(with_recordings)["segments"][0]["track"] == "sys"


def test_old_recording_popover_rename_without_remember_stays_remote(with_recordings, app, tmp_path):
    """0.1.0, путь (b): «Кто это?» без «Запомнить голос» переписывал реплики
    через сохранение транскрипта, не трогая names, — к «Ольге» не ведёт ни
    один кластер. Она всё равно собеседник: микрофон — только владелец."""
    from meet import speakers

    rid = _old_call_meeting(with_recordings, tmp_path)
    data = library.read_transcript(with_recordings)
    for s in data["segments"]:
        if s["speaker"] == "Спикер 2":
            s["speaker"] = "Ольга"
    state = tray_control.TrayControl(app, queue=_SplitQueue())
    state.save_transcript(rid, data)
    before = (with_recordings / "transcript.json").read_bytes()
    state.speakers(rid)
    assert (with_recordings / "transcript.json").read_bytes() == before
    got = state.speakers_apply(rid, {"ops": [{"type": "rename", "label": "Ольга", "to": "Ольга Петрова"}],
                                     "remember": {}})
    assert got["pos"] == 1
    assert _tracks(with_recordings)[2] == ("Ольга Петрова", "sys", "inferred")
    assert _tracks(with_recordings)[1] == ("Вы", "mic", "inferred")
    assert speakers.editable(with_recordings)["segments"][2]["track"] == "sys"


def test_owner_renamed_in_settings_keeps_old_mic_turns(with_recordings, app, tmp_path):
    """Реплики старых записей подписаны прежним именем владельца: оно
    запоминается при смене имени в настройках и по-прежнему значит микрофон."""
    from meet import segvoices, settings

    _old_call_meeting(with_recordings, tmp_path)
    settings.patch({"recording": {"speaker_name": "Кузьма"}})
    settings.patch({"recording": {"speaker_name": "Кузьма Р."}})
    assert settings.load().recording.former_speaker_names == ("Кузьма",)
    data = library.read_transcript(with_recordings)
    data["segments"][1]["speaker"] = "Кузьма"
    segvoices.mark_tracks(with_recordings, data)
    assert data["segments"][1]["track"] == "mic" and data["segments"][0]["track"] == "sys"


def test_speakers_panel_apply_undo_redo_and_overview(with_recordings, app, tmp_path):
    rid = _speaker_meeting(with_recordings)
    state = tray_control.TrayControl(app, queue=_Queue())
    seen = []
    app.bus.subscribe(lambda e: seen.append(e))
    got = state.speakers(rid)
    assert [r["label"] for r in got["speakers"]] == ["Спикер 1", "Спикер 2"]
    assert got["owner"] == "Вы"

    got = state.speakers_apply(rid, {"ops": [{"type": "rename", "label": "Спикер 2", "to": "Анна"}],
                                     "remember": {"Спикер 2": True}})
    assert got["step"]["ops"][0]["to"] == "Анна" and got["pos"] == 1
    assert [r["label"] for r in got["speakers"]] == ["Спикер 1", "Анна"]
    assert (tmp_path / "voices" / "Анна.json").exists()
    assert any(getattr(e, "kind", None) == tray_control.RECORDING_UPDATED for e in seen)

    got = state.speakers_undo(rid)
    assert got["pos"] == 0 and [r["label"] for r in got["speakers"]] == ["Спикер 1", "Спикер 2"]
    assert not (tmp_path / "voices" / "Анна.json").exists()
    assert state.speakers_redo(rid)["pos"] == 1
    assert state.speakers_revert(rid, {"to_step_id": None})["pos"] == 0


def test_speakers_panel_errors_map_to_api_statuses(with_recordings, app):
    from meet import control

    rid = _speaker_meeting(with_recordings)
    state = tray_control.TrayControl(app, queue=_Queue())
    assert state.speakers("../чужое") == {"error": "записи нет"}
    with pytest.raises(control.BadRequest):
        state.speakers_apply(rid, {"ops": [{"type": "rename", "label": "Спикер 1", "to": "a/b"}]})
    with pytest.raises(control.BadRequest):
        state.speakers_undo(rid)
    state.speakers_apply(rid, {"ops": [{"type": "rename", "label": "Спикер 1", "to": "Борис"}]})
    data = library.read_transcript(with_recordings)
    data["segments"][0]["speaker"] = "Кто-то"
    library.write_transcript(with_recordings, data)
    with pytest.raises(control.Conflict):
        state.speakers_undo(rid)


def test_speakers_panel_refuses_while_the_recording_is_transcribed(with_recordings, app):
    from meet import control

    rid = _speaker_meeting(with_recordings)

    class Busy(_Queue):
        def active_for(self, folder, kinds):
            return jobs.Job(id="j1", kind=jobs.TRANSCRIBE, folder=folder)

    state = tray_control.TrayControl(app, queue=Busy())
    before = library.read_transcript(with_recordings)
    with pytest.raises(control.Conflict, match="расшифровка"):
        state.speakers_apply(rid, {"ops": [{"type": "rename", "label": "Спикер 1", "to": "Борис"}]})
    with pytest.raises(control.Conflict):
        state.speakers_undo(rid)
    assert library.read_transcript(with_recordings) == before
    assert state.speakers(rid)["speakers"]  # смотреть можно


def test_speakers_relabel_turns_through_the_panel_lock(with_recordings, app):
    from meet import control

    rid = _speaker_meeting(with_recordings)
    state = tray_control.TrayControl(app, queue=_Queue())
    got = state.speakers_relabel(rid, {"idx": [1], "labels": ["Спикер 2"], "count": 2, "to": "Спикер 1"})
    assert got["step"]["ops"][0]["type"] == "relabel" and got["pos"] == 1
    assert [r["label"] for r in got["speakers"]] == ["Спикер 1"]
    with pytest.raises(control.Conflict, match="обновите"):
        state.speakers_relabel(rid, {"idx": [1], "labels": ["Спикер 2"], "count": 2, "to": "Анна"})
    with pytest.raises(control.BadRequest):
        state.speakers_relabel(rid, {"idx": [], "to": "Анна"})

    class Busy(_Queue):
        def active_for(self, folder, kinds):
            return jobs.Job(id="j1", kind=jobs.TRANSCRIBE, folder=folder)

    busy = tray_control.TrayControl(app, queue=Busy())
    with pytest.raises(control.Conflict, match="расшифровка"):
        busy.speakers_relabel(rid, {"idx": [0], "to": "Анна"})


class _SplitQueue(_Queue):
    def __init__(self):
        super().__init__()
        self.options = []
        self.active = None

    def submit(self, kind, folder, options=None):
        self.options.append(options)
        self.active = jobs.Job(id=f"v{len(self.options)}", kind=kind, folder=folder, options=options or {})
        return self.active

    def active_for(self, folder, kinds):
        return self.active if self.active and self.active.kind in kinds else None


def test_split_prepare_queues_voices_once_then_preview_and_apply(with_recordings, app, tmp_path):
    from meet import control, segvoices

    rid = _speaker_meeting(with_recordings)
    queue = _SplitQueue()
    state = tray_control.TrayControl(app, queue=queue)
    got = state.speakers_split_prepare(rid, {"label": "Спикер 1"})
    assert got["ready"] is False and got["job"]["kind"] == jobs.SPEAKER_SPLIT
    assert queue.options == [{"label": "Спикер 1"}]
    state.speakers_split_prepare(rid, {"label": "Спикер 1"})       # уже идёт — вторую не ставим
    assert len(queue.options) == 1
    # Пока голоса считаются, запись не удалить, а правки спикеров — можно.
    with pytest.raises(control.BadRequest, match="разбор голосов"):
        state.delete_recording(rid)
    with pytest.raises(control.BadRequest):
        state.speakers_split_prepare(rid, {"label": "Нет такого"})

    data = library.read_transcript(with_recordings)
    data["segments"] = [{"start": float(i * 3), "end": float(i * 3 + 2.5), "speaker": "Спикер 1",
                         "text": f"фраза {i}"} for i in range(6)]
    library.write_transcript(with_recordings, data)
    vecs = {0: [1, 0], 1: [0, 1], 2: [1, 0.1], 3: [0.1, 1], 4: [1, 0], 5: [0, 1]}
    tracks = segvoices.tracks_of(with_recordings, data["segments"])
    segvoices.write_cache(with_recordings, {
        segvoices.key(tracks[i], data["segments"][i]): __import__("numpy").array(v, dtype=float)
        for i, v in vecs.items()})
    ready = state.speakers_split_prepare(rid, {"label": "Спикер 1"})
    assert ready["ready"] is True and "job" not in ready
    prev = state.speakers_split_preview(rid, {"label": "Спикер 1", "mode": "auto", "k": 2})
    assert sorted(g["idx"] for g in prev["groups"]) == [[0, 2, 4], [1, 3, 5]]
    got = state.speakers_split_apply(rid, {"label": "Спикер 1", "fingerprint": prev["fingerprint"],
                                           "groups": [{"idx": g["idx"], "to": None} for g in prev["groups"]]})
    assert got["step"]["ops"][0]["type"] == "split" and len(got["speakers"]) == 2
    with pytest.raises(control.BadRequest, match="нет спикера"):   # его уже разделили
        state.speakers_split_apply(rid, {"label": "Спикер 1", "fingerprint": prev["fingerprint"],
                                         "groups": [{"idx": [0], "to": None}]})


def test_rediarize_queues_validates_previews_applies_and_discards(with_recordings, app, monkeypatch):
    from meet import control, rediarize

    rid = _speaker_meeting(with_recordings)
    queue = _SplitQueue()
    state = tray_control.TrayControl(app, queue=queue)
    for bad in ({"num_speakers": 0}, {"num_speakers": "3"}, {"num_speakers": 3, "max_speakers": 4},
                {"min_speakers": 5, "max_speakers": 2}, {"sensitivity": 2}):
        with pytest.raises(control.BadRequest):
            state.speakers_rediarize(rid, bad)
    got = state.speakers_rediarize(rid, {"num_speakers": 3, "sensitivity": 0.7})
    assert got["job"]["kind"] == jobs.REDIARIZE
    assert queue.options == [{"num_speakers": 3, "sensitivity": 0.7}]
    state.speakers_rediarize(rid, {})                       # уже идёт — вторую не ставим
    assert len(queue.options) == 1
    assert state.speakers_rediarized(rid) == {"error": "нового разделения нет"}

    data = library.read_transcript(with_recordings)
    (with_recordings / rediarize.PREVIEW_NAME).write_text(json.dumps({
        "base": rediarize.fingerprint(data), "params": {"num_speakers": 3},
        "parts": [[{**s, "speaker": "SPEAKER_00"}] for s in data["segments"]],
        "voices": {}, "names": {}}, ensure_ascii=False), encoding="utf-8")
    prev = state.speakers_rediarized(rid)
    assert prev["stale"] is False and [r["label"] for r in prev["speakers"]] == ["Спикер 1"]
    got = state.speakers_rediarize_apply(rid)
    assert got["step"]["ops"][0]["type"] == "rediarize"
    assert [s["speaker"] for s in library.read_transcript(with_recordings)["segments"]] == ["Спикер 1"] * 2
    assert state.speakers_rediarize_discard(rid) == {"ok": True}


def test_split_turn_through_the_panel_lock(with_recordings, app):
    rid = _speaker_meeting(with_recordings)
    state = tray_control.TrayControl(app, queue=_Queue())
    got = state.speakers_split_turn(rid, {"turn": [0], "at": 0, "char": 0, "to": "Анна",
                                          "labels": ["Спикер 1"], "count": 2})
    assert got["step"]["ops"][0]["type"] == "split_turn"
    assert [r["label"] for r in got["speakers"]] == ["Анна", "Спикер 2"]


def test_threshold_preview_apply_and_bad_value(with_recordings, app, tmp_path):
    from meet import control

    rid = _speaker_meeting(with_recordings)
    voices = tmp_path / "voices"
    voices.mkdir(exist_ok=True)
    (voices / "Анна.json").write_text(json.dumps({"samples": [
        {"embedding": [0.3, 0.95], "source": "C:/old", "date": "2026-08-01"}]}, ensure_ascii=False),
        encoding="utf-8")
    state = tray_control.TrayControl(app, queue=_Queue())
    assert state.speakers(rid)["voice_threshold_default"] == 0.75
    plan = state.speakers_threshold(rid, {"value": 0.9})
    assert plan["value"] == 0.9 and [c["label"] for c in plan["changes"]] == ["Спикер 2"]
    got = state.speakers_threshold_apply(rid, {"value": 0.9})
    assert [r["label"] for r in got["speakers"]] == ["Спикер 1", "Анна"]
    assert got["voice_threshold"] == 0.9
    for bad in (0.2, "0.8", True, None):
        with pytest.raises(control.BadRequest):
            state.speakers_threshold(rid, {"value": bad})


def test_speakers_panel_works_while_a_summary_is_written(with_recordings, app):
    rid = _speaker_meeting(with_recordings)

    class Llm(_Queue):
        def active_for(self, folder, kinds):
            return jobs.Job(id="s1", kind=jobs.SUMMARY, folder=folder)

    state = tray_control.TrayControl(app, queue=_Queue(), llm_queue=Llm())
    got = state.speakers_apply(rid, {"ops": [{"type": "rename", "label": "Спикер 1", "to": "Борис"}]})
    assert got["pos"] == 1


def test_speakers_panel_change_refreshes_search_and_reexports(with_recordings, app, tmp_path,
                                                              monkeypatch):
    from meet import kb_export, search

    rid = _speaker_meeting(with_recordings)
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_config(tmp_path, {
        "recording": {"out_dir": str(tmp_path / "recordings"),
                      "voices_dir": str(tmp_path / "voices")},
        "export": {"meetings_dir": str(vault), "auto_export": True}})
    exported = []
    monkeypatch.setattr(kb_export, "export_recording",
                        lambda folder, cfg, **kw: exported.append(folder.name) or {"path": "x"})
    state = tray_control.TrayControl(app, queue=_Queue())
    state._background = lambda fn, name=None: fn()
    assert search.search_library(tmp_path / "recordings", "спикер:Анна") == []
    state.speakers_apply(rid, {"ops": [{"type": "rename", "label": "Спикер 2", "to": "Анна"}]})
    assert [i["id"] for i in search.search_library(tmp_path / "recordings", "спикер:Анна")] == [rid]
    assert exported == [rid]


def test_saving_transcript_waits_for_a_speaker_change(with_recordings, app):
    """Сохранение из редактора не вклинивается посреди «Применить» или отмены."""
    import threading

    state = tray_control.TrayControl(app, queue=_Queue())
    done = threading.Event()
    with state._speakers_lock:
        t = threading.Thread(target=lambda: (state.save_transcript(
            "2026-08-18_11-00", {"version": 1, "segments": []}), done.set()))
        t.start()
        assert not done.wait(0.2)
    assert done.wait(5)
    t.join()


# --- «Исправить…»: слово во всей встрече, термины распознавания ---------------------


def _fix_meeting(folder):
    library.write_transcript(folder, {
        "version": 1, "created_at": "2026-08-18T12:00:00", "segments": [
            {"start": 0.0, "end": 3.0, "speaker": "Спикер 1", "text": "Поднимем кубер нетис."},
            {"start": 3.0, "end": 5.0, "speaker": "Спикер 2", "text": "Кубер нетис готов."}]})
    return folder.name


def test_text_preview_and_apply_one_then_undo(with_recordings, app, tmp_path, monkeypatch):
    from meet import paths, search

    monkeypatch.setattr(paths, "hotwords_path", lambda: tmp_path / "hotwords.txt")
    rid = _fix_meeting(with_recordings)
    state = tray_control.TrayControl(app, queue=_Queue())
    got = state.text_preview(rid, {"find": "кубер нетис", "segment": 1, "offset": 0})
    assert got["count"] == 2 and got["here"] == {"start": 3.0, "end": 5.0}
    assert [s["match"] for s in got["samples"]] == ["кубер нетис", "Кубер нетис"]

    search.search_library(tmp_path / "recordings", "kubernetes")  # кэш поиска прогрет
    got = state.text_apply(rid, {"find": "кубер нетис", "replace": "Kubernetes", "scope": "one",
                                 "segment": 1, "offset": 0, "count": 2, "add_hotword": True})
    assert got["changed"] == 1 and got["step"]["ops"][0]["type"] == "text" and got["pos"] == 1
    assert got["hotword"] == {"term": "Kubernetes", "added": True, "over_budget": False}
    assert (tmp_path / "hotwords.txt").read_text(encoding="utf-8") == "Kubernetes\n"
    assert [s["text"] for s in library.read_transcript(with_recordings)["segments"]] == [
        "Поднимем кубер нетис.", "Kubernetes готов."]
    assert [i["id"] for i in search.search_library(tmp_path / "recordings", "kubernetes")] == [rid]

    # Тот же термин ещё раз — не дублируется.
    got = state.text_apply(rid, {"find": "кубер нетис", "replace": "Kubernetes", "scope": "all",
                                 "add_hotword": True})
    assert got["hotword"]["added"] is False and got["changed"] == 1
    assert (tmp_path / "hotwords.txt").read_text(encoding="utf-8") == "Kubernetes\n"
    state.speakers_undo(rid)
    state.speakers_undo(rid)
    assert [s["text"] for s in library.read_transcript(with_recordings)["segments"]] == [
        "Поднимем кубер нетис.", "Кубер нетис готов."]


def test_text_apply_errors_and_busy_refusal(with_recordings, app):
    from meet import control

    rid = _fix_meeting(with_recordings)
    state = tray_control.TrayControl(app, queue=_Queue())
    with pytest.raises(control.BadRequest):
        state.text_apply(rid, {"find": "Docker", "replace": "Докер", "scope": "all"})
    with pytest.raises(control.BadRequest):
        state.text_preview(rid, {"find": ""})
    with pytest.raises(control.Conflict, match="обновите"):
        state.text_apply(rid, {"find": "кубер нетис", "replace": "K8s", "scope": "one",
                               "segment": 0, "offset": 2})
    assert state.text_preview("../чужое", {"find": "x"}) == {"error": "записи нет"}

    class Busy(_Queue):
        def active_for(self, folder, kinds):
            return jobs.Job(id="j1", kind=jobs.TRANSCRIBE, folder=folder)

    before = library.read_transcript(with_recordings)
    with pytest.raises(control.Conflict, match="расшифровка"):
        tray_control.TrayControl(app, queue=Busy()).text_apply(
            rid, {"find": "кубер нетис", "replace": "K8s", "scope": "all"})
    assert library.read_transcript(with_recordings) == before


def test_hotword_only_when_the_text_is_already_right(with_recordings, app, tmp_path, monkeypatch):
    from meet import control, paths

    monkeypatch.setattr(paths, "hotwords_path", lambda: tmp_path / "hotwords.txt")
    rid = _fix_meeting(with_recordings)
    state = tray_control.TrayControl(app, queue=_Queue())
    got = state.text_apply(rid, {"find": "кубер нетис", "replace": "кубер нетис", "scope": "all",
                                 "add_hotword": True})
    assert got["changed"] == 0 and got["hotword"]["added"] is True and "step" not in got
    with pytest.raises(control.BadRequest):
        state.text_apply(rid, {"find": "кубер нетис", "replace": "кубер нетис", "scope": "all"})


def test_text_apply_adds_a_rule_for_future_transcriptions(with_recordings, app, tmp_path):
    rid = _fix_meeting(with_recordings)
    state = tray_control.TrayControl(app, queue=_Queue())
    got = state.text_apply(rid, {"find": "кубер нетис", "replace": "Kubernetes", "scope": "all",
                                 "add_rule": True})
    assert got["changed"] == 2
    assert got["rule"] == {"from": "кубер нетис", "to": "Kubernetes", "replaced": None}
    assert list(settings.load().asr.replacements) == [{"from": "кубер нетис", "to": "Kubernetes"}]
    # Текст уже исправлен, а правило просили — только правило.
    got = state.text_apply(rid, {"find": "Kubernetes", "replace": "K8s", "scope": "one", "segment": 0,
                                 "offset": 9, "add_rule": True})
    assert got["changed"] == 1
    assert [r["to"] for r in settings.load().asr.replacements] == ["Kubernetes", "K8s"]
    got = state.text_apply(rid, {"find": "K8s", "replace": "K8s", "scope": "all", "add_rule": True})
    assert got["changed"] == 0 and got["rule"] is None  # правило «то же на то же» не нужно


def test_undo_expected_step_and_edit_head_in_the_card(with_recordings, app):
    from meet import control

    rid = _fix_meeting(with_recordings)
    state = tray_control.TrayControl(app, queue=_Queue())
    assert state.recording(rid)["edit_head"] is None
    step = state.text_apply(rid, {"find": "кубер нетис", "replace": "K8s", "scope": "all"})["step"]["id"]
    assert state.recording(rid)["edit_head"] == step
    other = state.speakers_relabel(rid, {"idx": [1], "to": "Спикер 1"})["step"]["id"]
    with pytest.raises(control.Conflict, match="не последнее"):
        state.speakers_undo(rid, {"expect_step": step})
    assert state.speakers_undo(rid, {"expect_step": other})["pos"] == 1
    assert state.recording(rid)["edit_head"] == step


def test_rule_with_the_same_from_reports_the_replaced_rule(with_recordings, app):
    rid = _fix_meeting(with_recordings)
    state = tray_control.TrayControl(app, queue=_Queue())
    settings.patch({"asr": {"replacements": [{"from": "Кубер  нетис", "to": "K8s"}]}})
    got = state.text_apply(rid, {"find": "кубер нетис", "replace": "Kubernetes", "scope": "all",
                                 "add_rule": True})
    assert got["rule"]["replaced"] == {"from": "Кубер нетис", "to": "K8s"}
    assert list(settings.load().asr.replacements) == [{"from": "кубер нетис", "to": "Kubernetes"}]


def test_hotword_is_only_the_new_words_and_removal_is_server_side(with_recordings, app, tmp_path,
                                                                  monkeypatch):
    from meet import control, paths

    target = tmp_path / "hotwords.txt"
    monkeypatch.setattr(paths, "hotwords_path", lambda: target)
    target.write_text("SIEM\n# комментарий\n", encoding="utf-8")
    rid = _fix_meeting(with_recordings)
    state = tray_control.TrayControl(app, queue=_Queue())
    got = state.text_apply(rid, {"find": "кубер нетис", "replace": "кубер нетис подпись", "scope": "one",
                                 "segment": 0, "offset": 9, "add_hotword": True})
    assert got["hotword"]["term"] == "подпись"
    target.write_text(target.read_text(encoding="utf-8") + "SOC\n", encoding="utf-8")  # правка между делом
    reply = state.remove_hotword({"term": "подпись"})
    assert target.read_text(encoding="utf-8") == "SIEM\n# комментарий\nSOC\n" == reply["text"]
    with pytest.raises(control.BadRequest):
        state.remove_hotword({"term": " "})


def test_opencode_session_mark_has_no_id(control_state, tmp_path, monkeypatch):
    """OpenCode своего id при запуске не принимает: метка без id, «Продолжить
    прошлую» — `--continue` (последний сеанс в папке встречи)."""
    from meet import library

    folder = _agent_folder(tmp_path, control_state, monkeypatch)
    got = control_state.agent_context(folder.name, {"provider": "opencode"})
    assert "session" not in got
    assert library.read_meta(folder)["agent_sessions"]["opencode"]["id"] is None
    assert control_state.agent_files(folder.name)["sessions"] == ["opencode"]
    assert "session" not in control_state.agent_context(folder.name, {"provider": "opencode", "resume": True})
