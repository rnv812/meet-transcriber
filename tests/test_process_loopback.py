"""Системный звук без звука самого Meet (0.5): выбор, поток, откат. Без настоящего COM."""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest

from meet import process_loopback as pl


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(pl.ENV_PID, raising=False)
    monkeypatch.delenv(pl.ENV_OFF, raising=False)


def _windows(monkeypatch, build=26100):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "getwindowsversion", lambda: SimpleNamespace(build=build), raising=False)


def test_offered_only_on_windows_2004_plus_under_the_shell(monkeypatch):
    _windows(monkeypatch)
    assert pl.pseudo_device() is None                     # нет оболочки — исключать некого
    monkeypatch.setenv(pl.ENV_PID, "4242")
    dev = pl.pseudo_device()
    assert dev and dev["process_loopback"] and dev["name"] == pl.NAME and dev["maxInputChannels"] == 2
    assert pl.exclude_pid() == 4242
    monkeypatch.setenv(pl.ENV_OFF, "0")
    assert pl.pseudo_device() is None                     # выключено вручную
    monkeypatch.delenv(pl.ENV_OFF)
    _windows(monkeypatch, build=18363)                    # Windows 10 1909 — нет process loopback
    assert pl.pseudo_device() is None
    monkeypatch.setattr(sys, "platform", "darwin")
    assert pl.pseudo_device() is None


class FakeCapture:
    def __init__(self, packets):
        self.packets = list(packets)       # (frames, flags)
        self.released = []

    def GetNextPacketSize(self):           # noqa: N802
        return self.packets[0][0] if self.packets else 0

    def GetBuffer(self):                   # noqa: N802
        frames, flags = self.packets.pop(0)
        self._last = bytes([7]) * (frames * 4)
        return self._last, frames, flags, 0, 0

    def ReleaseBuffer(self, frames):       # noqa: N802
        self.released.append(frames)


class FakeClient:
    def __init__(self):
        self.started = self.stopped = False

    def Start(self):                       # noqa: N802
        self.started = True

    def Stop(self):                        # noqa: N802
        self.stopped = True


def _stream(monkeypatch, packets, callback, error=None):
    monkeypatch.setattr(pl, "_read", lambda data, n: data[:n])
    client, capture = FakeClient(), FakeCapture(packets)

    def opener(pid):
        assert pid == 77
        if error:
            raise error
        return client, capture

    return pl.ProcessLoopbackStream(callback, 77, open_client=opener, poll_s=0.001), client, capture


def test_packets_reach_the_callback_and_silence_is_zeros(monkeypatch):
    got = []
    stream, client, capture = _stream(monkeypatch, [(10, 0), (5, 2)],
                                      lambda data, n, t, s: (got.append((data, n)), (None, pl.PA_CONTINUE))[1])
    stream.start_stream()
    deadline = time.monotonic() + 2
    while len(got) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert stream.is_active() and client.started
    stream.stop_stream()
    assert not stream.is_active() and client.stopped
    assert got == [(bytes([7]) * 40, 10), (bytes(20), 5)]       # флаг SILENT — тишина
    assert capture.released == [10, 5]


def test_abort_from_the_callback_ends_the_stream(monkeypatch):
    stream, client, _ = _stream(monkeypatch, [(4, 0)], lambda *a: (None, pl.PA_ABORT))
    stream.start_stream()
    deadline = time.monotonic() + 2
    while stream.is_active() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not stream.is_active() and client.stopped


def test_activation_failure_is_an_error_from_start(monkeypatch):
    stream, _, _ = _stream(monkeypatch, [], lambda *a: (None, 0), error=OSError("активация отклонена"))
    with pytest.raises(RuntimeError, match="активация отклонена"):
        stream.start_stream()


def test_recorder_takes_process_loopback_and_falls_back_to_the_device(monkeypatch):
    from meet import recorder

    monkeypatch.setattr(recorder, "_MAC", False)
    monkeypatch.setattr(recorder, "_find_loopback", lambda p: {"name": "Динамики [Loopback]"})
    monkeypatch.setattr(pl, "pseudo_device", lambda: None)
    assert recorder._system_output(object())["name"] == "Динамики [Loopback]"
    monkeypatch.setattr(pl, "pseudo_device", lambda: {"name": pl.NAME, "process_loopback": True})
    assert recorder._system_output(object())["name"] == pl.NAME
