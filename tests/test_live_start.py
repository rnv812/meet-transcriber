"""Старт ассистента под резидентом: этапы, сбои до готовности, повтор.

Ребёнок — та же заглушка, что в test_live_control (настоящий процесс):
по новому протоколу он публикует порт сразу (`ready: false`, `stage`), потом
«захват звука» (`capturing`), потом готовность (`ready: true`). Шаги
заглушка делает по файлам-разрешениям теста (`capture`, `ready`, `crash`).
"""

import json
import time
import urllib.request

from meet import events, live_control, pcm_tap

# Заглушка ребёнка и фикстуры — общие с test_live_control.
from test_live_control import (  # noqa: F401  (фикстуры pytest)
    Recorder,
    Stub,
    _active,
    _idle,
    _wait_for,
    data_dir,
    make_live,
)


def _allow(stub, name: str) -> None:
    (stub.notes / name).write_text("", encoding="utf-8")


def _log(data_dir) -> str:
    return (data_dir / "logs" / "live.log").read_text(encoding="utf-8")


def test_staged_start_reports_stages_capture_and_readiness(make_live, tmp_path):
    live, stub, rec = make_live("staged")
    live.start(tmp_path / "recordings")
    # Порт известен сразу: этап виден в статусе, ассистент ещё не пишет.
    _wait_for(lambda: live.status()["stage"] == "подключаюсь к записи…")
    status = live.status()
    assert status["starting"] is True and status["active"] is False
    assert status["ready"] is False
    assert live_control.LIVE_STAGE in rec.kinds()
    # Захват звука пошёл — это уже запись (active), но ассистент ещё не готов.
    _allow(stub, "capture")
    _wait_for(lambda: _active(live))
    status = live.status()
    assert status["ready"] is False and status["starting"] is False
    assert status["stage"] == "загружаю модель распознавания…"
    assert status["folder"].endswith("2026-10-01_10-00")
    assert status["started_at"] is not None  # часы записи — с захвата
    assert rec.last(live_control.LIVE_STARTED).data["folder"] == status["folder"]
    _allow(stub, "ready")
    _wait_for(lambda: live.status()["ready"])
    assert live.status()["stage"] is None
    assert rec.last(live_control.LIVE_STAGE).data == {"stage": None, "ready": True}


def test_old_style_endpoint_means_ready(make_live, tmp_path):
    live, _, _ = make_live()
    live.start(tmp_path / "recordings")
    _wait_for(lambda: _active(live))
    assert live.status()["ready"] is True and live.status()["stage"] is None


def test_stage_progress_extends_the_start_deadline(make_live, tmp_path):
    """Медленный, но идущий старт не убивают: таймаут — на этап, а не на весь старт."""
    live, stub, rec = make_live("staged", start_timeout=1.0, stage_timeout=1.5)
    live.start(tmp_path / "recordings")
    _wait_for(lambda: live.status()["stage"] == "подключаюсь к записи…")
    time.sleep(1.0)  # меньше этапного таймаута — жив
    _allow(stub, "capture")
    _wait_for(lambda: _active(live))
    time.sleep(1.0)
    assert live.busy() and live_control.LIVE_FAILED not in rec.kinds()
    _allow(stub, "ready")
    _wait_for(lambda: live.status()["ready"])


def test_stalled_stage_is_killed_with_its_name_in_the_error_and_log(make_live, data_dir,
                                                                   tmp_path):
    live, stub, rec = make_live("stall", stage_timeout=0.5, max_attempts=1)
    live.start(tmp_path / "recordings")
    _wait_for(lambda: live_control.LIVE_FAILED in rec.kinds())
    error = rec.last(live_control.LIVE_FAILED).data["error"]
    assert "не запустился" in error and "подключаюсь к записи" in error
    assert stub.processes[0].poll() is not None
    log = _log(data_dir)
    assert error in log  # причина — в live.log, а не только в журнале резидента


def test_silent_native_crash_leaves_its_code_in_live_log(make_live, data_dir, tmp_path):
    live, _, rec = make_live("native-crash", max_attempts=1)
    live.start(tmp_path / "recordings")
    _wait_for(lambda: live_control.LIVE_FAILED in rec.kinds())
    error = rec.last(live_control.LIVE_FAILED).data["error"]
    assert "0xC0000005" in error
    assert "0xC0000005" in _log(data_dir)


def test_failed_start_is_retried_once(make_live, data_dir, tmp_path):
    live, stub, rec = make_live("fail-once")
    live.start(tmp_path / "recordings")
    _wait_for(lambda: _active(live))
    assert len(stub.processes) == 2
    assert live_control.LIVE_FAILED not in rec.kinds()
    assert live.status()["error"] is None
    log = _log(data_dir)
    assert "RuntimeError: модель не загрузилась" in log
    assert "повторяю запуск" in log


def test_retry_is_attempted_only_once(make_live, data_dir, tmp_path):
    live, stub, rec = make_live("native-crash")
    live.start(tmp_path / "recordings")
    _wait_for(lambda: live_control.LIVE_FAILED in rec.kinds())
    assert len(stub.processes) == 2
    assert not live.busy()


def test_fatal_error_is_not_retried(make_live, data_dir, tmp_path):
    live, stub, rec = make_live("fatal")
    live.start(tmp_path / "recordings")
    _wait_for(lambda: live_control.LIVE_FAILED in rec.kinds())
    assert len(stub.processes) == 1
    assert rec.last(live_control.LIVE_FAILED).data["error"] == \
        "Авторизация Claude не прошла: войдите заново"


def test_crash_after_capture_of_own_recording_is_not_retried(make_live, data_dir, tmp_path):
    """Своя запись уже пишется (папка с дорожками) — повтор начал бы другую
    папку: сообщаем об ошибке, запись остаётся в библиотеке."""
    live, stub, rec = make_live("crash-loading")
    live.start(tmp_path / "recordings")
    _allow(stub, "capture")
    _wait_for(lambda: _active(live))
    _allow(stub, "crash")
    _wait_for(lambda: live_control.LIVE_FAILED in rec.kinds())
    failed = rec.last(live_control.LIVE_FAILED).data
    assert failed["error"] == "OSError: модель повреждена"
    assert failed["folder"].endswith("2026-10-01_10-00")
    assert len(stub.processes) == 1
    assert "OSError: модель повреждена" in _log(data_dir)


def test_stop_during_staged_start_is_graceful(make_live, tmp_path):
    """Порт известен — остановка во время загрузки модели штатная (/stop):
    уже идущая запись дописывается, а не убивается."""
    live, stub, rec = make_live("staged")
    live.start(tmp_path / "recordings")
    _allow(stub, "capture")
    _wait_for(lambda: _active(live))
    live.stop(wait=True)
    assert stub.note("stop") == [""]
    assert stub.note("stopped_before_ready") == [""]
    stopped = rec.last(live_control.LIVE_STOPPED).data
    assert stopped["error"] is None and stopped["complete"] is True
    assert stopped["folder"].endswith("2026-10-01_10-00")


def test_crash_mid_meeting_is_reported_in_live_log(make_live, data_dir, tmp_path):
    live, stub, rec = make_live()
    live.start(tmp_path / "recordings")
    _wait_for(lambda: _active(live))
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{live._port}/crash", data=b"{}", method="POST"),
            timeout=5).close()
    except OSError:
        pass  # заглушка умирает, не дописав ответ, — так и задумано
    _wait_for(lambda: live_control.LIVE_FAILED in rec.kinds())
    assert len(stub.processes) == 1  # посреди встречи — не повтор, а сообщение
    log = _log(data_dir)
    assert "ассистент завершился" in log and "код 3" in log


def _hub():
    hub = pcm_tap.TapHub()
    hub.begin(1)
    hub.configure(0, "mic.opus", 16000, 1)
    return hub


def test_attached_retry_gets_a_fresh_tap(make_live, tmp_path):
    live, stub, rec = make_live("fail-once")
    hub = _hub()
    folder = tmp_path / "rec" / "f"
    folder.mkdir(parents=True)
    first = pcm_tap.TapServer(hub)
    made = []

    def reopen():
        made.append(pcm_tap.TapServer(hub))
        return made[-1]

    live.start(tmp_path / "rec", attach={"folder": str(folder), "server": first,
                                         "reopen": reopen})
    _wait_for(lambda: _active(live))
    assert len(made) == 1 and first.closed
    argv = stub.argv
    assert argv[argv.index("--tap-port") + 1] == str(made[0].port)
    assert stub.note("env_token")[-1] == made[0].token
    live.stop(wait=True)
    _wait_for(lambda: made[0].closed)


def test_attached_start_without_reopen_is_not_retried(make_live, tmp_path):
    live, stub, rec = make_live("fail-once")
    folder = tmp_path / "rec" / "f"
    folder.mkdir(parents=True)
    live.start(tmp_path / "rec", attach={"folder": str(folder),
                                         "server": pcm_tap.TapServer(_hub())})
    _wait_for(lambda: live_control.LIVE_FAILED in rec.kinds())
    assert len(stub.processes) == 1


def test_stage_reaches_the_resident_log_once_per_change(data_dir, tmp_path):
    stub = Stub(tmp_path, "staged")
    bus = events.EventBus()
    rec = Recorder(bus)
    logged = []
    live = live_control.LiveControl(bus, spawn=stub, log=logged.append)
    try:
        live.start(tmp_path / "recordings")
        _wait_for(lambda: live.status()["stage"] == "подключаюсь к записи…")
        _allow(stub, "capture")
        _wait_for(lambda: _active(live))
        _allow(stub, "ready")
        _wait_for(lambda: live.status()["ready"])
        stages = [e.data.get("stage") for e in rec.events if e.kind == live_control.LIVE_STAGE]
        assert stages == ["подключаюсь к записи…", "загружаю модель распознавания…", None]
        assert any("слушает" in line for line in logged)
    finally:
        live.stop(wait=True)
        stub.cleanup()


def test_endpoint_reading_keeps_new_fields(tmp_path):
    path = tmp_path / "live.json"
    path.write_text(json.dumps({"port": 5, "pid": 7, "folder": "F", "ready": False,
                                "stage": "x"}), encoding="utf-8")
    assert live_control._read_endpoint(path, 7)["stage"] == "x"
