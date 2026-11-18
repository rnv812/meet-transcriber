"""Платформа: Windows (основная) и macOS (экспериментально, Apple Silicon).

Всё, что зависит от ОС и нужно больше чем одному модулю, — здесь: папка
данных, «жив ли процесс», флаг «без окна» для подпроцессов. Папки и адреса
открывает оболочка на Rust (`platform.rs`), резиденту это не нужно. Модули Windows (winreg, ctypes.windll, pyaudiowpatch) импортируются
только на Windows и только там, где нужны: импорт на macOS упал бы.

IMPORTANT: функции читают `sys.platform` при каждом вызове, а не при импорте:
тесты подменяют платформу monkeypatch'ем и проверяют ветку macOS на Windows.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_DIR_NAME = "meet"
WINDOWS = "win32"
MACOS = "darwin"
# macOS: где лежит приложение и его ресурсы (ffmpeg, помощник meet-audiotap):
# «Программы» системы, затем «Программы» пользователя.
SYSTEM_APPLICATIONS = Path("/Applications")
APP_RESOURCES = Path("Meet.app") / "Contents" / "Resources" / "resources"


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


def mac_perf_cores(run=None) -> int | None:
    """Число производительных ядер Apple Silicon (`hw.perflevel0.physicalcpu`,
    8 у M1 Pro); не macOS, Intel-Mac без этого ключа или сбой — None."""
    if not is_macos():
        return None
    run = run or subprocess.run
    try:
        out = run(["sysctl", "-n", "hw.perflevel0.physicalcpu"], capture_output=True,
                  text=True, timeout=5)
        if out.returncode == 0 and int(out.stdout.strip()) > 0:
            return int(out.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def perf_cores(run=None) -> int:
    """Сколько потоков считать «полноценными»: на Apple Silicon — число
    производительных ядер (работа не уходит на энергоэффективные), иначе —
    `os.cpu_count()`; не знаем — 1."""
    return mac_perf_cores(run) or os.cpu_count() or 1


_IOPOL_TYPE_DISK = 0
_IOPOL_SCOPE_PROCESS = 0
_IOPOL_THROTTLE = 3
_NICE_BACKGROUND_JOB = 10


def _libc():
    import ctypes

    return ctypes.CDLL(None, use_errno=True)


def low_priority_preexec():
    """`preexec_fn` для тяжёлой фоновой работы на macOS: `nice` 10 и
    приглушённый ввод-вывод (`IOPOL_THROTTLE`). Не класс «background»: он сажает
    процесс только на энергоэффективные ядра и сильно замедляет его. На
    Windows (там приоритет задают флаги запуска) и без libc — None.

    Библиотека грузится заранее, в родителе: в самом `preexec_fn` — только
    два вызова, без импортов и выделений."""
    if not is_macos():
        return None
    try:
        libc = _libc()
        throttle = libc.setiopolicy_np
    except (OSError, AttributeError):
        throttle = None

    def lower() -> None:
        try:
            os.setpriority(os.PRIO_PROCESS, 0, _NICE_BACKGROUND_JOB)
        except (OSError, AttributeError):
            pass
        if throttle is not None:
            try:
                throttle(_IOPOL_TYPE_DISK, _IOPOL_SCOPE_PROCESS, _IOPOL_THROTTLE)
            except Exception:
                pass

    return lower


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


def app_resource_dirs() -> list[Path]:
    """macOS: папки ресурсов установленного Meet.app — в `/Applications`, затем в
    `~/Applications`. Нужны `meet`, запущенному из Терминала: PATH резидента
    дополняет оболочка, а у Терминала его нет. На Windows — пусто."""
    if not is_macos():
        return []
    home = Path(os.environ.get("HOME") or Path.home())
    return [SYSTEM_APPLICATIONS / APP_RESOURCES, home / "Applications" / APP_RESOURCES]


def use_app_tools() -> "Path | None":
    """macOS: ffmpeg не найден в PATH — папка ресурсов Meet.app, где он есть,
    встаёт в начало PATH этого процесса (и его детей). Возвращает её; ничего не
    поменялось — None."""
    if not is_macos() or shutil.which("ffmpeg"):
        return None
    for folder in app_resource_dirs():
        if (folder / "ffmpeg").is_file():
            current = os.environ.get("PATH", "")
            os.environ["PATH"] = os.pathsep.join([str(folder), *([current] if current else [])])
            return folder
    return None
