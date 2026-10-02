"""Платформа: Windows (основная) и macOS (экспериментально, Apple Silicon).

Всё, что зависит от ОС и нужно больше чем одному модулю, — здесь: папка
данных, «жив ли процесс», флаг «без окна» для подпроцессов, открыть папку или
адрес. Модули Windows (winreg, ctypes.windll, pyaudiowpatch) импортируются
только на Windows и только там, где нужны: импорт на macOS упал бы.

IMPORTANT: функции читают `sys.platform` при каждом вызове, а не при импорте:
тесты подменяют платформу monkeypatch'ем и проверяют ветку macOS на Windows.
"""

import os
import subprocess
import sys
from pathlib import Path

APP_DIR_NAME = "meet"
WINDOWS = "win32"
MACOS = "darwin"


def is_windows() -> bool:
    return sys.platform == WINDOWS


def is_macos() -> bool:
    return sys.platform == MACOS


def data_root() -> Path:
    """Папка, внутри которой лежит `meet`: на Windows — `%LOCALAPPDATA%`, на
    macOS — `~/Library/Application Support`.

    Фоллбэк на "." на Windows — исторический (`tray._state_dir()`): под
    pythonw в автозагрузке переменной среды может не оказаться."""
    if is_macos():
        return Path(os.environ.get("HOME") or Path.home()) / "Library" / "Application Support"
    return Path(os.environ.get("LOCALAPPDATA", "."))


def no_window() -> int:
    """`creationflags` подпроцесса без окна консоли: на Windows —
    CREATE_NO_WINDOW, на macOS окна у консольной программы и так нет."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if is_windows() else 0


def pid_alive(pid: int) -> bool:
    """Жив ли процесс.

    IMPORTANT: `os.kill(pid, 0)` на Windows — не проверка, а безусловный
    TerminateProcess (убьёт запись); там — OpenProcess через ctypes. На macOS
    сигнал 0 ничего не посылает: ESRCH — процесса нет, EPERM — есть, но чужой.
    """
    if pid <= 0:
        return False
    if is_windows():
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def open_command(target: str) -> list[str]:
    """Команда «открыть папку или адрес в программе по умолчанию»: на macOS —
    `open`, на Windows — `explorer` (папку он показывает, адрес отдаёт
    браузеру)."""
    return ["open", target] if is_macos() else ["explorer", target]


def open_target(target: str) -> bool:
    """Открыть папку или адрес. Ждём только запуска: explorer возвращает
    ненулевой код и при успехе. False — не запустилось."""
    try:
        subprocess.Popen(open_command(str(target)), creationflags=no_window(),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True


def exe_name(name: str) -> str:
    """Имя исполняемого файла: `ffmpeg` → `ffmpeg.exe` на Windows."""
    return f"{name}.exe" if is_windows() else name


def venv_bin(prefix: Path) -> Path:
    """Папка программ venv: `Scripts` на Windows, `bin` на macOS."""
    return prefix / ("Scripts" if is_windows() else "bin")
