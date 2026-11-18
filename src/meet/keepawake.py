"""Запрет сна системы от бездействия на время записи.

Очная встреча (только микрофон) на ноутбуке от батареи: экран гаснет, через
несколько минут система засыпает — и в записи десятки минут тишины вместо
разговора. Пока идёт запись, держим запрет средствами ОС:

- macOS — утверждение питания `PreventUserIdleSystemSleep`
  (`IOPMAssertionCreateWithName` через ctypes; заодно снимает App Nap с
  процесса); видно в `pmset -g assertions`;
- Windows — `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`;
  видно в `powercfg /requests`.

Экран при этом гаснет как обычно. Закрытую крышку запрет не удержит — такой
сон запись отмечает сама (`meta.json` `sleep_gaps`, см. `meet.recorder`).

Сбой — строка в журнал записи и `False`: запись важнее запрета сна. Модули
ОС загружаются при вызове и только на своей ОС (`ctypes.WinDLL` есть только
на Windows); тесты подменяют `_kernel32` и `_mac_libs`.
"""

import ctypes
import sys
import threading

# Windows: winbase.h.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

# macOS: IOPMLib.h / CFString.h.
ASSERTION_TYPE = "PreventUserIdleSystemSleep"
ASSERTION_ON = 255  # kIOPMAssertionLevelOn
CF_UTF8 = 0x08000100  # kCFStringEncodingUTF8
IOKIT = "/System/Library/Frameworks/IOKit.framework/IOKit"
CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
# Подпись утверждения в `pmset -g assertions`.
REASON = "Meet: идёт запись встречи"

# Сколько ждать поток-держатель Windows на взятии и снятии.
THREAD_WAIT_S = 5.0


def _kernel32():
    """Своя копия kernel32 (не общий `ctypes.windll`: argtypes не задеваем)."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
    kernel.SetThreadExecutionState.restype = ctypes.c_uint32
    return kernel


def _mac_libs():
    """IOKit и CoreFoundation с типами нужных функций."""
    iokit = ctypes.CDLL(IOKIT)
    cf = ctypes.CDLL(CORE_FOUNDATION)
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None
    iokit.IOPMAssertionCreateWithName.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int32
    iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
    iokit.IOPMAssertionRelease.restype = ctypes.c_int32
    return iokit, cf


class KeepAwake:
    """Запрет сна от бездействия: `acquire()` на старте записи, `release()` на
    «Стоп» и при сбое (повторный `release()` — тихий). Можно и `with`.

    Windows: состояние `SetThreadExecutionState` принадлежит потоку, который
    его выставил, поэтому его держит свой поток-держатель — снять можно из
    любого потока, а смерть процесса снимает запрет сама. macOS: утверждение
    живёт, пока его не освободят или процесс не завершится."""

    def __init__(self, log=None) -> None:
        self._say = log or (lambda msg: None)
        self._mac_id: "int | None" = None
        self._win: "tuple[threading.Thread, threading.Event] | None" = None

    def __enter__(self) -> "KeepAwake":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    @property
    def held(self) -> bool:
        return self._mac_id is not None or self._win is not None

    def acquire(self) -> bool:
        """Взять запрет. True — взят (или уже держится); False — нет (не та ОС
        или ОС отказала, причина — в журнале)."""
        if self.held:
            return True
        try:
            if sys.platform == "win32":
                return self._acquire_windows()
            if sys.platform == "darwin":
                return self._acquire_mac()
        except Exception as e:
            self._say(f"запрет сна не взят: {type(e).__name__}")
        return False

    def release(self) -> None:
        """Снять запрет; не взят — ничего. Не бросает."""
        win, self._win = self._win, None
        if win is not None:
            thread, stop = win
            stop.set()
            thread.join(THREAD_WAIT_S)
        mac_id, self._mac_id = self._mac_id, None
        if mac_id is not None:
            try:
                iokit, _ = _mac_libs()
                iokit.IOPMAssertionRelease(mac_id)
            except Exception as e:
                self._say(f"запрет сна не снят: {type(e).__name__}")

    def _acquire_windows(self) -> bool:
        kernel = _kernel32()
        ready, stop = threading.Event(), threading.Event()
        result = {"ok": False}

        def hold() -> None:
            try:
                result["ok"] = bool(kernel.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
            finally:
                ready.set()
            if not result["ok"]:
                return
            stop.wait()
            kernel.SetThreadExecutionState(ES_CONTINUOUS)

        thread = threading.Thread(target=hold, name="meet-keepawake", daemon=True)
        thread.start()
        ready.wait(THREAD_WAIT_S)
        if not result["ok"]:
            self._say("запрет сна не взят: SetThreadExecutionState отказал")
            return False
        self._win = (thread, stop)
        return True

    def _acquire_mac(self) -> bool:
        iokit, cf = _mac_libs()
        kind = cf.CFStringCreateWithCString(None, ASSERTION_TYPE.encode("utf-8"), CF_UTF8)
        name = cf.CFStringCreateWithCString(None, REASON.encode("utf-8"), CF_UTF8)
        try:
            if not kind or not name:
                self._say("запрет сна не взят: строка CoreFoundation не создана")
                return False
            out = ctypes.c_uint32(0)
            code = iokit.IOPMAssertionCreateWithName(kind, ASSERTION_ON, name, ctypes.byref(out))
        finally:
            for ref in (kind, name):
                if ref:
                    cf.CFRelease(ref)
        if code != 0:
            self._say(f"запрет сна не взят: IOPMAssertionCreateWithName {code & 0xFFFFFFFF:#x}")
            return False
        self._mac_id = out.value
        return True
