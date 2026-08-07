import json
import os

import meet.tray as tray
import meet.watch as watch
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


def test_launch_claude_silent_without_config_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(tray, "_config", lambda: {})
    monkeypatch.setattr(tray.subprocess, "Popen", lambda *a, **k: calls.append(a))
    tray._launch_claude(r"C:\rec\2026-07-19_10-00")
    assert calls == []


def test_launch_claude_opens_terminal_in_project_root(monkeypatch):
    calls = []
    monkeypatch.setattr(tray, "_config", lambda: {"post_record_hook": True})
    monkeypatch.setattr(tray.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    folder = r"C:\rec\2026-07-19_10-00"
    tray._launch_claude(folder)
    (cmd,) = calls
    assert cmd[:3] == ["wt", "-d", str(OUT_ROOT.parent)]
    # запуск через powershell с профилем, claude напрямую: промпт с папкой внутри
    assert "powershell" in cmd
    assert any(part.startswith("claude ") and folder in part for part in cmd)
    # 10-00 — не слот дейлика, подсказки про календарь быть не должно
    assert not any("calendar_lookup" in part for part in cmd)


def test_launch_claude_hints_daily_skill_inside_window(monkeypatch):
    calls = []
    monkeypatch.setattr(tray, "_config", lambda: {"post_record_hook": True})
    monkeypatch.setattr(tray.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    tray._launch_claude(r"C:\rec\2026-07-24_11-28")
    (cmd,) = calls
    prompt = next(part for part in cmd if part.startswith("claude "))
    assert "дейлик" in prompt and "calendar_lookup" in prompt
    # промпт идёт в одинарных кавычках powershell — апостроф внутри его сломает
    assert prompt.count("'") == 2
    # ';' wt считает разделителем команд: хвост промпта уходил в запуск файла
    assert ";" not in prompt


def test_wt_safe_neutralizes_command_line_metachars():
    assert tray._wt_safe("режимом дейлика; другую встречу") == (
        "режимом дейлика, другую встречу"
    )
    assert tray._wt_safe("папка 'rec'") == "папка ''rec''"


def test_daily_window_bounds():
    assert tray._in_daily_window(r"C:\rec\2026-07-24_11-00")
    assert tray._in_daily_window(r"C:\rec\2026-07-24_12-00")
    assert not tray._in_daily_window(r"C:\rec\2026-07-24_10-59")
    assert not tray._in_daily_window(r"C:\rec\2026-07-24_12-01")
    assert not tray._in_daily_window(r"C:\rec\test")  # папка без времени в имени


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
    assert cfg["processes"] == ["Dion.exe"]
    assert cfg["grace_seconds"] == watch.GRACE_S


def test_auto_config_reads_file(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _write_config(tmp_path, {
        "auto_record": {
            "enabled": True, "processes": ["Foo.exe"], "grace_seconds": 30,
            "poll_seconds": 5,
        }
    })
    cfg = tray._auto_config()
    assert cfg == {
        "enabled": True, "processes": ["Foo.exe"],
        "grace_seconds": 30.0, "poll_seconds": 5.0,
    }


def test_auto_config_survives_garbage_section(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _write_config(tmp_path, {"auto_record": {"processes": []}})
    assert tray._auto_config()["processes"] == ["Dion.exe"]


# --- резидент и команды от ярлыка ----------------------------------------


def test_no_resident_without_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert tray._resident_alive() is False


def test_resident_alive_for_living_pid(monkeypatch, tmp_path):
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
    calls = []
    monkeypatch.setattr(app, "stop_recording", lambda: calls.append("stop"))
    app._auto_stop()
    assert calls == ["stop"]


def test_auto_stop_never_touches_manual_recording(monkeypatch, tmp_path):
    # надиктовка или телефонный разговор не должны обрываться из-за того,
    # что в Дионе нет конференции
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.MANUAL
    monkeypatch.setattr(app, "stop_recording", lambda: 1 / 0)
    app._auto_stop()
    assert app.recording is True


def test_disabled_auto_record_only_logs(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, enabled=False)
    monkeypatch.setattr(watch, "in_call", lambda names: (True, True, None))
    monkeypatch.setattr(app, "_auto_start", lambda: 1 / 0)
    app._watch_tick()
    assert app.watcher.state == watch.Watcher.RECORDING  # машина крутится
    assert "микрофон да" in (tmp_path / "meet" / "watch.log").read_text("utf-8")


def test_enabled_auto_record_reacts_to_call(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(watch, "in_call", lambda names: (True, True, None))
    calls = []
    monkeypatch.setattr(app, "_auto_start", lambda: calls.append("start"))
    app._watch_tick()
    assert calls == ["start"]


def test_stop_command_stops_recording_without_killing_resident(monkeypatch, tmp_path):
    # pid в .recording.lock теперь принадлежит резиденту: taskkill по нему убил
    # бы и дежурного, поэтому остановка снаружи идёт командой
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    calls = []
    monkeypatch.setattr(app, "stop_recording", lambda: calls.append("stop"))
    monkeypatch.setattr(app, "_watch_tick", lambda: None)
    tray._send_command("stop")
    app._tick()
    assert calls == ["stop"]
    assert app._alive is True


def test_failed_recording_returns_tray_to_idle(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app.result = {"error": "Запись уже идёт (папка X)"}
    app._collect_error()
    assert app.recording is False
    assert app.source is None
