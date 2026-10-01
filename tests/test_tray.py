import json
import os
import threading
import time

import pytest

import meet.tray as tray
import meet.watch as watch
from meet import settings
from meet.tray import OUT_ROOT, _icon_image


def test_icon_image_is_drawable():
    img = _icon_image()
    assert img.size == (64, 64)


def test_icon_colors_differ_between_idle_and_recording():
    idle = _icon_image(tray.IDLE_COLOR).tobytes()
    rec = _icon_image(tray.REC_COLOR).tobytes()
    assert idle != rec


def test_out_root_is_repo_recordings():
    # ярлык может запускаться с любым cwd — путь должен быть абсолютным
    assert OUT_ROOT.is_absolute()
    assert OUT_ROOT.name == "recordings"
    assert (OUT_ROOT.parent / "pyproject.toml").exists()


def test_post_hook_silent_for_a_fresh_install(monkeypatch):
    """У нового пользователя команда пуста — не запускается ничего."""
    calls = []
    monkeypatch.setattr(tray, "_config", lambda: {})
    monkeypatch.setattr(tray.subprocess, "Popen", lambda *a, **k: calls.append(a))
    tray._run_post_hook(r"C:\rec\2026-07-19_10-00")
    assert calls == []


def test_post_hook_silent_while_flag_is_off(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tray, "_config",
        lambda: {"hooks": {"post_record": False, "command": ["notepad", "{folder}"]}},
    )
    monkeypatch.setattr(tray.subprocess, "Popen", lambda *a, **k: calls.append(a))
    tray._run_post_hook(r"C:\rec\2026-07-19_10-00")
    assert calls == []


def test_post_hook_substitutes_placeholders(monkeypatch):
    """Шаблон команды — обычная программа с аргументами, не обязательно ассистент."""
    calls = []
    monkeypatch.setattr(
        tray, "_config",
        lambda: {"hooks": {"post_record": True,
                           "command": ["explorer", "{folder}", "{date}"]}},
    )
    monkeypatch.setattr(tray.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    tray._run_post_hook(r"C:\rec\2026-07-19_10-00")
    assert calls == [["explorer", r"C:\rec\2026-07-19_10-00", "2026-07-19"]]


def test_migrated_config_keeps_the_historic_hook(monkeypatch):
    """У того, кто пользовался хуком раньше, он остаётся прежним: миграция
    достраивает старому конфигу команду, которая была зашита в коде."""
    calls = []
    monkeypatch.setattr(tray, "_config", lambda: {"post_record_hook": True})
    monkeypatch.setattr(tray.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    folder = r"C:\rec\2026-07-19_10-00"
    tray._run_post_hook(folder)
    (cmd,) = calls
    assert cmd[:3] == ["wt", "-d", str(OUT_ROOT.parent)]
    assert "powershell" in cmd
    assert any(part.startswith("claude ") and folder in part for part in cmd)
    # 10-00 — не слот регулярной встречи, подсказки про календарь быть не должно
    assert not any("calendar_lookup" in part for part in cmd)


def test_recurring_hint_added_inside_window(monkeypatch):
    calls = []
    monkeypatch.setattr(tray, "_config", lambda: {"post_record_hook": True})
    monkeypatch.setattr(tray.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    tray._run_post_hook(r"C:\rec\2026-07-24_11-28")
    (cmd,) = calls
    prompt = next(part for part in cmd if part.startswith("claude "))
    assert "регулярной" in prompt and "calendar_lookup" in prompt
    # промпт идёт в одинарных кавычках powershell — апостроф внутри его сломает
    assert prompt.count("'") == 2
    # ';' wt считает разделителем команд: хвост промпта уходил в запуск файла
    assert ";" not in prompt


def test_hook_command_is_not_a_shell_string(monkeypatch):
    """Команда запускается списком аргументов: значение из конфига не должно
    превращаться в исполняемую строку шелла."""
    seen = {}
    monkeypatch.setattr(
        tray, "_config",
        lambda: {"hooks": {"post_record": True, "command": ["echo", "{folder}"]}},
    )
    monkeypatch.setattr(
        tray.subprocess, "Popen",
        lambda cmd, **kwargs: seen.update(cmd=cmd, kwargs=kwargs),
    )
    tray._run_post_hook(r"C:\rec\2026-07-19_10-00")
    assert isinstance(seen["cmd"], list)
    assert "shell" not in seen["kwargs"]


def test_wt_safe_neutralizes_command_line_metachars():
    assert tray._wt_safe("режимом дейлика; другую встречу") == (
        "режимом дейлика, другую встречу"
    )
    assert tray._wt_safe("папка 'rec'") == "папка ''rec''"


WINDOW = ("11:00", "12:00")


def test_recurring_window_bounds():
    assert tray._in_recurring_window(r"C:\rec\2026-07-24_11-00", WINDOW)
    assert tray._in_recurring_window(r"C:\rec\2026-07-24_12-00", WINDOW)
    assert not tray._in_recurring_window(r"C:\rec\2026-07-24_10-59", WINDOW)
    assert not tray._in_recurring_window(r"C:\rec\2026-07-24_12-01", WINDOW)
    assert not tray._in_recurring_window(r"C:\rec\test", WINDOW)  # без времени в имени


def test_no_recurring_window_means_no_hint():
    # у нового пользователя окно не задано: про регулярность молчим
    assert not tray._in_recurring_window(r"C:\rec\2026-07-24_11-30", None)


def test_config_empty_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert tray._config() == {}


# --- настройки автозаписи -------------------------------------------------


def _write_config(tmp_path, body: dict) -> None:
    path = tmp_path / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_auto_config_defaults_to_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cfg = tray._auto_config()
    assert cfg["enabled"] is False
    # список клиентов конференций, а не один Дион: приложение общего назначения
    assert "Dion.exe" in cfg["processes"] and "Zoom.exe" in cfg["processes"]
    assert cfg["grace_minutes"] * 60 == watch.GRACE_S
    assert cfg["min_call_seconds"] == tray.MIN_CALL_S


def test_auto_config_reads_file(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _write_config(tmp_path, {
        "auto_record": {
            "enabled": True, "processes": ["Foo.exe"], "grace_minutes": 30,
            "poll_seconds": 5,
        }
    })
    cfg = tray._auto_config()
    assert {k: cfg[k] for k in ("enabled", "processes", "grace_minutes",
                                 "poll_seconds", "min_call_seconds", "browsers")} == {
        "enabled": True, "processes": ["Foo.exe"],
        "grace_minutes": 30.0, "poll_seconds": 5.0,
        "min_call_seconds": tray.MIN_CALL_S, "browsers": [],
    }


def test_auto_config_survives_garbage_section(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _write_config(tmp_path, {"auto_record": {"processes": []}})
    assert tray._auto_config()["processes"] == list(settings.DEFAULT_PROCESSES)


def test_bad_numbers_fall_back_instead_of_killing_the_tray(monkeypatch, tmp_path):
    # трей стартует из автозагрузки под pythonw: исключение здесь означало бы
    # «резидента нет», без иконки и без строки в журнале
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for bad in ("три минуты", None, -5, 0):
        _write_config(tmp_path, {
            "auto_record": {"grace_minutes": bad, "poll_seconds": bad}
        })
        cfg = tray._auto_config()
        assert 1 <= cfg["grace_minutes"] <= 60
        assert cfg["poll_seconds"] >= 0.5
    _write_config(tmp_path, {"auto_record": {"grace_minutes": "три минуты"}})
    assert tray.TrayApp().watcher.grace_seconds == watch.GRACE_S


def test_watcher_waits_the_configured_minutes(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _write_config(tmp_path, {"auto_record": {"grace_minutes": 25}})
    assert tray.TrayApp().watcher.grace_seconds == 25 * 60


def test_string_false_does_not_enable_auto_record(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _write_config(tmp_path, {"auto_record": {"enabled": "false"}})
    assert tray._auto_config()["enabled"] is False


def test_single_process_may_be_a_string(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _write_config(tmp_path, {"auto_record": {"processes": "Teams.exe"}})
    assert tray._auto_config()["processes"] == ["Teams.exe"]


# --- резидент и команды от ярлыка ----------------------------------------


def test_no_resident_without_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert tray._resident_alive() is False


def test_resident_alive_for_living_pid(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    lock = tmp_path / "meet" / "tray.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"pid": os.getpid(), **tray._proc_ident(os.getpid())}),
        encoding="utf-8",
    )
    assert tray._resident_alive() is True


def test_reused_pid_is_not_our_resident(monkeypatch, tmp_path):
    # gui-script живёт под pythonw.exe, и таких процессов на машине несколько:
    # имени мало, различает запуски время старта
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    lock = tmp_path / "meet" / "tray.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    ident = tray._proc_ident(os.getpid())
    lock.write_text(
        json.dumps({"pid": os.getpid(), "name": ident.get("name"),
                    "started": (ident.get("started") or 0) - 3600}),
        encoding="utf-8",
    )
    assert tray._resident_alive() is False


def test_old_format_lock_still_trusted(monkeypatch, tmp_path):
    # lock, записанный прежней версией трея, не должен считаться чужим
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    lock = tmp_path / "meet" / "tray.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    assert tray._resident_alive() is True


def test_stale_lock_is_not_a_resident(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    lock = tmp_path / "meet" / "tray.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 0x7FFFFFFF}), encoding="utf-8")
    assert tray._resident_alive() is False


def test_command_is_taken_once(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    tray._send_command("start")
    assert tray._take_command() == "start"
    assert tray._take_command() is None


def test_shortcut_delegates_to_living_resident(monkeypatch, tmp_path):
    # ярлык без аргументов — прежнее поведение: «запусти запись»
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(tray, "_resident_alive", lambda: True)
    monkeypatch.setattr(tray.sys, "argv", ["meet-tray"])
    monkeypatch.setattr(tray.TrayApp, "run", lambda self: (_ for _ in ()).throw(
        AssertionError("вторая иконка подниматься не должна")
    ))
    tray.main()
    assert tray._take_command() == "start"


def test_shortcut_records_when_no_resident(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(tray, "_resident_alive", lambda: False)
    monkeypatch.setattr(tray.sys, "argv", ["meet-tray"])
    started = []
    monkeypatch.setattr(tray.TrayApp, "run", lambda self: started.append(self.start_now))
    tray.main()
    assert started == [True]


def test_watch_flag_only_stands_by(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(tray, "_resident_alive", lambda: False)
    monkeypatch.setattr(tray.sys, "argv", ["meet-tray", "--watch"])
    started = []
    monkeypatch.setattr(tray.TrayApp, "run", lambda self: started.append(self.start_now))
    tray.main()
    assert started == [False]


def test_second_watcher_exits_without_command(monkeypatch, tmp_path):
    # автозагрузка отработала дважды — вторая иконка не нужна и запись не нужна
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(tray, "_resident_alive", lambda: True)
    monkeypatch.setattr(tray.sys, "argv", ["meet-tray", "--watch"])
    monkeypatch.setattr(tray.TrayApp, "run", lambda self: (_ for _ in ()).throw(
        AssertionError("вторая иконка подниматься не должна")
    ))
    tray.main()
    assert tray._take_command() is None


# --- автозапись -----------------------------------------------------------


def _app(monkeypatch, tmp_path, enabled=True):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    if enabled:
        _write_config(tmp_path, {"auto_record": {"enabled": True}})
    return tray.TrayApp()


def test_auto_start_launches_recording(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(app, "start_recording", lambda source: calls.append(source))
    app._auto_start()
    assert calls == [tray.AUTO]


def test_auto_start_leaves_running_recording_alone(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.MANUAL
    monkeypatch.setattr(app, "start_recording", lambda source: 1 / 0)
    app._auto_start()  # не должно ни стартовать, ни падать


def test_auto_stop_stops_auto_recording(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app.started = 0.0
    calls = []
    monkeypatch.setattr(app, "stop_recording", lambda hook=True: calls.append(hook))
    app._auto_stop(now=tray.MIN_CALL_S + 10)
    assert calls == [True]


def test_short_call_stops_without_calling_claude(monkeypatch, tmp_path):
    # звук уведомления Диона не должен приводить к окну Claude с предложением
    # транскрибировать полминуты тишины
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app.started = 0.0
    app._call_end = 10.0
    calls = []
    monkeypatch.setattr(app, "stop_recording", lambda hook=True: calls.append(hook))
    app._auto_stop(now=10.0 + watch.GRACE_S)
    assert calls == [False]


def test_auto_stop_never_touches_manual_recording(monkeypatch, tmp_path):
    # надиктовка или телефонный разговор не должны обрываться из-за того,
    # что в Дионе нет конференции
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.MANUAL
    monkeypatch.setattr(app, "stop_recording", lambda **k: 1 / 0)
    app._auto_stop(now=1000.0)
    assert app.recording is True


def _fixed_signals(app, monkeypatch, call, mic=True, render=None):
    monkeypatch.setattr(app.signals, "read", lambda now: (call, mic, render))


def test_disabled_auto_record_only_logs(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=False)
    _fixed_signals(app, monkeypatch, call=True)
    monkeypatch.setattr(app, "_auto_start", lambda: 1 / 0)
    app._watch_tick()
    assert app.watcher.state == watch.Watcher.RECORDING  # машина крутится
    assert "микрофон да" in (tmp_path / "meet" / "watch.log").read_text("utf-8")


def test_auto_record_off_mid_auto_recording_still_auto_stops(monkeypatch, tmp_path):
    """Выключили автозапись, пока она пишет звонок: STOP детектор выдаёт один
    раз, и проглоти его трей — запись шла бы до выхода, вечно."""
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app.started = time.monotonic() - 600
    app.watcher.state = watch.Watcher.RECORDING
    app.cfg["enabled"] = False  # переключатель в трее/настройках
    _fixed_signals(app, monkeypatch, call=False)
    monkeypatch.setattr(app.watcher, "poll", lambda call, now: watch.STOP)
    stops = []
    monkeypatch.setattr(app, "stop_recording", lambda hook=True: stops.append(hook))
    app._watch_tick()
    assert stops == [True]


def test_auto_record_off_never_starts_a_recording(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.cfg["enabled"] = False
    _fixed_signals(app, monkeypatch, call=True)
    monkeypatch.setattr(app.watcher, "poll", lambda call, now: watch.START)
    monkeypatch.setattr(app, "start_recording", lambda source: 1 / 0)
    app._watch_tick()
    assert app.recording is False


def test_enabled_auto_record_reacts_to_call(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    _fixed_signals(app, monkeypatch, call=True)
    calls = []
    monkeypatch.setattr(app, "_auto_start", lambda: calls.append("start"))
    app._watch_tick()
    assert calls == ["start"]


def test_failed_recording_returns_tray_to_idle(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app.watcher.state = watch.Watcher.RECORDING
    app.result = {"error": "Запись уже идёт (папка X)"}
    app._collect_error()
    assert app.recording is False
    assert app.source is None
    # без сброса машины повторной попытки не было бы до конца звонка,
    # а звонок может идти ещё два часа
    assert app.watcher.state == watch.Watcher.IDLE


def test_manual_stop_during_a_call_does_not_restart_recording(monkeypatch, tmp_path):
    # нажал «Остановить», а звонок идёт: сброс в IDLE заставил бы машину через
    # такт выдать START, то есть кнопка не работала бы вовсе
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app.watcher.state = watch.Watcher.RECORDING
    monkeypatch.setattr(app, "stop_recording", lambda **k: setattr(app, "recording", False))
    app._on_stop()
    assert app.watcher.poll(True, now=1.0) == watch.NONE
    assert app.watcher.poll(True, now=100.0) == watch.NONE


def test_cancel_during_a_call_does_not_restart_recording(monkeypatch, tmp_path):
    # иначе «Отменить (удалить)» удаляет папку, и через две секунды пишется заново
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app.watcher.state = watch.Watcher.RECORDING
    monkeypatch.setattr(app, "stop_recording", lambda **k: setattr(app, "recording", False))
    app._on_cancel()
    assert app.watcher.poll(True, now=1.0) == watch.NONE


def test_next_call_records_again_after_manual_stop(monkeypatch, tmp_path):
    # подавление действует только до конца текущего звонка
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app.watcher.state = watch.Watcher.RECORDING
    monkeypatch.setattr(app, "stop_recording", lambda **k: setattr(app, "recording", False))
    app._on_stop()
    app.watcher.poll(True, now=1.0)
    app.watcher.poll(False, now=2.0)  # звонок кончился
    assert app.watcher.poll(True, now=3.0) == watch.START


def test_failed_start_retries_later_not_immediately(monkeypatch, tmp_path):
    # повтор нужен, иначе встреча теряется целиком; но не каждые две секунды
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app.watcher.state = watch.Watcher.RECORDING
    app.result = {"error": "нет устройства"}
    app._collect_error()
    attempts = []
    monkeypatch.setattr(app, "start_recording", lambda source: attempts.append(source))
    app._auto_start()
    assert attempts == []  # слишком рано
    app._retry_after = time.monotonic() - 1
    app._auto_start()
    assert attempts == [tray.AUTO]


def test_manual_start_over_auto_recording_disables_autostop(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app._on_start()
    assert app.source == tray.MANUAL


# --- запись: реальные start_recording / stop_recording --------------------


def _stub_record(folder, finalize=0.0):
    """Заглушка record(): ждёт stop_event, «финализирует» и отдаёт папку.

    bus без default: трей обязан передавать свою шину событий — без неё панель
    не видит ни уровней дорожек, ни хода записи, и тест падает TypeError."""

    def rec(out_root, stop_event=None, *, bus):
        assert bus is not None
        stop_event.wait(timeout=5)
        time.sleep(finalize)
        return folder

    return rec


def _recording_app(monkeypatch, tmp_path, folder, finalize=0.0, source=tray.AUTO):
    app = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(tray, "record", _stub_record(folder, finalize))
    monkeypatch.setattr(tray, "OUT_ROOT", tmp_path / "recordings")
    app.start_recording(source)
    return app


def test_stop_saves_and_calls_claude_once(monkeypatch, tmp_path):
    folder = tmp_path / "rec" / "2026-08-07_12-00"
    folder.mkdir(parents=True)
    hooks = []
    monkeypatch.setattr(tray, "_run_post_hook", lambda f: hooks.append(f))
    app = _recording_app(monkeypatch, tmp_path, folder)
    app.stop_recording()
    assert hooks == [str(folder)]
    assert folder.exists()
    assert app.recording is False


def test_cancel_deletes_folder_and_skips_claude(monkeypatch, tmp_path):
    folder = tmp_path / "rec" / "2026-08-07_12-00"
    folder.mkdir(parents=True)
    (folder / "mic.opus").write_bytes(b"x")
    monkeypatch.setattr(tray, "_run_post_hook", lambda f: 1 / 0)
    app = _recording_app(monkeypatch, tmp_path, folder)
    app.stop_recording(discard=True)
    assert not folder.exists()


def test_stop_without_hook_keeps_folder(monkeypatch, tmp_path):
    folder = tmp_path / "rec" / "2026-08-07_12-00"
    folder.mkdir(parents=True)
    monkeypatch.setattr(tray, "_run_post_hook", lambda f: 1 / 0)
    app = _recording_app(monkeypatch, tmp_path, folder)
    app.stop_recording(hook=False)
    assert folder.exists()


@pytest.mark.parametrize("kwargs, reason", [
    ({}, "saved"),
    ({"discard": True}, "discarded"),
    ({"hook": False}, "short"),
])
def test_every_stop_leaves_its_reason(monkeypatch, tmp_path, kwargs, reason):
    """Оболочка по last_stop отличает «сохранена» от «отменена»: отмену из окна
    приложения она иначе не видит и сообщала бы «Запись сохранена»."""
    folder = tmp_path / "rec" / "2026-08-07_12-00"
    folder.mkdir(parents=True)
    monkeypatch.setattr(tray, "_run_post_hook", lambda f: None)
    app = _recording_app(monkeypatch, tmp_path, folder)
    assert app.last_stop is None
    before = time.time()
    app.stop_recording(**kwargs)
    assert app.last_stop["folder"] == str(folder)
    assert app.last_stop["reason"] == reason
    assert before - 1 <= app.last_stop["at"] <= time.time() + 1


def test_last_stop_is_known_by_the_time_recording_flag_drops(monkeypatch, tmp_path):
    """Опрос оболочки может прийти между снятием флага и удалением папки: в
    этот момент причина уже должна быть видна, иначе отмена прочиталась бы
    как сохранение."""
    folder = tmp_path / "rec" / "2026-08-07_12-00"
    folder.mkdir(parents=True)
    app = _recording_app(monkeypatch, tmp_path, folder)
    seen = []
    real_rmtree = tray.shutil.rmtree

    def rmtree(path, **kwargs):
        seen.append((app.recording, dict(app.last_stop or {}).get("reason")))
        real_rmtree(path, **kwargs)

    monkeypatch.setattr(tray.shutil, "rmtree", rmtree)
    app.stop_recording(discard=True)
    assert seen == [(False, "discarded")]


def test_concurrent_stops_run_exactly_once(monkeypatch, tmp_path):
    # гонка «автостоп сработал ровно когда нажали Отменить»: без общего лока
    # папка удалялась и одновременно уходила в Claude
    folder = tmp_path / "rec" / "2026-08-07_12-00"
    folder.mkdir(parents=True)
    hooks = []
    monkeypatch.setattr(tray, "_run_post_hook", lambda f: hooks.append(f))
    removed = []
    monkeypatch.setattr(tray.shutil, "rmtree", lambda f, **k: removed.append(f))
    app = _recording_app(monkeypatch, tmp_path, folder, finalize=0.3)
    ready = threading.Barrier(2)

    def stopper(discard):
        ready.wait()
        app.stop_recording(discard=discard)

    threads = [
        threading.Thread(target=stopper, args=(False,)),
        threading.Thread(target=stopper, args=(True,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(hooks) + len(removed) == 1  # ровно один исход, не оба сразу


def test_second_stop_is_a_noop(monkeypatch, tmp_path):
    folder = tmp_path / "rec" / "2026-08-07_12-00"
    folder.mkdir(parents=True)
    hooks = []
    monkeypatch.setattr(tray, "_run_post_hook", lambda f: hooks.append(f))
    app = _recording_app(monkeypatch, tmp_path, folder)
    app.stop_recording()
    app.stop_recording()
    assert hooks == [str(folder)]


def test_late_thread_does_not_leak_into_next_recording(monkeypatch, tmp_path):
    # истёкший join оставляет поток живым: он должен дописать результат в свой
    # словарь, а не в состояние следующей записи
    old = tmp_path / "rec" / "старая"
    old.mkdir(parents=True)
    app = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(tray, "record", _stub_record(old, finalize=0.4))
    app.start_recording(tray.AUTO)
    first_result = app.result
    app.stop_event.set()
    app.thread.join(timeout=0.05)  # эмулируем истёкший join
    app.recording = False
    app.thread = None
    monkeypatch.setattr(tray, "record", _stub_record(tmp_path / "rec" / "новая"))
    app.start_recording(tray.AUTO)
    second_result = app.result
    time.sleep(0.6)  # старый поток за это время дописывает свой результат
    assert first_result.get("folder") == old
    assert second_result.get("folder") is None or second_result["folder"] != old


def test_start_clears_own_stale_recording_lock(monkeypatch, tmp_path):
    # pid в lock теперь принадлежит резиденту и всегда жив — механизм
    # «протухший lock перезапишется» без этого перестал работать
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    lock = recordings / tray.LOCK_NAME
    lock.write_text(json.dumps({"pid": os.getpid(), "folder": "X"}), encoding="utf-8")
    app = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(tray, "OUT_ROOT", recordings)
    monkeypatch.setattr(tray, "record", _stub_record(tmp_path / "rec"))
    app.start_recording(tray.AUTO)
    assert not lock.exists()
    app.stop_event.set()
    app.thread.join(timeout=5)


def test_stop_command_stops_real_recording_and_keeps_resident(monkeypatch, tmp_path):
    # pid в .recording.lock принадлежит резиденту: taskkill по нему убил бы и
    # дежурного, поэтому остановка снаружи идёт командой
    folder = tmp_path / "rec" / "2026-08-07_12-00"
    folder.mkdir(parents=True)
    monkeypatch.setattr(tray, "_run_post_hook", lambda f: None)
    app = _recording_app(monkeypatch, tmp_path, folder, source=tray.MANUAL)
    monkeypatch.setattr(app, "_watch_tick", lambda: None)
    tray._send_command("stop")
    app._tick()
    assert app.recording is False
    assert app._alive is True


def test_unknown_command_is_logged(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_watch_tick", lambda: None)
    tray._send_command("stpo")
    app._tick()
    assert "stpo" in (tmp_path / "meet" / "watch.log").read_text("utf-8")


def test_resident_drops_command_left_by_dead_session(monkeypatch, tmp_path):
    # ярлык нажали, когда трей уже умирал: команда не должна пролежать до
    # следующей загрузки Windows и запустить запись на пустом месте
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    tray._send_command("start")
    tray._drop_command()
    assert tray._take_command() is None


def test_stale_command_dropped_on_every_startup(monkeypatch, tmp_path):
    # в том числе при запуске ярлыком: залипший «stop» иначе погасил бы
    # запись, которую этим же запуском только что начали
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    tray._send_command("stop")
    started = []

    class _FakeIcon:
        def __init__(self, *a, **k):
            pass

        def run(self, setup=None):
            started.append(tray._take_command())

    import sys
    import types

    fake = types.ModuleType("pystray")
    fake.Icon = _FakeIcon
    fake.Menu = lambda *items: None
    fake.MenuItem = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "pystray", fake)
    tray.TrayApp(start_now=True).run()
    assert started == [None]  # команда снята до запуска цикла


def test_folder_of_running_recording_comes_from_lock(monkeypatch, tmp_path):
    # в self.result папка появляется только когда record() вернулась, то есть
    # уже после остановки — во время записи её знает только lock
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    (recordings / tray.LOCK_NAME).write_text(
        json.dumps({"pid": os.getpid(), "folder": str(recordings / "2026-08-07_12-00")}),
        encoding="utf-8",
    )
    app = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(tray, "OUT_ROOT", recordings)
    assert "2026-08-07_12-00" in app._current_folder()


def test_own_lock_of_a_living_thread_is_kept(monkeypatch, tmp_path):
    # доживающий после истёкшего join поток ещё пишет и держит рабочий lock:
    # сняв его, мы пустили бы вторую запись в ту же папку
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    lock = recordings / tray.LOCK_NAME
    lock.write_text(json.dumps({"pid": os.getpid(), "folder": "X"}), encoding="utf-8")
    app = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(tray, "OUT_ROOT", recordings)
    alive = threading.Event()
    app.thread = threading.Thread(target=alive.wait, daemon=True)
    app.thread.start()
    try:
        app._clear_own_lock()
        assert lock.exists()
    finally:
        alive.set()
        app.thread.join(timeout=5)
    app._clear_own_lock()  # поток кончился — теперь снять можно
    assert not lock.exists()


def test_headless_runs_ticker_without_icon_and_exits_on_request(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    app = tray.TrayApp(start_now=False)
    monkeypatch.setattr(app, "_start_control_api", lambda: None)
    ticks = []
    monkeypatch.setattr(app, "_tick", lambda: (ticks.append(1),
                                               len(ticks) >= 3 and app.request_exit()))
    monkeypatch.setattr(tray, "TICK_S", 0.01)
    app.run_headless(parent_pid=None)
    assert app.icon is None
    assert len(ticks) >= 3
    assert not (tmp_path / "meet" / "tray.lock").exists()  # lock снят при выходе


def test_headless_exits_when_parent_dies(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = tray.TrayApp(start_now=False)
    monkeypatch.setattr(app, "_start_control_api", lambda: None)
    monkeypatch.setattr(app, "_tick", lambda: None)
    monkeypatch.setattr(tray, "TICK_S", 0.01)
    monkeypatch.setattr(tray, "_pid_alive", lambda pid: False)
    app.run_headless(parent_pid=999999)  # возвращается, а не висит
    assert app._alive is False


def test_main_headless_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    seen = {}
    monkeypatch.setattr(tray, "_resident_alive", lambda: False)
    monkeypatch.setattr(tray.TrayApp, "run_headless",
                        lambda self, parent_pid: seen.update(start=self.start_now,
                                                             parent=parent_pid))
    monkeypatch.setattr(tray.sys, "argv", ["meet-tray", "--headless", "--parent-pid", "42"])
    tray.main()
    assert seen == {"start": False, "parent": 42}


def test_main_headless_when_resident_already_runs_exits_distinctly(monkeypatch, tmp_path, capsys):
    """Оболочка запускает `--headless`, а дежурный уже есть (ярлык, автозагрузка):
    внятная строка и отдельный код выхода 3, а не тихий 0 — иначе оболочка
    сочла бы, что резидент поднялся и тут же упал, и крутила бы перезапуски."""
    import pytest

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setattr(tray, "_resident_alive", lambda: True)
    ran = []
    monkeypatch.setattr(tray.TrayApp, "run_headless",
                        lambda self, parent_pid: ran.append(parent_pid))
    monkeypatch.setattr(tray.sys, "argv", ["meet-tray", "--headless"])
    with pytest.raises(SystemExit) as exc:
        tray.main()
    assert exc.value.code == tray.EXIT_ALREADY_RUNNING == 3
    assert ran == []
    assert "уже" in capsys.readouterr().err


# --- звонки в браузере --------------------------------------------------------


def test_signals_get_browser_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _write_config(tmp_path, {"auto_record": {
        "enabled": True, "browsers": ["chrome.exe"], "browser_require_site": True,
        "call_sites": ["Dion"],
    }})
    app = tray.TrayApp()
    assert app.signals.browsers == ["chrome.exe"]
    assert app.signals.require_site is True
    assert app.signals.sites == ["Dion"]


def test_browser_call_start_is_logged_and_titles_the_recording(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    _fixed_signals(app, monkeypatch, call=True)
    app.signals.browser_call = {"exe": "chrome.exe", "site": "Dion",
                                "title": "Dion — Планёрка отдела"}
    monkeypatch.setattr(app, "start_recording", lambda source: True)
    app._watch_tick()
    assert app.recording_title == "Dion — Планёрка отдела"
    log = (tmp_path / "meet" / "watch.log").read_text("utf-8")
    assert "звонок в браузере: chrome.exe, сайт Dion" in log


def test_desktop_call_keeps_recording_untitled(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.recording_title = "от прошлого звонка"
    _fixed_signals(app, monkeypatch, call=True, mic=True, render=False)
    monkeypatch.setattr(app, "start_recording", lambda source: True)
    app._watch_tick()
    assert app.recording_title is None
    log = (tmp_path / "meet" / "watch.log").read_text("utf-8")
    assert "звонок в программе" in log


def test_browser_note_is_logged_once_per_change(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=False)
    _fixed_signals(app, monkeypatch, call=False, mic=False)
    app.signals.browser_note = "chrome.exe: микрофон занят, сайта звонка в заголовках окон нет"
    app._last_poll = -100
    app._watch_tick()
    app._last_poll = -100
    app._watch_tick()
    log = (tmp_path / "meet" / "watch.log").read_text("utf-8")
    assert log.count("сайта звонка в заголовках окон нет") == 1


def test_auto_stop_remembers_when_the_call_signal_ended(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app.started = 100.0
    app._call_end = 400.0  # монотонные часы: сигнал пропал за 600 с до остановки
    monkeypatch.setattr(app, "stop_recording", lambda hook=True: None)
    monkeypatch.setattr(tray.time, "time", lambda: 50_000.0)
    app._auto_stop(now=1000.0)
    assert app.call_end_at == 49_400.0
