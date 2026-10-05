"""Подпроцесс устройств: список для настроек и короткая проверка уровня.

Настоящий звук не открывается — PyAudio подменён фейком; `sleep` проверки
подменён впрыском буфера в callback, как это делает PortAudio."""

import json
import struct
from types import SimpleNamespace

import pytest

import meet.recorder as recorder
from meet import devices_probe


class _Stream:
    def __init__(self, callback):
        self.callback = callback
        self.stopped = self.closed = False

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _PA:
    """WASAPI: 0 — колонки, 1 — микрофон (системные), 2 — USB-микрофон,
    3 — loopback колонок, 4 — наушники, 5 — loopback наушников."""

    DEVICES = {
        0: {"name": "Колонки", "maxInputChannels": 0, "maxOutputChannels": 2},
        1: {"name": "Микрофон", "maxInputChannels": 1, "maxOutputChannels": 0},
        2: {"name": "USB-микрофон", "maxInputChannels": 1, "maxOutputChannels": 0},
        3: {"name": "Колонки [Loopback]", "maxInputChannels": 2, "isLoopbackDevice": True},
        4: {"name": "Наушники", "maxInputChannels": 0, "maxOutputChannels": 2},
        5: {"name": "Наушники [Loopback]", "maxInputChannels": 2, "isLoopbackDevice": True},
    }
    instances: list = []

    def __init__(self):
        self.opened = []
        self.terminated = False
        type(self).instances.append(self)

    def get_host_api_info_by_type(self, t):
        return {"index": 0, "defaultOutputDevice": 0, "defaultInputDevice": 1}

    def get_device_count(self):
        return len(self.DEVICES)

    def get_device_info_by_index(self, i):
        return {"index": i, "hostApi": 0, "defaultSampleRate": 48000,
                "isLoopbackDevice": False, **self.DEVICES[i]}

    def get_loopback_device_info_generator(self):
        for i in self.DEVICES:
            info = self.get_device_info_by_index(i)
            if info["isLoopbackDevice"]:
                yield info

    def open(self, **kw):
        stream = _Stream(kw["stream_callback"])
        self.opened.append((kw["input_device_index"], stream))
        return stream

    def terminate(self):
        self.terminated = True


@pytest.fixture
def fake_pa(monkeypatch):
    _PA.instances = []
    monkeypatch.setattr(recorder, "pyaudio", SimpleNamespace(
        PyAudio=_PA, paWASAPI=13, paInt16=8, paContinue=0, paAbort=2))
    return _PA


def test_probe_lists_inputs_and_outputs_with_defaults(fake_pa):
    got = devices_probe.probe()
    assert got["available"] is True
    assert got["inputs"] == [{"name": "Микрофон", "default": True},
                             {"name": "USB-микрофон", "default": False}]
    assert got["outputs"] == [{"name": "Колонки", "default": True},
                              {"name": "Наушники", "default": False}]
    # прежние поля — для старого окна
    assert got["system"]["name"] == "Колонки [Loopback]"
    assert got["mic"]["name"] == "Микрофон"
    assert fake_pa.instances[0].terminated


def _loud(pa_holder):
    """sleep проверки: пока «идёт запись», впрыскиваем буфер с пиком 0.5."""
    def sleep(_seconds):
        _, stream = pa_holder.instances[-1].opened[-1]
        stream.callback(struct.pack("<4h", 0, 16384, -100, 200), 4, None, 0)
    return sleep


def test_check_level_of_pinned_mic(fake_pa):
    got = devices_probe.check_level("mic", "USB-микрофон", seconds=2, sleep=_loud(fake_pa))
    assert got == {"ok": True, "peak": 0.5, "device": "USB-микрофон", "fallback": False}
    index, stream = fake_pa.instances[0].opened[0]
    assert index == 2 and stream.closed
    assert fake_pa.instances[0].terminated


def test_check_level_of_output_records_its_loopback(fake_pa):
    got = devices_probe.check_level("output", "Наушники", seconds=2, sleep=_loud(fake_pa))
    assert fake_pa.instances[0].opened[0][0] == 5
    assert got["device"] == "Наушники"  # без технического « [Loopback]»
    assert got["fallback"] is False


def test_check_level_of_system_device_and_missing_one(fake_pa):
    got = devices_probe.check_level("mic", None, seconds=2, sleep=lambda s: None)
    assert got == {"ok": True, "peak": 0.0, "device": "Микрофон", "fallback": False}
    got = devices_probe.check_level("mic", "Чужой", seconds=2, sleep=lambda s: None)
    assert got["device"] == "Микрофон" and got["fallback"] is True


def test_main_prints_one_json_line(fake_pa, capsys, monkeypatch):
    monkeypatch.setattr(devices_probe.time, "sleep", lambda s: None)
    assert devices_probe.main(["--check", "mic", "--name", "USB-микрофон",
                               "--seconds", "0.1"]) == 0
    line = capsys.readouterr().out.strip().splitlines()
    assert len(line) == 1 and json.loads(line[0])["device"] == "USB-микрофон"
    assert line[0].isascii()  # читается при любой кодировке stdout


def test_main_reports_errors_as_json(monkeypatch, capsys):
    def boom():
        raise OSError("нет WASAPI")

    monkeypatch.setattr(recorder, "pyaudio", SimpleNamespace(PyAudio=boom, paWASAPI=13))
    assert devices_probe.main([]) == 0
    got = json.loads(capsys.readouterr().out.strip())
    assert got["available"] is False and "нет WASAPI" in got["error"]
    assert devices_probe.main(["--check", "mic"]) == 0
    got = json.loads(capsys.readouterr().out.strip())
    assert got["ok"] is False and "нет WASAPI" in got["error"]


# --- запись образца голоса: --record mic --out <wav> ---------------------------


def _stereo(pa_holder, frames):
    """sleep записи: «PortAudio» отдаёт буферы стерео-микрофона по очереди."""
    def sleep(_seconds):
        _, stream = pa_holder.instances[-1].opened[-1]
        for left, right in frames:
            stream.callback(struct.pack("<2h", left, right), 1, None, 0)
    return sleep


def test_record_writes_mono_wav_of_the_pinned_mic(fake_pa, tmp_path, monkeypatch):
    import wave

    monkeypatch.setitem(_PA.DEVICES, 2, {**_PA.DEVICES[2], "maxInputChannels": 2})
    out = tmp_path / "образец.wav"
    got = devices_probe.record("USB-микрофон", out, seconds=25,
                               sleep=_stereo(fake_pa, [(100, 300), (-1000, -3000), (32767, 32767)]))
    assert got == {"ok": True, "path": str(out), "device": "USB-микрофон", "fallback": False,
                   "seconds": round(3 / 48000, 3), "rate": 48000}
    with wave.open(str(out), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 48000)
        assert struct.unpack("<3h", w.readframes(3)) == (200, -2000, 32767)
    index, stream = fake_pa.instances[0].opened[0]
    assert index == 2 and stream.closed and fake_pa.instances[0].terminated


def test_record_falls_back_to_system_mic(fake_pa, tmp_path):
    got = devices_probe.record("Чужой", tmp_path / "a.wav", seconds=1, sleep=lambda s: None)
    assert got["device"] == "Микрофон" and got["fallback"] is True
    assert got["seconds"] == 0.0  # тишина без буферов — пустой, но честный файл


def test_record_failure_leaves_no_file(fake_pa, tmp_path, monkeypatch):
    def broken(self, **kw):
        raise OSError("устройство занято")

    monkeypatch.setattr(_PA, "open", broken)
    out = tmp_path / "a.wav"
    with pytest.raises(OSError):
        devices_probe.record(None, out, seconds=1, sleep=lambda s: None)
    assert not out.exists()


def test_main_record_prints_json_and_requires_out(fake_pa, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(devices_probe.time, "sleep", lambda s: None)
    out = tmp_path / "образец.wav"
    assert devices_probe.main(["--record", "mic", "--seconds", "25", "--out", str(out)]) == 0
    got = json.loads(capsys.readouterr().out.strip())
    assert got["ok"] is True and got["path"] == str(out) and out.exists()
    assert devices_probe.main(["--record", "mic"]) == 0
    got = json.loads(capsys.readouterr().out.strip())
    assert got["ok"] is False and "--out" in got["error"]


def test_record_writes_nothing_when_the_resident_is_gone(fake_pa, tmp_path):
    """Резидент умер посреди записи: удалить файл было бы некому — не пишем."""
    out = tmp_path / "a.wav"
    with pytest.raises(devices_probe.ParentGone):
        devices_probe.record(None, out, seconds=1, sleep=_stereo(fake_pa, [(1, 1)]),
                             parent_pid=4242, alive=lambda pid: False)
    assert not out.exists()
    _, stream = fake_pa.instances[0].opened[0]
    assert stream.closed and fake_pa.instances[0].terminated
    got = devices_probe.record(None, out, seconds=1, sleep=lambda s: None, parent_pid=4242,
                               alive=lambda pid: pid == 4242)
    assert got["ok"] and out.exists()


def test_main_record_passes_parent_pid(fake_pa, tmp_path, capsys, monkeypatch):
    import meet.plat as plat

    monkeypatch.setattr(devices_probe.time, "sleep", lambda s: None)
    monkeypatch.setattr(plat, "pid_alive", lambda pid: False)
    out = tmp_path / "a.wav"
    assert devices_probe.main(["--record", "mic", "--out", str(out), "--parent-pid", "4242"]) == 0
    got = json.loads(capsys.readouterr().out.strip())
    assert got["ok"] is False and "4242" in got["error"] and not out.exists()
