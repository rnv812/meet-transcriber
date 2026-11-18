"""Запрет сна на время записи: вызовы ОС подменены, проверяется их вид."""

import threading

import pytest

from meet import keepawake


# --- Windows: SetThreadExecutionState ---


class _Kernel32:
    def __init__(self, result=1):
        self.result = result
        self.calls = []  # (флаги, поток)

    def SetThreadExecutionState(self, flags):
        self.calls.append((flags, threading.get_ident()))
        return self.result


def _windows(monkeypatch, kernel):
    monkeypatch.setattr(keepawake.sys, "platform", "win32")
    monkeypatch.setattr(keepawake, "_kernel32", lambda: kernel)


def test_windows_holds_system_required_until_release(monkeypatch):
    kernel = _Kernel32()
    _windows(monkeypatch, kernel)
    awake = keepawake.KeepAwake()
    assert awake.acquire() is True
    assert [f for f, _ in kernel.calls] == [keepawake.ES_CONTINUOUS | keepawake.ES_SYSTEM_REQUIRED]
    awake.release()
    assert [f for f, _ in kernel.calls] == [
        keepawake.ES_CONTINUOUS | keepawake.ES_SYSTEM_REQUIRED,
        keepawake.ES_CONTINUOUS,
    ]
    # Состояние принадлежит потоку: снимается тем же потоком, что и взято.
    assert kernel.calls[0][1] == kernel.calls[1][1]


def test_windows_flags_are_the_documented_values():
    assert keepawake.ES_CONTINUOUS == 0x80000000
    assert keepawake.ES_SYSTEM_REQUIRED == 0x00000001


def test_windows_release_from_another_thread_still_resets(monkeypatch):
    """Взяли в одном потоке, отпустили в другом — сброс всё равно случился в
    потоке, который держал состояние (иначе система так и не уснула бы)."""
    kernel = _Kernel32()
    _windows(monkeypatch, kernel)
    awake = keepawake.KeepAwake()
    awake.acquire()
    t = threading.Thread(target=awake.release)
    t.start()
    t.join()
    assert [f for f, _ in kernel.calls][-1] == keepawake.ES_CONTINUOUS
    assert kernel.calls[0][1] == kernel.calls[1][1]


def test_windows_refusal_is_logged_and_not_raised(monkeypatch):
    kernel = _Kernel32(result=0)
    _windows(monkeypatch, kernel)
    lines = []
    awake = keepawake.KeepAwake(lines.append)
    assert awake.acquire() is False
    assert lines and "сна" in lines[0]
    awake.release()  # взять не вышло — снимать нечего
    assert [f for f, _ in kernel.calls] == [keepawake.ES_CONTINUOUS | keepawake.ES_SYSTEM_REQUIRED]


# --- macOS: IOPMAssertionCreateWithName ---


class _Ref:
    """Как `ctypes.byref`: значение под `_obj.value`."""

    def __init__(self, obj):
        self._obj = obj


class _CF:
    def __init__(self):
        self.made = []
        self.released = []

    def CFStringCreateWithCString(self, alloc, text, encoding):
        assert alloc is None and encoding == keepawake.CF_UTF8
        self.made.append(text.decode("utf-8"))
        return f"cf:{text.decode('utf-8')}"

    def CFRelease(self, ref):
        self.released.append(ref)


class _IOKit:
    def __init__(self, result=0, assertion_id=42):
        self.result = result
        self.assertion_id = assertion_id
        self.created = []
        self.released = []

    def IOPMAssertionCreateWithName(self, kind, level, name, out):
        self.created.append((kind, level, name))
        if self.result == 0:
            out._obj.value = self.assertion_id
        return self.result

    def IOPMAssertionRelease(self, assertion_id):
        self.released.append(assertion_id)
        return 0


def _mac(monkeypatch, iokit, cf):
    monkeypatch.setattr(keepawake.sys, "platform", "darwin")
    monkeypatch.setattr(keepawake, "_mac_libs", lambda: (iokit, cf))
    monkeypatch.setattr(keepawake.ctypes, "byref", _Ref)


def test_mac_creates_and_releases_the_power_assertion(monkeypatch):
    iokit, cf = _IOKit(), _CF()
    _mac(monkeypatch, iokit, cf)
    awake = keepawake.KeepAwake()
    assert awake.acquire() is True
    assert iokit.created == [("cf:PreventUserIdleSystemSleep", keepawake.ASSERTION_ON,
                              f"cf:{keepawake.REASON}")]
    assert keepawake.ASSERTION_ON == 255
    assert sorted(cf.released) == sorted(f"cf:{s}" for s in cf.made)  # строки CF не утекают
    awake.release()
    assert iokit.released == [42]
    awake.release()  # повторный «Стоп» — тихий
    assert iokit.released == [42]


def test_mac_failed_assertion_is_logged_and_never_released(monkeypatch):
    iokit, cf = _IOKit(result=-536870212), _CF()
    _mac(monkeypatch, iokit, cf)
    lines = []
    awake = keepawake.KeepAwake(lines.append)
    assert awake.acquire() is False
    assert lines
    assert sorted(cf.released) == sorted(f"cf:{s}" for s in cf.made)
    awake.release()
    assert iokit.released == []


def test_mac_without_iokit_records_anyway(monkeypatch):
    monkeypatch.setattr(keepawake.sys, "platform", "darwin")

    def broken():
        raise OSError("IOKit не загрузился")

    monkeypatch.setattr(keepawake, "_mac_libs", broken)
    lines = []
    awake = keepawake.KeepAwake(lines.append)
    assert awake.acquire() is False
    assert lines
    awake.release()


# --- общее ---


def test_other_platforms_do_nothing(monkeypatch):
    monkeypatch.setattr(keepawake.sys, "platform", "linux")
    monkeypatch.setattr(keepawake, "_kernel32", lambda: pytest.fail("не Windows"))
    monkeypatch.setattr(keepawake, "_mac_libs", lambda: pytest.fail("не macOS"))
    awake = keepawake.KeepAwake()
    assert awake.acquire() is False
    awake.release()


def test_context_manager_releases_on_error(monkeypatch):
    iokit, cf = _IOKit(), _CF()
    _mac(monkeypatch, iokit, cf)
    with pytest.raises(RuntimeError):
        with keepawake.KeepAwake():
            assert iokit.created
            raise RuntimeError("запись упала")
    assert iokit.released == [42]
