import struct
import wave

from meet.recorder import WavWriter, _stopped_tracks


class _FakeStream:
    def __init__(self, active: bool) -> None:
        self._active = active

    def is_active(self) -> bool:
        return self._active


def test_stopped_tracks_reports_inactive_once():
    streams = [("sys.opus", _FakeStream(True)), ("mic.opus", _FakeStream(False))]
    reported: set[str] = set()
    assert _stopped_tracks(streams, reported) == ["mic.opus"]
    # уже сообщили — второй такт молчит, чтобы не спамить
    assert _stopped_tracks(streams, reported) == []


def test_stopped_tracks_all_active_reports_nothing():
    streams = [("sys.opus", _FakeStream(True)), ("mic.opus", _FakeStream(True))]
    assert _stopped_tracks(streams, set()) == []


def test_closed_file_is_valid_wav(tmp_path):
    path = tmp_path / "out.wav"
    w = WavWriter(path, channels=2, rate=48000)
    w.write(b"\x00\x00" * 2 * 480)  # 480 стерео-фреймов int16
    w.close()
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 2
        assert wf.getframerate() == 48000
        assert wf.getsampwidth() == 2
        assert wf.getnframes() == 480


def test_unclosed_file_has_streamable_header(tmp_path):
    path = tmp_path / "crash.wav"
    w = WavWriter(path, channels=1, rate=16000)
    w.write(b"\x00\x00" * 160)
    w._f.flush()  # имитация сбоя: данные на диске, close() не вызван
    raw = path.read_bytes()
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    assert struct.unpack("<I", raw[4:8])[0] == 0xFFFFFFFF  # placeholder, не 0
    assert len(raw) == 44 + 320


import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import meet.recorder as recorder
from meet.recorder import LOCK_NAME, _acquire_lock, _pid_alive, record


def _finished_pid() -> int:
    """PID уже завершившегося процесса. Именно subprocess.run, не Popen:
    Popen держит хэндл процесса, и Windows сохраняет объект процесса живым
    (OpenProcess на «мёртвый» pid сработал бы) — run() закрывает хэндл."""
    out = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
    )
    return int(out.stdout.strip())


def test_pid_alive_for_self():
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_for_dead_process():
    assert _pid_alive(_finished_pid()) is False


def test_acquire_lock_writes_pid_and_folder(tmp_path):
    lock = _acquire_lock(tmp_path, tmp_path / "rec1")
    data = json.loads(lock.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert data["folder"].endswith("rec1")


def test_acquire_lock_refuses_when_pid_alive(tmp_path):
    (tmp_path / LOCK_NAME).write_text(
        json.dumps({"pid": os.getpid(), "folder": "x"}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="уже идёт"):
        _acquire_lock(tmp_path, tmp_path / "rec2")


def test_acquire_lock_overwrites_stale(tmp_path):
    (tmp_path / LOCK_NAME).write_text(
        json.dumps({"pid": _finished_pid(), "folder": "x"}), encoding="utf-8"
    )
    lock = _acquire_lock(tmp_path, tmp_path / "rec3")
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()


class _FakeAudioStream:
    def __init__(self):
        self.active = True

    def is_active(self):
        return self.active

    def stop_stream(self):
        self.active = False

    def close(self):
        pass


class _FakePyAudio:
    def get_host_api_info_by_type(self, t):
        return {"defaultOutputDevice": 0, "defaultInputDevice": 1}

    def get_device_info_by_index(self, i):
        return {
            "index": i,
            "name": f"dev{i}",
            "maxInputChannels": 1,
            "defaultSampleRate": 16000,
            "isLoopbackDevice": True,  # _find_loopback вернёт это устройство сразу
        }

    def open(self, **kwargs):
        return _FakeAudioStream()

    def terminate(self):
        pass


class _DummyWriter:
    instances: list = []

    def __init__(self, path, channels, rate):
        self.closed = False
        _DummyWriter.instances.append(self)

    def write(self, data):
        pass

    def close(self):
        self.closed = True


def _fake_audio(monkeypatch):
    monkeypatch.setattr(
        recorder,
        "pyaudio",
        SimpleNamespace(PyAudio=_FakePyAudio, paWASAPI=13, paInt16=8, paContinue=0),
    )
    monkeypatch.setattr(recorder, "OpusWriter", _DummyWriter)
    _DummyWriter.instances = []


def test_record_stops_on_event_and_cleans_lock(tmp_path, monkeypatch):
    _fake_audio(monkeypatch)
    ev = threading.Event()
    ev.set()  # взведённое событие: цикл выходит на первом же такте
    out_dir = record(str(tmp_path), stop_event=ev)
    assert out_dir.exists()
    assert not (tmp_path / LOCK_NAME).exists()  # lock снят в finally
    assert _DummyWriter.instances and all(w.closed for w in _DummyWriter.instances)


def test_record_refuses_second_start(tmp_path, monkeypatch):
    _fake_audio(monkeypatch)
    (tmp_path / LOCK_NAME).write_text(
        json.dumps({"pid": os.getpid(), "folder": "x"}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="уже идёт"):
        record(str(tmp_path), stop_event=threading.Event())
