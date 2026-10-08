"""Системный звук без звука самого Meet (0.5, Windows 10 2004+).

WASAPI «process loopback» (`ActivateAudioInterfaceAsync` с
`VAD\\Process_Loopback`, `PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE`):
всё, что играет компьютер, кроме дерева процессов оболочки Meet — окна и
WebView2, где звучит плеер записи. Иначе запись встречи, во время которой
слушали старую запись, ловила бы и её.

`ProcessLoopbackStream` повторяет то, чем запись пользуется у потока PyAudio
(`start_stream`, `stop_stream`, `close`, `is_active`, callback
`(in_data, frames, time_info, status)` → `(None, paContinue|paAbort)`):
recorder открывает его вместо loopback-устройства, если `pseudo_device()`
его предлагает. Сбой активации — исключение из `start_stream`, и запись
берёт прежний loopback.

Формат — 48 кГц, 16 бит, стерео (у process loopback своего формата нет,
`GetMixFormat` не поддержан; `AUTOCONVERTPCM` приводит звук к нему).
COM — в своём потоке (MTA), обработчик завершения активации — agile.
"""

from __future__ import annotations

import os
import sys
import threading
import time

RATE = 48000
CHANNELS = 2
NAME = "Системный звук (без звука Meet)"
# Windows 10 2004 — первая сборка с process loopback.
MIN_BUILD = 19041
ENV_PID = "MEET_EXCLUDE_PID"
ENV_OFF = "MEET_PROCESS_LOOPBACK"
PA_CONTINUE, PA_ABORT = 0, 2


def exclude_pid() -> int | None:
    """Процесс, чьё дерево исключить (оболочка Meet, `--parent-pid` резидента); нет — None."""
    raw = os.environ.get(ENV_PID, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def exclude_target() -> int | None:
    """Кого исключать из системного звука на любой ОС (на macOS — помощнику
    `--exclude-pid`): выключено (`MEET_PROCESS_LOOPBACK=0`) или некого — None."""
    if os.environ.get(ENV_OFF, "").strip() == "0":
        return None
    return exclude_pid()


def available() -> bool:
    """Windows 10 2004+, не выключено (`MEET_PROCESS_LOOPBACK=0`) и известно, кого исключать."""
    if sys.platform != "win32":
        return False
    try:
        if sys.getwindowsversion().build < MIN_BUILD:
            return False
    except AttributeError:
        return False
    return exclude_target() is not None


def pseudo_device() -> dict | None:
    """«Устройство» для recorder: системный звук без Meet; недоступно — None."""
    if not available():
        return None
    return {"name": NAME, "index": -1, "defaultSampleRate": float(RATE), "maxInputChannels": CHANNELS,
            "process_loopback": True}


class ProcessLoopbackStream:
    """Поток системного звука без дерева процесса `pid` (см. модуль).
    `open_client(pid)` → (клиент, захват) — для тестов подменяется."""

    def __init__(self, callback, pid: int, *, open_client=None, poll_s: float = 0.01) -> None:
        self._callback = callback
        self._pid = pid
        self._open_client = open_client or _open_client
        self._poll = poll_s
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None
        self._active = False

    def start_stream(self, timeout: float = 5.0) -> None:
        self._thread = threading.Thread(target=self._run, name="meet-process-loopback", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self._stop.set()
            raise RuntimeError("process loopback: активация не завершилась")
        if self._error is not None:
            raise RuntimeError(f"process loopback: {self._error}") from self._error

    def is_active(self) -> bool:
        return self._active and self._thread is not None and self._thread.is_alive()

    def stop_stream(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def close(self) -> None:
        self.stop_stream()

    def _run(self) -> None:
        coinit = _co_init()
        try:
            try:
                client, capture = self._open_client(self._pid)
                client.Start()
            except Exception as e:  # noqa: BLE001 — любая причина: запись возьмёт прежний loopback
                self._error = e
                self._ready.set()
                return
            self._active = True
            self._ready.set()
            frame = 2 * CHANNELS
            try:
                while not self._stop.is_set():
                    time.sleep(self._poll)
                    while capture.GetNextPacketSize():
                        data, frames, flags, _, _ = capture.GetBuffer()
                        n = frames * frame
                        chunk = bytes(n) if flags & 2 else _read(data, n)    # AUDCLNT_BUFFERFLAGS_SILENT
                        capture.ReleaseBuffer(frames)
                        _, code = self._callback(chunk, frames, None, 0)
                        if code != PA_CONTINUE:
                            return
            finally:
                self._active = False
                try:
                    client.Stop()
                except Exception:
                    pass
        finally:
            _co_uninit(coinit)


def _read(pointer, n: int) -> bytes:
    import ctypes

    return ctypes.string_at(pointer, n)


def _co_init() -> bool:
    try:
        import comtypes

        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        return True
    except Exception:
        return False


def _co_uninit(done: bool) -> None:
    if not done:
        return
    try:
        import comtypes

        comtypes.CoUninitialize()
    except Exception:
        pass


def _open_client(pid: int):
    """Активировать process loopback без дерева `pid` → (IAudioClient, IAudioCaptureClient)."""
    import ctypes
    from ctypes import POINTER, byref, c_void_p, wintypes

    from comtypes import HRESULT

    com = _com()
    params = com.ACTIVATION_PARAMS(1, com.LOOPBACK_PARAMS(pid, 1))   # PROCESS_LOOPBACK, EXCLUDE_TARGET_PROCESS_TREE
    pv = com.PROPVARIANT(65, 0, 0, 0, com.BLOB(ctypes.sizeof(params), ctypes.addressof(params)))   # VT_BLOB
    activate = ctypes.WinDLL("Mmdevapi.dll").ActivateAudioInterfaceAsync
    activate.argtypes = [wintypes.LPCWSTR, POINTER(com.GUID), c_void_p, c_void_p,
                         POINTER(POINTER(com.IActivateAudioInterfaceAsyncOperation))]
    activate.restype = HRESULT
    handler = com.Handler()
    pointer = handler.QueryInterface(com.IActivateAudioInterfaceCompletionHandler)
    op = POINTER(com.IActivateAudioInterfaceAsyncOperation)()
    activate("VAD\\Process_Loopback", byref(com.IAudioClient._iid_), ctypes.addressof(pv),
             ctypes.cast(pointer, c_void_p), byref(op))
    if not handler.done.wait(5):
        raise RuntimeError("нет ответа на активацию")
    hr, unknown = op.GetActivateResult()
    if hr != 0:
        raise OSError(f"активация отклонена ({hr & 0xFFFFFFFF:#x})")
    client = unknown.QueryInterface(com.IAudioClient)
    fmt = com.WAVEFORMATEX(1, CHANNELS, RATE, RATE * 2 * CHANNELS, 2 * CHANNELS, 16, 0)
    # LOOPBACK | AUTOCONVERTPCM | SRC_DEFAULT_QUALITY; буфер 200 мс.
    client.Initialize(0, 0x00020000 | 0x80000000 | 0x08000000, 2_000_000, 0, byref(fmt), None)
    capture = client.GetService(byref(com.IAudioCaptureClient._iid_)).QueryInterface(com.IAudioCaptureClient)
    return client, capture


_COM = None


def _com():
    """Описания COM-интерфейсов (comtypes) — лениво: модуль импортируется и не на Windows."""
    global _COM
    if _COM is not None:
        return _COM
    import ctypes
    import types
    from ctypes import POINTER, Structure, c_int64, c_uint16, c_uint32, c_uint64, c_void_p, wintypes

    from comtypes import COMMETHOD, GUID, HRESULT, COMObject, IUnknown

    class WAVEFORMATEX(Structure):
        _fields_ = [("wFormatTag", c_uint16), ("nChannels", c_uint16), ("nSamplesPerSec", c_uint32),
                    ("nAvgBytesPerSec", c_uint32), ("nBlockAlign", c_uint16), ("wBitsPerSample", c_uint16),
                    ("cbSize", c_uint16)]

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetBuffer", (["out"], POINTER(POINTER(ctypes.c_byte)), "ppData"),
                      (["out"], POINTER(c_uint32), "pNumFramesToRead"), (["out"], POINTER(c_uint32), "pdwFlags"),
                      (["out"], POINTER(c_uint64), "pu64DevicePosition"),
                      (["out"], POINTER(c_uint64), "pu64QPCPosition")),
            COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
            COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "pNumFramesInNextPacket")),
        ]

    class IAudioClient(IUnknown):
        _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Initialize", (["in"], ctypes.c_int, "ShareMode"),
                      (["in"], c_uint32, "StreamFlags"), (["in"], c_int64, "hnsBufferDuration"),
                      (["in"], c_int64, "hnsPeriodicity"), (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                      (["in"], POINTER(GUID), "AudioSessionGuid")),
            COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "n")),
            COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(c_int64), "l")),
            COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "p")),
            COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], ctypes.c_int, "m"),
                      (["in"], POINTER(WAVEFORMATEX), "f"), (["out"], POINTER(POINTER(WAVEFORMATEX)), "c")),
            COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)), "f")),
            COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(c_int64), "a"), (["out"], POINTER(c_int64), "b")),
            COMMETHOD([], HRESULT, "Start"),
            COMMETHOD([], HRESULT, "Stop"),
            COMMETHOD([], HRESULT, "Reset"),
            COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "h")),
            COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID), "riid"),
                      (["out"], POINTER(POINTER(IUnknown)), "ppv")),
        ]

    class IActivateAudioInterfaceAsyncOperation(IUnknown):
        _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
        _methods_ = [COMMETHOD([], HRESULT, "GetActivateResult", (["out"], POINTER(HRESULT), "hr"),
                               (["out"], POINTER(POINTER(IUnknown)), "unk"))]

    class IActivateAudioInterfaceCompletionHandler(IUnknown):
        _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
        _methods_ = [COMMETHOD([], HRESULT, "ActivateCompleted",
                               (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "op"))]

    class IAgileObject(IUnknown):
        _iid_ = GUID("{94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90}")
        _methods_ = []

    class Handler(COMObject):
        _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

        def __init__(self):
            super().__init__()
            self.done = threading.Event()

        def ActivateCompleted(self, op):  # noqa: N802 — имя метода COM
            self.done.set()
            return 0

    class LOOPBACK_PARAMS(Structure):
        _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]

    class ACTIVATION_PARAMS(Structure):
        _fields_ = [("ActivationType", ctypes.c_int), ("ProcessLoopbackParams", LOOPBACK_PARAMS)]

    class BLOB(Structure):
        _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]

    class PROPVARIANT(Structure):
        _fields_ = [("vt", c_uint16), ("r1", c_uint16), ("r2", c_uint16), ("r3", c_uint16), ("blob", BLOB)]

    _COM = types.SimpleNamespace(
        GUID=GUID, WAVEFORMATEX=WAVEFORMATEX, IAudioCaptureClient=IAudioCaptureClient, IAudioClient=IAudioClient,
        IActivateAudioInterfaceAsyncOperation=IActivateAudioInterfaceAsyncOperation,
        IActivateAudioInterfaceCompletionHandler=IActivateAudioInterfaceCompletionHandler, Handler=Handler,
        LOOPBACK_PARAMS=LOOPBACK_PARAMS, ACTIVATION_PARAMS=ACTIVATION_PARAMS, BLOB=BLOB, PROPVARIANT=PROPVARIANT)
    return _COM
