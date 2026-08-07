import json
import os
import threading
import time

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


def test_bad_numbers_fall_back_instead_of_killing_the_tray(monkeypatch, tmp_path):
    # трей стартует из автозагрузки под pythonw: исключение здесь означало бы
    # «резидента нет», без иконки и без строки в журнале
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for bad in ("три минуты", None, -5, 0):
        _write_config(tmp_path, {
            "auto_record": {"grace_seconds": bad, "poll_seconds": bad}
        })
        cfg = tray._auto_config()
        assert cfg["grace_seconds"] >= 0
        assert cfg["poll_seconds"] >= 0.5
    _write_config(tmp_path, {"auto_record": {"grace_seconds": "три минуты"}})
    assert tray.TrayApp().cfg["grace_seconds"] == watch.GRACE_S


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


def test_manual_stop_of_auto_recording_allows_restart(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app.watcher.state = watch.Watcher.RECORDING
    monkeypatch.setattr(app, "stop_recording", lambda **k: setattr(app, "recording", False))
    app._on_stop()
    assert app.watcher.state == watch.Watcher.IDLE
    assert app.watcher.poll(True, now=1.0) == watch.START


def test_manual_start_over_auto_recording_disables_autostop(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.recording = True
    app.source = tray.AUTO
    app._on_start()
    assert app.source == tray.MANUAL


# --- запись: реальные start_recording / stop_recording --------------------


def _stub_record(folder, finalize=0.0):
    """Заглушка record(): ждёт stop_event, «финализирует» и отдаёт папку."""

    def rec(out_root, stop_event=None):
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
    monkeypatch.setattr(tray, "_launch_claude", lambda f: hooks.append(f))
    app = _recording_app(monkeypatch, tmp_path, folder)
    app.stop_recording()
    assert hooks == [str(folder)]
    assert folder.exists()
    assert app.recording is False


def test_cancel_deletes_folder_and_skips_claude(monkeypatch, tmp_path):
    folder = tmp_path / "rec" / "2026-08-07_12-00"
    folder.mkdir(parents=True)
    (folder / "mic.opus").write_bytes(b"x")
    monkeypatch.setattr(tray, "_launch_claude", lambda f: 1 / 0)
    app = _recording_app(monkeypatch, tmp_path, folder)
    app.stop_recording(discard=True)
    assert not folder.exists()


def test_stop_without_hook_keeps_folder(monkeypatch, tmp_path):
    folder = tmp_path / "rec" / "2026-08-07_12-00"
    folder.mkdir(parents=True)
    monkeypatch.setattr(tray, "_launch_claude", lambda f: 1 / 0)
    app = _recording_app(monkeypatch, tmp_path, folder)
    app.stop_recording(hook=False)
    assert folder.exists()


def test_concurrent_stops_run_exactly_once(monkeypatch, tmp_path):
    # гонка «автостоп сработал ровно когда нажали Отменить»: без общего лока
    # папка удалялась и одновременно уходила в Claude
    folder = tmp_path / "rec" / "2026-08-07_12-00"
    folder.mkdir(parents=True)
    hooks = []
    monkeypatch.setattr(tray, "_launch_claude", lambda f: hooks.append(f))
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
    monkeypatch.setattr(tray, "_launch_claude", lambda f: hooks.append(f))
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
    monkeypatch.setattr(tray, "_launch_claude", lambda f: None)
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
