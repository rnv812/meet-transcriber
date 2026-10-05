"""Платформа: Windows (основная) и macOS (экспериментально, Apple Silicon).

Всё, что зависит от ОС и нужно больше чем одному модулю, — здесь: папка
данных, «жив ли процесс», флаг «без окна» для подпроцессов. Папки и адреса
открывает оболочка на Rust (`platform.rs`), резиденту это не нужно. Модули Windows (winreg, ctypes.windll, pyaudiowpatch) импортируются
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
    TerminateProcess (убьёт запись); там — OpenProcess через ctypes и код
    выхода: пока кто-то держит хэндл умершего процесса (оболочка — резидента,
    Popen — своего ребёнка), OpenProcess на него удаётся, и одно это сказало
    бы «жив» — замок записи и временные папки мёртвого процесса считались бы
    занятыми. Код выхода не узнали — «жив» (не трогаем чужое зря). На macOS
    сигнал 0 ничего не посылает: ESRCH — процесса нет, EPERM — есть, но чужой.
    """
    if pid <= 0:
        return False
    if is_windows():
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
