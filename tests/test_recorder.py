import json
import os
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import meet.recorder as recorder
from meet.recorder import (
    LOCK_NAME,
    WavWriter,
    _acquire_lock,
    _make_converter,
    _pid_alive,
    record,
)


# --- конвертация PCM к каноническому формату дорожки ---


def test_converter_passthrough_when_formats_match():
    conv = _make_converter(16000, 1, 16000, 1)
    data = struct.pack("<4h", 1, -2, 3, -4)
    assert conv(data) == data
    conv2 = _make_converter(48000, 2, 48000, 2)
    assert conv2(data) == data


def test_converter_averages_stereo_to_mono():
    conv = _make_converter(48000, 2, 48000, 1)
    data = struct.pack("<4h", 100, 200, -100, -300)  # 2 стерео-фрейма
    assert struct.unpack("<2h", conv(data)) == (150, -200)


def test_converter_duplicates_mono_to_stereo():
    conv = _make_converter(48000, 1, 48000, 2)
    data = struct.pack("<2h", 7, -9)
    assert struct.unpack("<4h", conv(data)) == (7, 7, -9, -9)


def test_converter_resamples_rate():
    conv = _make_converter(32000, 1, 16000, 1)
    out = conv(b"\x00\x00" * 3200)  # 0.1 с на 32 кГц
    assert abs(len(out) // 2 - 1600) <= 16  # ~0.1 с на 16 кГц


def test_converter_resample_state_carries_between_calls():
    # Нарастающий сигнал двумя вызовами: длины кусков должны сойтись к половине
    conv = _make_converter(32000, 1, 16000, 1)
    ramp = struct.pack("<320h", *range(320))
    total = len(conv(ramp)) + len(conv(ramp))
    assert abs(total // 2 - 320) <= 4


def test_converter_takes_first_two_channels_of_many():
    conv = _make_converter(16000, 4, 16000, 1)
    # 2 фрейма по 4 канала: моно = среднее первых двух каналов
    data = struct.pack("<8h", 10, 20, 999, 999, 30, 40, 999, 999)
    assert struct.unpack("<2h", conv(data)) == (15, 35)


def test_converter_many_to_stereo_needs_no_audioop(monkeypatch):
    # >2 каналов режутся array — на Python 3.13 (без audioop) должно работать
    import builtins

    real_import = builtins.__import__

    def no_audioop(name, *args, **kwargs):
        if name == "audioop":
            raise ModuleNotFoundError("No module named 'audioop'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_audioop)
    conv = _make_converter(48000, 4, 48000, 2)
    data = struct.pack("<8h", 1, 2, 999, 999, 3, 4, 999, 999)
    assert struct.unpack("<4h", conv(data)) == (1, 2, 3, 4)


# --- WavWriter (легаси-формат, транскрибируется как прежде) ---


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


# --- lock-файл записи ---


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


# --- фейки аудио-стека ---
# _FakePyAudio моделирует ключевое свойство PortAudio: снимок устройств
# делается в момент создания инстанса и дальше не обновляется.


class _FakeAudioStream:
    def __init__(self, callback=None, autostart=True):
        self.callback = callback
        self.active = autostart
        self.closed = False

    def start_stream(self):
        self.active = True

    def is_active(self):
        return self.active

    def stop_stream(self):
        self.active = False

    def close(self):
        self.closed = True

    def pump(self, data: bytes):
        """Впрыснуть PCM через callback, как это делает PortAudio."""
        return self.callback(data, len(data) // 2, None, 0)


class _FakePyAudio:
    devices: dict = {}  # {"render": {...}|None, "capture": {...}|None}
    live: list = []  # незатерминированные инстансы
    streams: list = []  # все открытые стримы по порядку
    fail_open: set = set()  # роли, у которых устройство есть, но open падает

    def __init__(self):
        cls = type(self)
        self.snapshot = {k: dict(v) if v else None for k, v in cls.devices.items()}
        self.terminated = False
        cls.live.append(self)

    def get_host_api_info_by_type(self, t):
        return {"defaultOutputDevice": 0, "defaultInputDevice": 1}

    def get_device_info_by_index(self, i):
        dev = self.snapshot["render" if i == 0 else "capture"]
        if dev is None:
            raise OSError("устройства нет в снимке")
        return {"index": i, "isLoopbackDevice": True, **dev}

    def open(self, **kwargs):
        role = "render" if kwargs["input_device_index"] == 0 else "capture"
        current = type(self).devices[role]
        # устройство из устаревшего снимка не откроется, как в WASAPI
        if current is None or current["name"] != self.snapshot[role]["name"]:
            raise OSError("устройство недоступно")
        if role in type(self).fail_open:
            raise OSError("устройство занято")
        stream = _FakeAudioStream(
            callback=kwargs.get("stream_callback"),
            autostart=kwargs.get("start", True),
        )
        type(self).streams.append(stream)
        return stream

    def terminate(self):
        self.terminated = True
        type(self).live.remove(self)


class _FakeEndpoints:
    """ids() из текущего состояния _FakePyAudio.devices — как настоящий COM,
    который видит систему, а не снимок PortAudio."""

    broken = False

    def ids(self):
        if type(self).broken:
            return None
        return tuple(
            (dev or {}).get("name")
            for dev in (_FakePyAudio.devices["render"], _FakePyAudio.devices["capture"])
        )

    def close(self):
        pass


class _DummyWriter:
    instances: list = []

    def __init__(self, path, channels, rate):
        self.channels, self.rate = channels, rate
        self.closed = False
        self.data = b""
        _DummyWriter.instances.append(self)

    def write(self, data):
        self.data += data

    def close(self):
        self.closed = True


def _dev(name, rate=16000, ch=1):
    return {"name": name, "defaultSampleRate": rate, "maxInputChannels": ch}


_UNSET = object()


def _fake_audio(monkeypatch, render=_UNSET, capture=_UNSET):
    _FakePyAudio.devices = {
        "render": _dev("Колонки") if render is _UNSET else render,
        "capture": _dev("Микрофон") if capture is _UNSET else capture,
    }
    _FakePyAudio.live = []
    _FakePyAudio.streams = []
    _FakePyAudio.fail_open = set()
    _FakeEndpoints.broken = False
    _DummyWriter.instances = []
    monkeypatch.setattr(
        recorder,
        "pyaudio",
        SimpleNamespace(
            PyAudio=_FakePyAudio, paWASAPI=13, paInt16=8, paContinue=0, paAbort=2
        ),
    )
    monkeypatch.setattr(recorder, "OpusWriter", _DummyWriter)
    monkeypatch.setattr(recorder, "_DefaultEndpoints", _FakeEndpoints)
    monkeypatch.setattr(recorder, "RESTART_MIN_S", 0.0)
    monkeypatch.setattr(recorder, "RETRY_S", 0.0)


def _session(monkeypatch, tmp_path, **kw):
    _fake_audio(monkeypatch, **kw)
    s = recorder._Session(tmp_path)
    s.start()
    return s


# --- _Track: тишина в паузу ---


def _bare_track(tmp_path):
    t = recorder._Track("mic.opus", lambda p: None, 1, tmp_path)
    t.writer = _DummyWriter("x", 1, 16000)
    t.rate, t.channels = 16000, 1
    return t


def test_pad_silence_fills_wall_clock_gap(tmp_path):
    t = _bare_track(tmp_path)
    t.bytes_written = 2 * 16000 * 2  # в ffmpeg ушло 2 с звука
    t.started = time.monotonic() - 5.0  # а по стенным часам прошло 5 с
    t._pad_silence()
    assert set(t.writer.data) == {0}
    padded_s = len(t.writer.data) / (2 * 16000)
    assert 2.8 <= padded_s <= 3.3  # ~3 с тишины
    assert t.bytes_written == 2 * 16000 * 2 + len(t.writer.data)


def test_pad_silence_ignores_capture_latency(tmp_path):
    t = _bare_track(tmp_path)
    t.bytes_written = 0
    t.started = time.monotonic() - 0.01
    t._pad_silence()
    assert t.writer.data == b""


def test_pad_silence_survives_negative_gap(tmp_path):
    t = _bare_track(tmp_path)
    t.bytes_written = 16000 * 2 * 10  # «записано» больше, чем прошло времени
    t.started = time.monotonic() - 1.0
    t._pad_silence()
    assert t.writer.data == b""


def test_tick_pad_fills_stalled_track(tmp_path):
    t = _bare_track(tmp_path)
    t.started = time.monotonic() - 3.0
    t.tick_pad()
    assert len(t.writer.data) >= 2 * int(2.8 * 16000)
    assert t._stalled is True


def test_tick_pad_leaves_healthy_track_alone(tmp_path):
    t = _bare_track(tmp_path)
    t.started = time.monotonic() - 0.5  # дефицит меньше TICK_PAD_S
    t.tick_pad()
    assert t.writer.data == b""
    assert t._stalled is False


def test_close_pads_tail_and_finalizes_writer(tmp_path):
    t = _bare_track(tmp_path)
    t.started = time.monotonic() - 3.0
    t.close()
    assert len(t.writer.data) >= 2 * int(2.8 * 16000)  # хвост залит тишиной
    assert t.writer.closed


def test_close_finalizes_writer_even_if_padding_fails(tmp_path):
    t = _bare_track(tmp_path)
    t.rate = None  # ломаем арифметику паддинга
    with pytest.raises(TypeError):
        t.close()
    assert t.writer.closed  # ffmpeg всё равно финализирован


# --- _Session: восстановление после смерти стрима и смены устройства ---


def test_session_restarts_dead_stream(tmp_path, monkeypatch):
    s = _session(monkeypatch, tmp_path)
    sys_stream, mic_stream = _FakePyAudio.streams
    mic_stream.active = False  # микрофонный стрим умер
    s.tick()
    mic = s.tracks[1]
    assert mic.stream is not None and mic.stream is not mic_stream
    assert mic.stream.is_active()
    assert len(_FakePyAudio.live) == 1  # старый инстанс терминирован


def test_session_migrates_on_default_device_change(tmp_path, monkeypatch):
    s = _session(monkeypatch, tmp_path)
    old_streams = list(_FakePyAudio.streams)
    _FakePyAudio.devices["capture"] = _dev("Наушники")
    s.tick()
    assert s.tracks[1].device_name == "Наушники"
    assert all(st.closed or not st.is_active() for st in old_streams)
    assert len(_FakePyAudio.live) == 1
    # повторный такт без изменений — стримы не трогаются
    count = len(_FakePyAudio.streams)
    s.tick()
    assert len(_FakePyAudio.streams) == count


def test_session_waits_for_absent_device_then_recovers(tmp_path, monkeypatch):
    s = _session(monkeypatch, tmp_path)
    mic = s.tracks[1]
    mic.started = time.monotonic() - 3.0  # для проверки паддинга ниже
    _FakePyAudio.devices["capture"] = None  # устройство пропало
    _FakePyAudio.streams[1].active = False
    s.tick()
    assert mic.stream is None  # ждём, не падаем
    opened = len(_FakePyAudio.streams)
    s.tick()  # устройства всё нет — здоровая дорожка не перезапускается
    assert len(_FakePyAudio.streams) == opened
    _FakePyAudio.devices["capture"] = _dev("Наушники")
    s.tick()
    assert mic.stream is not None and mic.device_name == "Наушники"
    assert len(mic.writer.data) >= 2 * int(2.8 * 16000)  # пауза залита тишиной
    assert set(mic.writer.data) == {0}


def test_session_recovers_by_death_when_com_broken(tmp_path, monkeypatch):
    s = _session(monkeypatch, tmp_path)
    _FakeEndpoints.broken = True
    _FakePyAudio.streams[1].active = False
    s.tick()
    assert s.tracks[1].stream is not None and s.tracks[1].stream.is_active()


def test_session_converts_migrated_device_format(tmp_path, monkeypatch):
    s = _session(monkeypatch, tmp_path)  # канонический формат mic: 16000/1ch
    _FakePyAudio.devices["capture"] = _dev("Наушники", rate=32000, ch=2)
    s.tick()
    mic = s.tracks[1]
    written = len(mic.writer.data)
    # 0.1 с стерео 32 кГц → должно стать ~0.1 с моно 16 кГц
    mic.stream.pump(b"\x01\x00\x01\x00" * 3200)
    got = len(mic.writer.data) - written
    assert abs(got // 2 - 1600) <= 16
    assert mic.bytes_written >= got


def test_session_keeps_single_writer_across_restarts(tmp_path, monkeypatch):
    s = _session(monkeypatch, tmp_path)
    for name in ("Наушники", "Колонки-2"):
        _FakePyAudio.devices["capture"] = _dev(name)
        s.tick()
    assert len(_DummyWriter.instances) == 2  # по одному ffmpeg на дорожку


def test_session_survives_failing_stream_close_and_terminate(tmp_path, monkeypatch):
    s = _session(monkeypatch, tmp_path)

    def boom():
        raise OSError("устройство пропало")

    for st in _FakePyAudio.streams:
        st.stop_stream = boom
        st.close = boom
        st.active = False
    broken_p = s.p
    broken_p._streams = {object()}
    orig_terminate = _FakePyAudio.terminate

    def bad_terminate(self):
        if self is broken_p and self._streams:
            raise OSError("stream close failed")
        orig_terminate(self)

    monkeypatch.setattr(_FakePyAudio, "terminate", bad_terminate)
    s.tick()  # не падает, реестр стримов вычищается, дорожки переоткрыты
    assert broken_p not in _FakePyAudio.live
    assert all(t.stream is not None for t in s.tracks)


def test_session_backs_off_when_reopen_keeps_failing(tmp_path, monkeypatch):
    # Устройство есть, но open устойчиво падает (занято exclusive-режимом):
    # ретраи не должны рвать здоровую дорожку каждые 5 с — backoff растёт.
    s = _session(monkeypatch, tmp_path)  # _fake_audio внутри ставит RETRY_S=0.0
    monkeypatch.setattr(recorder, "RETRY_S", 0.2)
    s.retry_wait = 0.2
    _FakePyAudio.fail_open = {"capture"}
    _FakePyAudio.streams[1].active = False  # микрофонный стрим умер
    s.tick()  # рестарт: mic не открылся, backoff удвоился
    assert s.tracks[1].stream is None
    assert s.retry_wait == pytest.approx(0.4)
    sys_streams = len(_FakePyAudio.streams)
    s.tick()  # рано для ретрая — здоровую дорожку не трогаем
    assert len(_FakePyAudio.streams) == sys_streams
    s.last_restart -= 1.0  # «прошло время»
    s.tick()  # ретрай по backoff'у: снова неудача, ждём ещё дольше
    assert s.retry_wait == pytest.approx(0.8)
    _FakePyAudio.fail_open = set()
    s.last_restart -= 1.0
    s.tick()  # устройство освободилось — открылись, backoff сброшен
    assert s.tracks[1].stream is not None
    assert s.retry_wait == pytest.approx(0.2)


def test_callback_exception_aborts_stream(tmp_path, monkeypatch):
    s = _session(monkeypatch, tmp_path)
    mic = s.tracks[1]
    mic.writer.write = None  # сломать writer: callback должен вернуть paAbort
    result = mic.stream.pump(b"\x00\x00" * 100)
    assert result == (None, 2)  # paAbort из фейкового pyaudio


# --- record(): запуск, остановка, восстановление ---


def test_record_stops_on_event_and_cleans_lock(tmp_path, monkeypatch):
    _fake_audio(monkeypatch)
    ev = threading.Event()
    ev.set()  # взведённое событие: цикл выходит на первом же такте
    out_dir = record(str(tmp_path), stop_event=ev)
    assert out_dir.exists()
    assert not (tmp_path / LOCK_NAME).exists()  # lock снят в finally
    assert _DummyWriter.instances and all(w.closed for w in _DummyWriter.instances)
    assert not _FakePyAudio.live  # PyAudio терминирован


def test_record_refuses_second_start(tmp_path, monkeypatch):
    _fake_audio(monkeypatch)
    (tmp_path / LOCK_NAME).write_text(
        json.dumps({"pid": os.getpid(), "folder": "x"}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="уже идёт"):
        record(str(tmp_path), stop_event=threading.Event())


def test_record_cleans_up_when_second_track_fails(tmp_path, monkeypatch):
    _fake_audio(monkeypatch, capture=None)  # микрофона нет — start() падает
    with pytest.raises(OSError):
        record(str(tmp_path), stop_event=threading.Event())
    assert not (tmp_path / LOCK_NAME).exists()
    assert _DummyWriter.instances[0].closed  # ffmpeg первой дорожки закрыт
    assert not _FakePyAudio.live


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "не дождались условия"
        time.sleep(0.05)


def test_record_reopens_dead_stream(tmp_path, monkeypatch):
    _fake_audio(monkeypatch)
    ev = threading.Event()
    th = threading.Thread(
        target=record, args=(str(tmp_path),), kwargs={"stop_event": ev}
    )
    th.start()
    try:
        _wait(lambda: len(_FakePyAudio.streams) >= 2)  # обе дорожки открылись
        _FakePyAudio.streams[1].active = False  # микрофонный стрим «умер»
        _wait(lambda: len(_FakePyAudio.streams) >= 3)  # вотчдог переоткрыл
    finally:
        ev.set()
        th.join(timeout=10)
    assert not th.is_alive()
    assert len(_DummyWriter.instances) == 2  # writer не пересоздавался
    assert not _FakePyAudio.live


def test_opus_writer_spawns_ffmpeg_without_console_window(monkeypatch):
    # meet-tray — GUI-процесс без консоли: без CREATE_NO_WINDOW каждый
    # ffmpeg-подпроцесс открывал собственное окно терминала (приёмка 02.07)
    captured = {}

    class _FakeProc:
        stdin = None

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(recorder.subprocess, "Popen", fake_popen)
    recorder.OpusWriter(Path("x.opus"), channels=1, rate=16000)
    assert captured["creationflags"] & recorder.subprocess.CREATE_NO_WINDOW
