"""Платформенный слой (`meet.plat`): папка данных, «жив ли процесс», открыть
без окна — на Windows как раньше, на macOS — свои пути и команды. Ветка macOS
проверяется на Windows подменой `sys.platform`."""

import os
import sys
import unicodedata
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


@pytest.mark.skipif(sys.platform != "win32", reason="ветка Windows: OpenProcess и код выхода")
def test_pid_alive_sees_death_while_someone_holds_the_handle():
    """Хэндл умершего процесса открыт (Popen ещё жив, оболочка держит
    резидента): OpenProcess удаётся, но мёртвый — по коду выхода. Иначе замок
    записи, маркер GPU и временные папки мёртвого процесса считались бы чужими."""
    import subprocess

    assert plat.pid_alive(os.getpid()) is True
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()  # объект жив — хэндл процесса открыт
    assert plat.pid_alive(proc.pid) is False


@pytest.mark.skipif(sys.platform != "win32", reason="ветка Windows")
def test_pid_alive_unknown_exit_code_counts_as_alive(monkeypatch):
    """Код выхода не узнали — «жив»: чужой замок и чужие папки не трогаем зря."""
    import ctypes

    real = ctypes.windll.kernel32

    class Kernel32:
        def __getattr__(self, name):
            return getattr(real, name)

        @staticmethod
        def GetExitCodeProcess(handle, code):
            return 0

    monkeypatch.setattr(ctypes.windll, "kernel32", Kernel32())
    assert plat.pid_alive(os.getpid()) is True


# --- перф-ядра и пониженный приоритет ----------------------------------------


def test_perf_cores_on_mac_reads_performance_cores(monkeypatch):
    import types

    monkeypatch.setattr(plat, "is_macos", lambda: True)
    asked = []

    def run(argv, **kw):
        asked.append(argv)
        return types.SimpleNamespace(returncode=0, stdout="6\n")

    assert plat.perf_cores(run=run) == 6
    assert asked == [["sysctl", "-n", "hw.perflevel0.physicalcpu"]]


def test_perf_cores_falls_back_to_cpu_count(monkeypatch):
    import types

    monkeypatch.setattr(os, "cpu_count", lambda: 10)
    monkeypatch.setattr(plat, "is_macos", lambda: True)
    bad = lambda argv, **kw: types.SimpleNamespace(returncode=1, stdout="")  # noqa: E731
    assert plat.perf_cores(run=bad) == 10  # Intel: ключа нет
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    assert plat.perf_cores() == 10
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert plat.perf_cores() == 1


def test_low_priority_preexec_only_on_mac(monkeypatch):
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    assert plat.low_priority_preexec() is None


def test_low_priority_preexec_on_mac_sets_nice_10_and_throttles_io(monkeypatch):
    calls = []

    class Libc:
        def setiopolicy_np(self, which, scope, policy):
            calls.append(("io", which, scope, policy))
            return 0

    monkeypatch.setattr(plat, "is_macos", lambda: True)
    monkeypatch.setattr(plat, "_libc", lambda: Libc())
    monkeypatch.setattr(os, "setpriority", lambda which, who, prio: calls.append(("nice", prio)),
                        raising=False)
    monkeypatch.setattr(os, "PRIO_PROCESS", 0, raising=False)
    fn = plat.low_priority_preexec()
    assert fn is not None
    fn()
    assert ("nice", 10) in calls
    # IOPOL_TYPE_DISK=0, IOPOL_SCOPE_PROCESS=0, IOPOL_THROTTLE=3 — не «background»
    assert ("io", 0, 0, 3) in calls
    fn()  # и повторный вызов не падает

# --- ffmpeg и помощник из Meet.app для `meet` из Терминала ---


def _bundle(apps: Path, name: str) -> Path:
    res = apps / "Meet.app" / "Contents" / "Resources" / "resources"
    res.mkdir(parents=True, exist_ok=True)
    (res / name).write_bytes(b"")
    return res


@pytest.fixture
def terminal(mac, monkeypatch, tmp_path):
    """macOS, `meet` из Терминала: ffmpeg в PATH нет."""
    monkeypatch.setattr(plat, "SYSTEM_APPLICATIONS", tmp_path / "Applications")
    monkeypatch.setattr(plat.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setenv("PATH", "/usr/bin")
    return mac


def test_app_resource_dirs_on_mac(terminal, tmp_path):
    assert plat.app_resource_dirs() == [
        tmp_path / "Applications" / "Meet.app" / "Contents" / "Resources" / "resources",
        terminal / "Applications" / "Meet.app" / "Contents" / "Resources" / "resources",
    ]


def test_ffmpeg_from_the_app_goes_to_path(terminal):
    res = _bundle(terminal / "Applications", "ffmpeg")
    assert plat.use_app_tools() == res
    assert os.environ["PATH"].split(os.pathsep) == [str(res), "/usr/bin"]


def test_ffmpeg_already_on_path_leaves_path_alone(terminal, monkeypatch):
    _bundle(terminal / "Applications", "ffmpeg")
    monkeypatch.setattr(plat.shutil, "which", lambda name, *a, **k: "/opt/homebrew/bin/ffmpeg")
    assert plat.use_app_tools() is None
    assert os.environ["PATH"] == "/usr/bin"


def test_no_app_leaves_path_alone(terminal):
    assert plat.use_app_tools() is None
    assert os.environ["PATH"] == "/usr/bin"


def test_windows_has_no_app_resource_dirs(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert plat.app_resource_dirs() == []
    path = os.environ.get("PATH")
    assert plat.use_app_tools() is None
    assert os.environ.get("PATH") == path


def test_cli_puts_app_tools_on_path_first(monkeypatch):
    from meet import cli

    seen = []
    monkeypatch.setattr(plat, "use_app_tools", lambda: seen.append(True))
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert seen == [True]

# --- ключ пути: один путь — один ключ ---

NFC = unicodedata.normalize("NFC", "Recordings/Встреча й ё")
NFD = unicodedata.normalize("NFD", NFC)


def test_path_key_on_mac_ignores_case_and_unicode_form(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert NFD != NFC
    assert plat.path_key(NFD) == plat.path_key(NFC) == plat.path_key(NFC.upper())
    assert plat.path_key(Path(NFD)) == plat.path_key(str(Path(NFC)))


def test_path_key_on_windows_is_as_before(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    for text in ("C:/Rec/Встреча", NFD, NFC):
        assert plat.path_key(text) == os.path.normcase(text)
    if os.name == "nt":
        assert plat.path_key("C:/Rec/Встреча") == r"c:\rec\встреча"
