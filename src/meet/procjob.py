"""Дочерние процессы, которые не переживают своего родителя.

Постоянный процесс модели (диалог подсказок живого ассистента) живёт, пока
живёт `meet assist`. Штатно его закрывает сам ассистент; но если ассистент
убит жёстко (резидент гасит дерево, сбой), процесс модели не должен
остаться сиротой. На Windows для этого — job object с
KILL_ON_JOB_CLOSE: его хэндл держит только этот процесс, и когда процесс
умирает (как угодно), Windows закрывает хэндл и гасит всех, кто в job.
На macOS job object нет: дочерний процесс запускается в своей группе
(`start_new_session`, чтобы `kill_tree` гасил его детей), и гибель группы
ассистента его не гасит — он выходит сам, когда закрывается его stdin (конец
пайпа у умершего родителя), но только после текущего хода (до 90 с).

Сбой привязки не критичен: процесс модели и так выходит по концу stdin.
"""

import subprocess
import sys

_job = None  # хэндл job object, открыт до конца процесса — так и задумано

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _job_handle():
    global _job
    if _job is not None:
        return _job
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        return None

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p, wintypes.DWORD)
    if not kernel32.SetInformationJobObject(handle, JobObjectExtendedLimitInformation,
                                            ctypes.byref(info), ctypes.sizeof(info)):
        kernel32.CloseHandle(handle)
        return None
    _job = handle
    return _job


def bind_to_this_process(pid: int) -> bool:
    """Процесс `pid` умрёт вместе с этим (Windows: job object). True — привязан."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        job = _job_handle()
        if job is None:
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
        proc = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid))
        if not proc:
            return False
        try:
            kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
            return bool(kernel32.AssignProcessToJobObject(job, proc))
        finally:
            kernel32.CloseHandle(proc)
    except Exception:
        return False


def popen_kwargs() -> dict:
    """Аргументы Popen для такого процесса: без окна консоли (Windows), своя
    группа процессов (macOS)."""
    if sys.platform == "win32":
        return {"creationflags": NO_WINDOW}
    return {"start_new_session": True}


def kill_tree(proc) -> None:
    """Погасить процесс и его детей (taskkill /T на Windows)."""
    if proc is None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            import os
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, AttributeError):
            pass
    try:
        proc.kill()
    except OSError:
        pass
