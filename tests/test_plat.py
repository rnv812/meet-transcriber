"""Платформенный слой (`meet.plat`): папка данных, «жив ли процесс», открыть
без окна — на Windows как раньше, на macOS — свои пути и команды. Ветка macOS
проверяется на Windows подменой `sys.platform`."""

import os
import sys
from pathlib import Path

import pytest

from meet import paths, plat


@pytest.fixture
def mac(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_platform_flags_follow_sys_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert plat.is_windows() and not plat.is_macos()
    monkeypatch.setattr(sys, "platform", "darwin")
    assert plat.is_macos() and not plat.is_windows()


def test_windows_data_dir_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "meet"
    monkeypatch.delenv("LOCALAPPDATA")
    assert paths.data_dir() == Path(".") / "meet"


def test_mac_data_dir_is_application_support(mac, monkeypatch):
    assert paths.data_dir() == mac / "Library" / "Application Support" / "meet"
    assert paths.config_path() == paths.data_dir() / "config.json"
    assert paths.engine_dir() == paths.data_dir() / "engine"
    # MEET_DATA_DIR побеждает на любой ОС (портативный режим, тесты).
    monkeypatch.setenv("MEET_DATA_DIR", str(mac / "portable"))
    assert paths.data_dir() == mac / "portable"


def test_no_window_flag_only_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert plat.no_window() == 0
    monkeypatch.setattr(sys, "platform", "win32")
    assert plat.no_window() == getattr(plat.subprocess, "CREATE_NO_WINDOW", 0)


def test_pid_alive_on_mac_uses_signal_zero(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        if pid == 404:
            raise ProcessLookupError
        if pid == 403:
            raise PermissionError

    monkeypatch.setattr(plat.os, "kill", fake_kill)
    assert plat.pid_alive(500) is True
    assert plat.pid_alive(404) is False
    assert plat.pid_alive(403) is True  # чужой процесс, но живой
    assert plat.pid_alive(0) is False and plat.pid_alive(-1) is False
    assert calls == [(500, 0), (404, 0), (403, 0)]


@pytest.mark.skipif(sys.platform != "win32", reason="ctypes.windll — только Windows")
def test_pid_alive_on_windows_never_signals(monkeypatch):
    def forbidden(*_):
        raise AssertionError("os.kill на Windows — TerminateProcess")

    monkeypatch.setattr(plat.os, "kill", forbidden)
    assert plat.pid_alive(os.getpid()) is True
