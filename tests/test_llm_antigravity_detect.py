"""Поиск Antigravity CLI (`agy`) и его место в реестре провайдеров, без запуска."""

import os
from pathlib import Path

import pytest

from meet import llm, settings
from meet.llm import detect


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    os.chmod(path, 0o755)
    return path


def test_windows_finds_the_default_install_in_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(detect, "_WINDOWS", True)
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    exe = _exe(tmp_path / "agy" / "bin" / "agy.exe")
    assert detect.find_agy() == str(exe)


def test_windows_prefers_path_exe_over_the_npm_shim(monkeypatch, tmp_path):
    monkeypatch.setattr(detect, "_WINDOWS", True)
    found = {"agy.exe": "C:/tools/agy.exe", "agy.cmd": "C:/npm/agy.cmd"}
    monkeypatch.setattr(detect.shutil, "which", lambda name: found.get(name))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert detect.find_agy() == "C:/tools/agy.exe"


def test_windows_shim_is_the_last_resort(monkeypatch, tmp_path):
    monkeypatch.setattr(detect, "_WINDOWS", True)
    monkeypatch.setattr(detect.shutil, "which", lambda name: "C:/npm/agy.cmd" if name == "agy.cmd" else None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert detect.find_agy() == "C:/npm/agy.cmd"


def test_mac_finds_local_bin_without_path(monkeypatch, tmp_path):
    monkeypatch.setattr(detect, "_WINDOWS", False)
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    exe = _exe(tmp_path / ".local" / "bin" / "agy")
    assert detect.find_agy() == str(exe)


def test_not_found_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(detect, "_WINDOWS", True)
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert detect.find_agy() is None


def test_available_lists_agy(monkeypatch):
    monkeypatch.setattr(detect, "find_agy", lambda: "C:/agy.exe")
    found = detect.available(probe_local=False)
    assert found["agy"] == {"found": True, "path": "C:/agy.exe"}


def test_logged_in_asks_acp_without_a_model(monkeypatch):
    from meet.llm import antigravity

    calls = []
    monkeypatch.setattr(antigravity, "login_status", lambda path: calls.append(path) or (False, "войдите"))
    assert detect.logged_in("agy", "C:/agy.exe") == (False, "войдите")
    assert calls == ["C:/agy.exe"]


def test_registry_labels_and_no_auto_no_vision():
    assert "agy" in llm.PROVIDERS and llm.LABELS["agy"] == "Antigravity CLI"
    assert "agy" not in llm.AUTO_PROVIDERS and "agy" not in llm.VISION_PROVIDERS
    assert llm.supports_resume("agy") and "agy" in llm.STREAMING_PROVIDERS
    assert settings.LLM_CONCRETE == llm.PROVIDERS


def test_runner_for_passes_the_agy_model(monkeypatch):
    cfg = settings.Settings.from_raw({"llm": {"provider": "agy", "agy_model": "gemini-3-pro"}})
    runner = llm.runner_for("agy", cfg)
    assert runner.keywords["model"] == "gemini-3-pro"
    assert llm.describe("agy", cfg) == {"provider": "agy", "model": "gemini-3-pro"}


def test_choice_error_when_not_found(monkeypatch):
    monkeypatch.setattr(detect, "find_agy", lambda: None)
    cfg = settings.Settings.from_raw({"llm": {"provider": "auto", "enabled": ["claude-code", "agy"]}})
    assert "agy" in cfg.llm.enabled
    assert llm.choice_error(cfg, "agy") == detect.AGY_NOT_FOUND


@pytest.mark.parametrize("value,ok", [("", True), ("gemini-3-pro", True), ("gemini-3-flash@preview", True),
                                      ("gem ini", False), ("a&b", False), ("a%b%", False), ("a^b", False)])
def test_agy_model_validation(value, ok):
    assert (settings.agy_model_error(value) is None) is ok


def test_agy_model_is_not_written_until_changed(tmp_path):
    path = tmp_path / "config.json"
    cfg = settings.Settings.from_raw({})
    settings.save(cfg, path)
    raw = settings.read_raw(path)
    assert "agy_model" not in raw["llm"]
    changed = settings.Settings.from_raw({"llm": {"agy_model": "gemini-3-pro"}})
    settings.save(changed, path)
    assert settings.read_raw(path)["llm"]["agy_model"] == "gemini-3-pro"


def test_bad_agy_model_from_the_window_is_refused():
    with pytest.raises(ValueError):
        settings.Llm.check({"agy_model": "a&b"})
