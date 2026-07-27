import meet.tray as tray
from meet.tray import OUT_ROOT, _icon_image


def test_icon_image_is_drawable():
    img = _icon_image()
    assert img.size == (64, 64)


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
