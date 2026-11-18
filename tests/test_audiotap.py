"""macOS: помощник meet-audiotap и запись через него — без Mac.

Помощник подменяется фейковым процессом (рукопожатие, PCM, коды выхода),
sounddevice — фейковым модулем. Проверяется то, что ломается без Mac
незаметно: протокол, отказ в разрешении «Запись экрана» с понятным текстом,
выбор устройств и то, что дорожки записи получают звук."""

import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from meet import audiotap, events, mac_audio, recorder

FIXTURES = Path(__file__).parent / "fixtures" / "mac"
HANDSHAKE = b'{"meet_audiotap": 1, "rate": 48000, "channels": 1, "format": "s16le"}\n'


# --- протокол ------------------------------------------------------------------


def test_handshake_is_checked():
    assert audiotap.parse_handshake(HANDSHAKE) == {"rate": 48000, "channels": 1}
    for bad in (b"not json\n", b'{"meet_audiotap": 2, "rate": 48000}\n',
                b'{"meet_audiotap": 1}\n', b'{"meet_audiotap": 1, "rate": 0}\n',
                b'{"meet_audiotap": 1, "rate": 48000, "format": "f32le"}\n'):
        with pytest.raises(audiotap.TapError):
            audiotap.parse_handshake(bad)


def test_exit_codes_become_russian_notices():
    permission = audiotap.notice_for_exit(audiotap.EXIT_PERMISSION)
    assert "Запись экрана" in permission and "Конфиденциальность и безопасность" in permission
    assert "BlackHole" in permission
    assert "macOS 13" in audiotap.notice_for_exit(audiotap.EXIT_UNSUPPORTED)
    other = audiotap.notice_for_exit(70, "SCStream: сбой\n  подробности")
    assert other.endswith("кодом 70: SCStream: сбой подробности")


def test_stream_command():
    assert audiotap.stream_command("/r/meet-audiotap", 48000, 1) == [
        "/r/meet-audiotap", "--stream", "--rate", "48000", "--channels", "1"]
    # Звук самого Meet (окно с плеером записи) — мимо захвата (0.5).
    assert audiotap.stream_command("/r/meet-audiotap", 48000, 1, exclude_pid=4242)[-2:] == [
        "--exclude-pid", "4242"]


def test_tap_stream_excludes_the_shell_unless_switched_off(monkeypatch):
    monkeypatch.setenv("MEET_EXCLUDE_PID", "4242")
    popen, calls = _popen_factory(HANDSHAKE)
    stream = mac_audio.TapStream(48000, 1, lambda *a: (None, mac_audio.paContinue), 1024,
                                 popen=popen, helper="/r/meet-audiotap")
    stream.start_stream()
    stream.close()
    assert calls[0][-2:] == ["--exclude-pid", "4242"]
    monkeypatch.setenv("MEET_PROCESS_LOOPBACK", "0")
    stream = mac_audio.TapStream(48000, 1, lambda *a: (None, mac_audio.paContinue), 1024,
                                 popen=popen, helper="/r/meet-audiotap")
    stream.start_stream()
    stream.close()
    assert "--exclude-pid" not in calls[1]


def test_mic_users_fixture_is_parsed():
    users = audiotap.parse_mic_users((FIXTURES / "mic_users.json").read_text(encoding="utf-8"))
    assert [u["name"] for u in users] == ["zoom.us", "Google Chrome Helper", "coreaudiod"]
    assert users[0] == {"pid": 501, "name": "zoom.us", "bundle": "us.zoom.xos",
                        "input": True, "output": True}
    unsupported = (FIXTURES / "mic_users_unsupported.json").read_text(encoding="utf-8")
    assert audiotap.parse_mic_users(unsupported) is None
    assert audiotap.parse_mic_users("") is None
    assert audiotap.parse_mic_users("мусор") is None


def test_mic_users_runs_the_helper(monkeypatch, tmp_path):
    helper = tmp_path / "meet-audiotap"
    helper.write_bytes(b"")
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(helper))
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs["timeout"]))
        return SimpleNamespace(returncode=0,
                               stdout=(FIXTURES / "mic_users.json").read_text(encoding="utf-8"))

    assert len(audiotap.mic_users(run=run)) == 3
    assert seen == [([str(helper), "--mic-users"], audiotap.MIC_USERS_TIMEOUT_S)]
    failed = audiotap.mic_users(run=lambda *a, **k: SimpleNamespace(returncode=70, stdout=""))
    assert failed is None
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
    assert audiotap.helper_path() is None
    assert audiotap.mic_users(run=run) is None


# --- разрешения: микрофон и «Запись экрана» ------------------------------------


@pytest.mark.parametrize("status", ["granted", "denied", "notDetermined", "restricted"])
def test_mic_status_protocol_is_parsed(status):
    line = json.dumps({"microphone": status}) + "\n"
    assert audiotap.parse_permission(line, "microphone") == status
    # Строка JSON — последняя: системный шум перед ней не мешает.
    assert audiotap.parse_permission("предупреждение\n" + line, "microphone") == status


@pytest.mark.parametrize("text", [
    "", "мусор", "[]", '{"microphone": "maybe"}', '{"microphone": 1}', '{"screen": "granted"}',
])
def test_permission_answer_out_of_protocol_is_unknown(text):
    assert audiotap.parse_permission(text, "microphone") is None


def _permission_helper(monkeypatch, tmp_path, answers: dict):
    """Помощник с режимами разрешений: argv → (код, stdout)."""
    helper = tmp_path / "meet-audiotap"
    helper.write_bytes(b"")
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(helper))
    seen = []

    def run(argv, **kwargs):
        seen.append((argv[1:], kwargs["timeout"]))
        code, stdout = answers[argv[1]]
        return SimpleNamespace(returncode=code, stdout=stdout)

    return run, seen


def test_permission_modes_run_the_helper(monkeypatch, tmp_path):
    run, seen = _permission_helper(monkeypatch, tmp_path, {
        "--mic-status": (0, '{"microphone": "notDetermined"}\n'),
        "--request-mic": (0, '{"microphone": "granted"}\n'),
        "--request-screen": (0, '{"screen": "denied"}\n'),
    })
    assert audiotap.mic_status(run=run) == audiotap.MIC_NOT_DETERMINED
    assert audiotap.request_mic(run=run) == audiotap.MIC_GRANTED
    assert audiotap.request_screen(run=run) == audiotap.SCREEN_DENIED
    assert seen == [
        (["--mic-status"], audiotap.PERMISSION_TIMEOUT_S),
        # Системный вопрос ждёт человека — таймаут длинный.
        (["--request-mic"], audiotap.REQUEST_TIMEOUT_S),
        (["--request-screen"], audiotap.REQUEST_TIMEOUT_S),
    ]


def test_permission_modes_without_an_answer_are_unknown(monkeypatch, tmp_path):
    # Прежний помощник режимов не знает: код 64 и строка «использование» в stderr.
    run, _ = _permission_helper(monkeypatch, tmp_path, {
        "--mic-status": (audiotap.EXIT_USAGE, ""),
        "--request-mic": (audiotap.EXIT_USAGE, ""),
        "--request-screen": (audiotap.EXIT_USAGE, ""),
    })
    assert audiotap.mic_status(run=run) is None
    assert audiotap.request_mic(run=run) is None
    assert audiotap.request_screen(run=run) is None

    def hung(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert audiotap.mic_status(run=hung) is None
    assert audiotap.request_mic(run=hung) is None
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
    assert audiotap.mic_status(run=lambda *a, **k: pytest.fail("помощника нет")) is None
    assert audiotap.request_screen(run=lambda *a, **k: pytest.fail("помощника нет")) is None


def test_screen_status_reads_the_preflight_code(monkeypatch, tmp_path):
    helper = tmp_path / "meet-audiotap"
    helper.write_bytes(b"")
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(helper))
    seen = []

    def answer(code):
        def run(argv, **kwargs):
            seen.append((argv[1:], kwargs["timeout"]))
            return SimpleNamespace(returncode=code, stdout="", stderr="")
        return run

    assert audiotap.screen_status(run=answer(0)) == audiotap.SCREEN_GRANTED
    assert audiotap.screen_status(run=answer(audiotap.EXIT_PERMISSION)) == audiotap.SCREEN_DENIED
    # Сбой или незнакомый ответ — не «запрещено», а «неизвестно».
    assert audiotap.screen_status(run=answer(audiotap.EXIT_FAILED)) is None
    assert seen[0] == (["--preflight"], audiotap.PREFLIGHT_TIMEOUT_S)

    def hung(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert audiotap.screen_status(run=hung) is None
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
    assert audiotap.screen_status(run=lambda *a, **k: pytest.fail("помощника нет")) is None


def test_permissions_report_both_and_what_is_missing(monkeypatch, tmp_path):
    run, _ = _permission_helper(monkeypatch, tmp_path, {
        "--mic-status": (0, '{"microphone": "denied"}\n'),
        "--preflight": (0, ""),
    })
    assert audiotap.permissions(run=run) == {"microphone": "denied", "screen": "granted"}
    assert audiotap.missing_permissions({"microphone": "denied", "screen": "granted"}) == ["microphone"]
    assert audiotap.missing_permissions(
        {"microphone": "notDetermined", "screen": "denied"}) == ["microphone", "screen"]
    # «Неизвестно» (прежний помощник, его нет) — не повод тревожить человека.
    assert audiotap.missing_permissions({"microphone": None, "screen": None}) == []
    assert audiotap.missing_permissions({"microphone": "granted", "screen": "granted"}) == []


# --- фейковый помощник ---------------------------------------------------------


class FakeHelper:
    """Процесс помощника: stdout — рукопожатие и PCM, код выхода по сценарию."""

    instances: list = []

    def __init__(self, stdout: bytes, code: "int | None" = None, stderr: bytes = b"") -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.stdin = io.BytesIO()
        self.code = code
        self.killed = False

    def poll(self):
        if self.stdin.closed or self.killed:
            return 0 if self.code is None else self.code
        return self.code

    def wait(self, timeout=None):
        return self.poll() if self.poll() is not None else 0

    def kill(self):
        self.killed = True


def _popen_factory(stdout: bytes, code=None, stderr=b""):
    calls = []

    def popen(argv, **kwargs):
        calls.append(argv)
        proc = FakeHelper(stdout, code, stderr)
        FakeHelper.instances.append(proc)
        return proc

    return popen, calls


def _pcm(frames: int, value: int = 1000) -> bytes:
    return value.to_bytes(2, "little", signed=True) * frames


def test_tap_stream_pumps_pcm_to_the_callback():
    got = []
    done = threading.Event()

    def callback(data, frames, _time, _status):
        got.append((len(data), frames))
        if sum(n for n, _ in got) >= 2048 * 2:
            done.set()
        return (None, mac_audio.paContinue)

    popen, calls = _popen_factory(HANDSHAKE + _pcm(2048 * 2 + 3))
    stream = mac_audio.TapStream(48000, 1, callback, 1024, popen=popen, helper="/r/meet-audiotap")
    stream.start_stream()
    assert calls == [["/r/meet-audiotap", "--stream", "--rate", "48000", "--channels", "1"]]
    assert done.wait(2.0)
    stream.close()
    assert got[0] == (2048, 1024)
    assert all(n % 2 == 0 for n, _ in got)  # нечётный хвост не уходит в дорожку


def test_permission_denied_is_a_clear_notice():
    popen, _ = _popen_factory(b"", code=audiotap.EXIT_PERMISSION, stderr=b"declined")
    stream = mac_audio.TapStream(48000, 1, lambda *a: (None, 0), 1024, popen=popen,
                                 helper="/r/meet-audiotap")
    with pytest.raises(audiotap.TapError) as err:
        stream.start_stream()
    assert err.value.code == audiotap.EXIT_PERMISSION
    assert err.value.notice == audiotap.PERMISSION_NOTICE
    assert stream.notice == audiotap.PERMISSION_NOTICE


def test_helper_of_another_format_is_refused():
    other = b'{"meet_audiotap": 1, "rate": 16000, "channels": 1, "format": "s16le"}\n'
    popen, _ = _popen_factory(other)
    stream = mac_audio.TapStream(48000, 1, lambda *a: (None, 0), 1024, popen=popen,
                                 helper="/r/meet-audiotap")
    with pytest.raises(audiotap.TapError, match="16000"):
        stream.start_stream()
    assert FakeHelper.instances[-1].killed


def test_missing_helper_explains_blackhole(monkeypatch, tmp_path):
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
    stream = mac_audio.TapStream(48000, 1, lambda *a: (None, 0), 1024)
    with pytest.raises(audiotap.TapError) as err:
        stream.start_stream()
    assert "BlackHole" in err.value.notice


# --- запись на macOS: фейковый sounddevice ---------------------------------------


class FakeRawInputStream:
    instances: list = []

    def __init__(self, samplerate, channels, dtype, device, blocksize, callback):
        assert dtype == "int16"
        self.device, self.callback = device, callback
        self.active = False
        FakeRawInputStream.instances.append(self)

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.active = False

    def feed(self, data: bytes):
        self.callback(data, len(data) // 2, None, 0)


class CallbackAbort(Exception):
    pass


DEVICES = [
    {"index": 0, "name": "Микрофон MacBook Pro", "max_input_channels": 1,
     "max_output_channels": 0, "default_samplerate": 48000.0, "hostapi": 0},
    {"index": 1, "name": "Динамики MacBook Pro", "max_input_channels": 0,
     "max_output_channels": 2, "default_samplerate": 48000.0, "hostapi": 0},
    {"index": 2, "name": "BlackHole 2ch", "max_input_channels": 2,
     "max_output_channels": 2, "default_samplerate": 48000.0, "hostapi": 0},
]


def _fake_sd():
    def query_devices(index=None):
        return list(DEVICES) if index is None else DEVICES[index]

    return SimpleNamespace(
        query_devices=query_devices,
        default=SimpleNamespace(device=(0, 1)),
        RawInputStream=FakeRawInputStream,
        CallbackAbort=CallbackAbort,
        _initialize=lambda: None,
        _terminate=lambda: None,
    )


class _Writer:
    def __init__(self, path, channels, rate):
        self.path, self.channels, self.rate = path, channels, rate
        self.data = bytearray()
        self.lock = threading.Lock()
        self.closed = False

    def write(self, data):
        with self.lock:
            self.data += data

    def close(self):
        self.closed = True


@pytest.fixture
def mac_recorder(monkeypatch, tmp_path):
    """recorder в режиме macOS: mac_audio поверх фейкового sounddevice,
    помощник — фейковый процесс."""
    sd = _fake_sd()
    monkeypatch.setattr(mac_audio, "_sd", lambda: sd)
    monkeypatch.setattr(recorder, "_MAC", True)
    monkeypatch.setattr(recorder, "pyaudio", mac_audio)
    monkeypatch.setattr(recorder, "OpusWriter", _Writer)
    helper = tmp_path / "meet-audiotap"
    helper.write_bytes(b"")
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(helper))
    FakeRawInputStream.instances = []
    FakeHelper.instances = []
    # Слежение за устройствами (`--watch-devices`): по умолчанию помощник
    # молчит — как прежний, который режима не знает.
    monkeypatch.setattr(mac_audio, "_watch_popen", lambda argv, **kw: _WatchProc(_LineFeed(ended=True)))
    return SimpleNamespace(sd=sd, helper=helper)


def test_backend_is_chosen_by_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert recorder.audio_backend() is mac_audio
    monkeypatch.setattr(sys, "platform", "win32")
    fake = SimpleNamespace(name="pyaudiowpatch")
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake)
    assert recorder.audio_backend() is fake


def test_mac_device_list_offers_system_audio_and_blackhole(mac_recorder):
    p = mac_audio.PyAudio()
    devices = recorder.list_devices(p)
    assert devices["inputs"] == [
        {"name": "Микрофон MacBook Pro", "default": True},
        {"name": "BlackHole 2ch", "default": False},
    ]
    assert devices["outputs"][0] == {"name": mac_audio.SYSTEM_AUDIO_NAME, "default": True}
    assert {"name": "BlackHole 2ch", "default": False} in devices["outputs"]
    dev, fell_back = recorder.resolve_device(p, "output", "BlackHole 2ch")
    assert (dev["name"], dev["index"], fell_back) == ("BlackHole 2ch", 2, False)
    dev, fell_back = recorder.resolve_device(p, "output", "Нет такого")
    assert (dev["index"], fell_back) == (mac_audio.TAP_INDEX, True)
    dev, fell_back = recorder.resolve_device(p, "mic", None)
    assert (dev["name"], fell_back) == ("Микрофон MacBook Pro", False)
    # Виртуальный «системный звук» микрофоном не считается.
    assert recorder._find_mic(p, mac_audio.SYSTEM_AUDIO_NAME) is None


def test_mac_session_records_both_tracks(mac_recorder, monkeypatch, tmp_path):
    popen, calls = _popen_factory(HANDSHAKE + _pcm(48000))
    monkeypatch.setattr(mac_audio, "_popen", popen)
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    s = recorder._Session(tmp_path, bus, mic_device=None, output_device=None)
    s.start()
    sys_track, mic_track = s.tracks
    assert sys_track.device_name == mac_audio.SYSTEM_AUDIO_NAME
    assert (sys_track.rate, sys_track.channels) == (48000, 1)
    assert mic_track.device_name == "Микрофон MacBook Pro"
    assert calls[0][1:] == ["--stream", "--rate", "48000", "--channels", "1"]
    FakeRawInputStream.instances[0].feed(_pcm(1024))
    deadline = time.monotonic() + 2.0
    while len(sys_track.writer.data) < 2 * 48000 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(sys_track.writer.data) >= 2 * 48000
    assert len(mic_track.writer.data) >= 2048
    s.tick()  # без COM: смену устройства не отслеживаем, но и не падаем
    s.close()
    assert sys_track.writer.closed and mic_track.writer.closed
    log = (tmp_path / "record.log").read_text(encoding="utf-8")
    assert "COM" not in log  # на macOS про COM не пишем


def _same_mic_session(monkeypatch, tmp_path, **devices):
    popen, _ = _popen_factory(HANDSHAKE + _pcm(48000))
    monkeypatch.setattr(mac_audio, "_popen", popen)
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    s = recorder._Session(tmp_path, bus, **devices)
    s.start()
    return s, seen


@pytest.mark.parametrize("devices", [
    # Микрофон системный (AirPods стали им при подключении), «звук собеседников» — они же.
    {"mic_device": None, "output_device": "Микрофон MacBook Pro"},
    # Оба выбраны вручную — одно устройство.
    {"mic_device": "BlackHole 2ch", "output_device": "BlackHole 2ch"},
])
def test_mac_output_that_is_the_microphone_records_system_audio(mac_recorder, monkeypatch, tmp_path, devices):
    """0.6.1: на Mac в «Звук собеседников» есть все устройства ввода (ради BlackHole), и
    выбирают свои наушники с микрофоном. Тогда обе дорожки — один микрофон: собеседников
    нет, свой голос дважды (и дедупликация считает его эхом). Тот же микрофон, что у
    дорожки голоса, — не источник звука собеседников: пишем системный звук и говорим почему."""
    s, seen = _same_mic_session(monkeypatch, tmp_path, **devices)
    sys_track, mic_track = s.tracks
    assert sys_track.device_name == mac_audio.SYSTEM_AUDIO_NAME
    assert mic_track.device_name == (devices["mic_device"] or "Микрофон MacBook Pro")
    fallbacks = [e for e in seen if e.kind == events.RECORD_DEVICE_FALLBACK]
    assert len(fallbacks) == 1
    assert fallbacks[0].data["role"] == "output"
    assert fallbacks[0].data["reason"] == recorder.FALLBACK_SAME_AS_MIC
    assert fallbacks[0].data["wanted"] == devices["output_device"]
    s.close()
    log = (tmp_path / "record.log").read_text(encoding="utf-8")
    assert (f"Выбранный источник звука собеседников {devices['output_device']} — тот же микрофон, "
            "что пишет ваш голос; записываю системный звук") in log


def test_mac_blackhole_next_to_a_real_microphone_stays(mac_recorder, monkeypatch, tmp_path):
    s, seen = _same_mic_session(monkeypatch, tmp_path, mic_device=None, output_device="BlackHole 2ch")
    assert s.tracks[0].device_name == "BlackHole 2ch"
    assert not [e for e in seen if e.kind == events.RECORD_DEVICE_FALLBACK]
    s.close()


def test_missing_output_fallback_carries_its_reason(mac_recorder, monkeypatch, tmp_path):
    s, seen = _same_mic_session(monkeypatch, tmp_path, mic_device=None, output_device="Нет такого")
    fallbacks = [e for e in seen if e.kind == events.RECORD_DEVICE_FALLBACK]
    assert [e.data["reason"] for e in fallbacks] == [recorder.FALLBACK_MISSING]
    s.close()


def test_mac_session_without_screen_recording_permission_records_the_mic_only(
        mac_recorder, monkeypatch, tmp_path):
    """Нет разрешения «Запись экрана»: запись не отменяется — микрофон пишется,
    дорожка собеседников — тишина, в окне плашка, в meta.json — пометка."""
    popen, calls = _popen_factory(b"", code=audiotap.EXIT_PERMISSION)
    monkeypatch.setattr(mac_audio, "_popen", popen)
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    s = recorder._Session(tmp_path, bus, mic_device=None, output_device=None)
    s.start()
    sys_track, mic_track = s.tracks
    assert sys_track.missing and sys_track.stream is None
    assert (sys_track.rate, sys_track.channels) == (48000, 1)
    assert mic_track.stream is not None
    missing = [e for e in seen if e.kind == events.RECORD_SYSTEM_AUDIO]
    assert [e.data["state"] for e in missing] == ["missing"]
    assert missing[0].data["permission"] is True
    assert missing[0].data["notice"] == audiotap.SYSTEM_AUDIO_MISSING
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))["system_audio"] == "missing"
    from meet import library

    assert library.read_meta(tmp_path)["system_audio"] == "missing"
    # Ретрая помощника на каждом такте нет: он рвал бы и микрофон.
    spawned = len(calls)
    s.last_restart -= 100
    s.tick()
    assert len(calls) == spawned
    FakeRawInputStream.instances[0].feed(_pcm(1024))
    assert len(mic_track.writer.data) >= 2048
    s.close()
    assert sys_track.writer.closed
    log = (tmp_path / "record.log").read_text(encoding="utf-8")
    assert "пишу только микрофон" in log


def test_permission_granted_mid_meeting_restores_the_track(mac_recorder, monkeypatch, tmp_path):
    denied, _ = _popen_factory(b"", code=audiotap.EXIT_PERMISSION)
    monkeypatch.setattr(mac_audio, "_popen", denied)
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    s = recorder._Session(tmp_path, bus, mic_device=None, output_device=None)
    s.start()
    granted, _ = _popen_factory(HANDSHAKE + _pcm(100))
    monkeypatch.setattr(mac_audio, "_popen", granted)
    assert s.tracks[0].reopen(s.p) is True
    assert not s.tracks[0].missing
    states = [e.data["state"] for e in seen if e.kind == events.RECORD_SYSTEM_AUDIO]
    assert states == ["missing", "restored"]
    assert json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))["system_audio"] == "partial"
    s.close()


def test_missing_helper_also_records_the_mic_only(mac_recorder, monkeypatch, tmp_path):
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    s = recorder._Session(tmp_path, bus, mic_device=None, output_device=None)
    s.start()
    event = next(e for e in seen if e.kind == events.RECORD_SYSTEM_AUDIO)
    assert event.data["permission"] is False
    assert event.data["notice"] == audiotap.MISSING_NOTICE
    assert s.tracks[0].missing and s.tracks[1].stream is not None
    s.close()


def test_windows_track_failure_still_refuses(monkeypatch, tmp_path):
    """Вне macOS отказ устройства — как раньше: исключение, записи нет."""
    monkeypatch.setattr(recorder, "_MAC", False)
    track = recorder._Track("sys.opus", lambda p: (_ for _ in ()).throw(RuntimeError("нет loopback")),
                            0, tmp_path, lambda m: None)
    with pytest.raises(RuntimeError):
        track.first_open(object())
    assert not track.missing


def test_snapshot_shows_the_missing_system_audio_while_recording(monkeypatch, tmp_path):
    from meet import tray, tray_control

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    control = tray_control.TrayControl(tray.TrayApp())
    control.tray.recording = True
    assert control.snapshot()["system_audio_missing"] is None
    control.bus.emit(events.RECORD_SYSTEM_AUDIO, track="sys.opus", state="missing",
                     notice=audiotap.SYSTEM_AUDIO_MISSING, permission=True)
    assert control.snapshot()["system_audio_missing"] == {
        "notice": audiotap.SYSTEM_AUDIO_MISSING, "permission": True}
    control.bus.emit(events.RECORD_SYSTEM_AUDIO, track="sys.opus", state="restored")
    assert control.snapshot()["system_audio_missing"] is None
    control.bus.emit(events.RECORD_SYSTEM_AUDIO, track="sys.opus", state="missing",
                     notice="x", permission=False)
    control.bus.emit(events.RECORD_STOPPED, folder="x", duration_s=1.0)
    assert control.snapshot()["system_audio_missing"] is None


def test_library_describes_the_missing_system_audio(tmp_path):
    from meet import library

    folder = tmp_path / "2026-10-02_10-00"
    folder.mkdir()
    (folder / "mic.opus").write_bytes(b"")
    (folder / "sys.opus").write_bytes(b"")
    library.write_meta(folder, {"system_audio": "missing"})
    assert library.describe(folder).to_raw()["system_audio"] == "missing"
    library.write_meta(folder, {"system_audio": "что-то"})
    assert library.describe(folder).to_raw()["system_audio"] is None


# --- stderr помощника и зависание -------------------------------------------------

FLOOD_HELPER = r"""
import sys
NL = chr(10)
sys.stderr.write(("буфер звука не прочитан: ошибка формата" + NL) * 8000)  # ~400 КБ
sys.stderr.flush()
sys.stdout.buffer.write(b'{"meet_audiotap": 1, "rate": 48000, "channels": 1, "format": "s16le"}' + NL.encode())
sys.stdout.buffer.write(bytes([16, 0]) * 4096)
sys.stdout.buffer.flush()
for _ in range(2000):
    sys.stderr.write("буфер звука не прочитан: снова" + NL)
sys.stderr.flush()
sys.stdout.buffer.write(bytes([16, 0]) * 4096)
sys.stdout.buffer.flush()
sys.stdin.read()
"""


def test_helper_flooding_stderr_does_not_stall_the_audio():
    """Настоящий процесс пишет в stderr больше, чем вмещает канал (64 КБ):
    без чтения stderr он встал бы до рукопожатия и до звука."""
    import subprocess

    def popen(argv, **kwargs):
        return subprocess.Popen([sys.executable, "-c", FLOOD_HELPER], **kwargs)

    got = bytearray()
    done = threading.Event()
    lines = []

    def callback(data, frames, _time, _status):
        got.extend(data)
        if len(got) >= 2 * 8192:
            done.set()
        return (None, mac_audio.paContinue)

    stream = mac_audio.TapStream(48000, 1, callback, 1024, popen=popen,
                                 helper="meet-audiotap", log=lines.append)
    stream.start_stream()
    try:
        assert done.wait(20.0), "звук не дошёл: помощник встал на stderr"
    finally:
        stream.close()
    deadline = time.monotonic() + 5.0
    while stream.stderr_lines < 10000 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert stream.stderr_lines == 10000
    assert len(stream.stderr_tail) == mac_audio.STDERR_TAIL
    # В журнал записи — не на каждую строку: раз в STDERR_LOG_EVERY_S.
    assert 1 <= len(lines) <= 3
    assert lines[0].startswith("meet-audiotap: буфер звука не прочитан")


class _BlockingOut:
    """stdout живого, но зависшего помощника: рукопожатие, немного звука и
    дальше ничего, пока поток не закроют."""

    def __init__(self, first: bytes) -> None:
        self._line = HANDSHAKE
        self._first = first
        self.closed = threading.Event()

    def readline(self):
        line, self._line = self._line, b""
        return line

    def read(self, size):
        if self._first:
            data, self._first = self._first[:size], self._first[size:]
            return data
        self.closed.wait(5.0)
        return b""


class _ClosingIn:
    """stdin помощника: закрыли — помощник выходит (его stdout кончается)."""

    def __init__(self, done: threading.Event) -> None:
        self.closed = False
        self._done = done

    def close(self):
        self.closed = True
        self._done.set()


class _HungHelper(FakeHelper):
    def __init__(self) -> None:
        super().__init__(b"")
        self.stdout = _BlockingOut(_pcm(1024))
        self.stdin = _ClosingIn(self.stdout.closed)

    def poll(self):
        return 0 if self.killed or self.stdin.closed else None

    def kill(self):
        self.killed = True
        self.stdout.closed.set()


def test_hung_helper_is_reported_and_restarted(monkeypatch):
    monkeypatch.setattr(mac_audio, "STALL_S", 0.2)
    lines = []
    helper = _HungHelper()
    stream = mac_audio.TapStream(48000, 1, lambda *a: (None, mac_audio.paContinue), 1024,
                                 popen=lambda argv, **kw: helper, helper="meet-audiotap",
                                 log=lines.append)
    stream.start_stream()
    assert stream.is_active()
    time.sleep(0.35)
    assert stream.is_active() is False  # вотчдог записи перезапустит дорожку
    assert stream.is_active() is False
    assert stream.notice == audiotap.STALL_NOTICE
    assert lines.count(audiotap.STALL_NOTICE) == 1
    helper.stdout.closed.set()
    stream.close()


def test_mac_session_with_blackhole_needs_no_helper(mac_recorder, monkeypatch, tmp_path):
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
    s = recorder._Session(tmp_path, None, mic_device=None, output_device="BlackHole 2ch")
    s.start()
    assert s.tracks[0].device_name == "BlackHole 2ch"
    assert s.tracks[0].channels == 2
    assert [st.device for st in FakeRawInputStream.instances] == [2, 0]
    s.close()


def test_reopen_after_permission_loss_logs_the_notice(mac_recorder, monkeypatch, tmp_path):
    """Разрешение отозвали посреди записи: дорожка ждёт и в журнале — почему."""
    popen, _ = _popen_factory(HANDSHAKE + _pcm(100))
    monkeypatch.setattr(mac_audio, "_popen", popen)
    s = recorder._Session(tmp_path, None, mic_device=None, output_device=None)
    s.start()
    denied, _ = _popen_factory(b"", code=audiotap.EXIT_PERMISSION)
    monkeypatch.setattr(mac_audio, "_popen", denied)
    track = s.tracks[0]
    track.close_stream()
    assert track.reopen(s.p) is False
    s.close()
    log = (tmp_path / "record.log").read_text(encoding="utf-8")
    assert "Запись экрана" in log


def test_devices_probe_lists_mac_devices_without_helper(mac_recorder, monkeypatch, tmp_path):
    from meet import devices_probe

    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
    result = devices_probe.probe()
    assert result["available"] is True
    assert result["system"] is None
    assert result["mic"]["name"] == "Микрофон MacBook Pro"
    assert all(o["name"] != mac_audio.SYSTEM_AUDIO_NAME for o in result["outputs"])
    json.dumps(result)


# --- системный звук на macOS: классы отказов, backoff, preflight ---------------------


class _NoHandshakeOut:
    """stdout помощника, который не отвечает: readline висит, пока его не убьют."""

    def __init__(self) -> None:
        self.closed = threading.Event()

    def readline(self):
        self.closed.wait(5.0)
        return b""

    def read(self, size):
        self.closed.wait(5.0)
        return b""


class _SilentHelper(FakeHelper):
    def __init__(self) -> None:
        super().__init__(b"")
        self.stdout = _NoHandshakeOut()

    def poll(self):
        return 0 if self.killed else None

    def kill(self):
        self.killed = True
        self.stdout.closed.set()


def _alive_popen():
    """Живой помощник: рукопожатие, немного звука, дальше — ждёт (не падает)."""
    calls = []

    def popen(argv, **kwargs):
        calls.append(argv)
        helper = _HungHelper()
        FakeHelper.instances.append(helper)
        return helper

    return popen, calls


def _start(mac_recorder, monkeypatch, tmp_path, popen):
    monkeypatch.setattr(mac_audio, "_popen", popen)
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    s = recorder._Session(tmp_path, bus, mic_device=None, output_device=None)
    s.start()
    return s, seen


def _meta(tmp_path):
    return json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))


def _missing(seen):
    return [e.data for e in seen if e.kind == events.RECORD_SYSTEM_AUDIO]


def _failed_start(kind):
    if kind == "exit70":
        return _popen_factory(b"", code=audiotap.EXIT_FAILED, stderr=b"SCStream: no display")[0]
    if kind == "protocol":
        return _popen_factory(b"not a handshake\n")[0]
    if kind == "format":
        return _popen_factory(b'{"meet_audiotap": 1, "rate": 16000, "channels": 1}\n')[0]
    return lambda argv, **kw: _SilentHelper()  # «timeout»


@pytest.mark.parametrize("kind", ["exit70", "protocol", "format", "timeout"])
def test_transient_start_failure_is_retried_with_backoff(mac_recorder, monkeypatch, tmp_path, kind):
    """Помощник не стартовал не из-за разрешения — запись идёт с микрофона, а
    помощник пробуется снова через 2, 4 … с; карточка не винит разрешение."""
    monkeypatch.setattr(mac_audio, "HANDSHAKE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(audiotap, "preflight", lambda: pytest.fail("preflight не нужен"))
    # Время следующей попытки считается от разных точек (до или после рукопожатия)
    # — проверяем, что оно между «до вызова + пауза» и «после вызова + пауза»:
    # под нагрузкой рукопожатие и убийство помощника идут дольше.
    t0 = time.monotonic()
    s, seen = _start(mac_recorder, monkeypatch, tmp_path, _failed_start(kind))
    tap, mic = s.tracks
    assert tap.missing and tap.missing_reason == audiotap.KIND_FAILED
    event = _missing(seen)[0]
    assert (event["reason"], event["permission"]) == ("failed", False)
    assert event["notice"] == audiotap.FAILED_NOTICE
    assert _meta(tmp_path)["system_audio"] == "missing"
    assert _meta(tmp_path)["system_audio_reason"] == "failed"
    pause = recorder.TAP_RETRY_MIN_S
    assert t0 + pause - 0.1 <= s.tap_next <= time.monotonic() + pause + 0.1
    # Рано — помощник не трогаем; потом — снова неудача, пауза удваивается.
    s.tick()
    s.tap_next = 0.0
    t1 = time.monotonic()
    s.tick()
    assert t1 + 2 * pause - 0.1 <= s.tap_next <= time.monotonic() + 2 * pause + 0.1
    assert len(_missing(seen)) == 1  # та же причина — без новой плашки
    # Помощник ожил: звук собеседников вернулся, микрофон не перезапускался.
    good, _ = _alive_popen()
    monkeypatch.setattr(mac_audio, "_popen", good)
    s.tap_next = 0.0
    s.tick()
    assert tap.stream is not None and not tap.missing
    assert [e["state"] for e in _missing(seen)] == ["missing", "restored"]
    assert _meta(tmp_path)["system_audio"] == "partial"
    assert len(FakeRawInputStream.instances) == 1 and mic.stream.is_active()
    s.close()


def test_permission_denied_waits_for_preflight_without_touching_the_helper(
        mac_recorder, monkeypatch, tmp_path):
    denied, calls = _popen_factory(b"", code=audiotap.EXIT_PERMISSION)
    s, seen = _start(mac_recorder, monkeypatch, tmp_path, denied)
    tap = s.tracks[0]
    assert tap.missing_reason == audiotap.KIND_PERMISSION
    assert _missing(seen)[0]["permission"] is True
    assert _meta(tmp_path)["system_audio_reason"] == "permission"
    asked = []
    monkeypatch.setattr(audiotap, "preflight", lambda: asked.append(1) or False)
    spawned = len(calls)
    s.tick()  # раньше PREFLIGHT_S — не спрашиваем
    assert asked == []
    s.preflight_next = 0.0
    s.tick()
    s.tick()  # следующий вопрос — через PREFLIGHT_S
    assert asked == [1]
    assert len(calls) == spawned  # помощник без разрешения не запускается
    assert s.preflight_next - time.monotonic() == pytest.approx(recorder.PREFLIGHT_S, abs=0.5)
    # Разрешение дали: помощник запускается, звук собеседников есть.
    monkeypatch.setattr(audiotap, "preflight", lambda: True)
    good, _ = _alive_popen()
    monkeypatch.setattr(mac_audio, "_popen", good)
    s.preflight_next = 0.0
    s.tick()
    assert tap.stream is not None and not tap.missing
    assert _meta(tmp_path)["system_audio"] == "partial"
    assert _meta(tmp_path)["system_audio_reason"] == "permission"
    assert len(FakeRawInputStream.instances) == 1
    s.close()


@pytest.mark.parametrize("kind", ["helper", "unsupported"])
def test_missing_helper_or_old_macos_is_not_retried(mac_recorder, monkeypatch, tmp_path, kind):
    if kind == "helper":
        monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
        popen = _popen_factory(b"")[0]
    else:
        popen, _ = _popen_factory(b"", code=audiotap.EXIT_UNSUPPORTED)
    s, seen = _start(mac_recorder, monkeypatch, tmp_path, popen)
    tap = s.tracks[0]
    assert tap.missing_reason == kind
    assert _missing(seen)[0]["permission"] is False
    assert _meta(tmp_path)["system_audio_reason"] == kind
    monkeypatch.setattr(audiotap, "preflight", lambda: pytest.fail("preflight не нужен"))
    monkeypatch.setattr(mac_audio, "_popen", lambda *a, **k: pytest.fail("помощник не нужен"))
    s.tap_next = s.preflight_next = 0.0
    s.tick()
    s.close()


def _wait_dead(track, timeout=2.0):
    deadline = time.monotonic() + timeout
    while track.stream is not None and track.stream._reader.is_alive() \
            and time.monotonic() < deadline:
        time.sleep(0.01)


def test_crashing_helper_backs_off_and_never_restarts_the_mic(mac_recorder, monkeypatch, tmp_path):
    """Помощник падает сразу после рукопожатия: перезапуски через 2, 4, 8 с,
    микрофон и PortAudio не трогаются."""
    crash, calls = _popen_factory(HANDSHAKE + _pcm(100))
    s, _ = _start(mac_recorder, monkeypatch, tmp_path, crash)
    tap, mic = s.tracks
    p_before = s.p
    delays = []
    for _ in range(3):
        _wait_dead(tap)
        s.tick()  # помощник умер — закрыть только его, запланировать повтор
        assert tap.stream is None
        delays.append(round(s.tap_next - time.monotonic()))
        s.tap_next = 0.0
        s.tick()  # повтор: снова стартует (и снова упадёт)
        assert tap.stream is not None
    assert delays == [2, 4, 8]
    assert len(calls) == 4
    assert s.p is p_before
    assert len(FakeRawInputStream.instances) == 1 and mic.stream.is_active()
    log = (tmp_path / "record.log").read_text(encoding="utf-8")
    assert "микрофон не трогаю" in log
    s.close()


def test_mic_restart_leaves_the_helper_running(mac_recorder, monkeypatch, tmp_path):
    good, calls = _alive_popen()
    s, _ = _start(mac_recorder, monkeypatch, tmp_path, good)
    tap, mic = s.tracks
    helper_stream = tap.stream
    FakeRawInputStream.instances[0].active = False  # умер микрофон
    s.tick()
    assert tap.stream is helper_stream and helper_stream.is_active()
    assert len(calls) == 1 and not FakeHelper.instances[0].killed
    assert len(FakeRawInputStream.instances) == 2 and mic.stream.is_active()
    s.close()


def test_preflight_asks_the_helper(monkeypatch, tmp_path):
    helper = tmp_path / "meet-audiotap"
    helper.write_bytes(b"")
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(helper))
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(returncode=audiotap.EXIT_PERMISSION)

    assert audiotap.preflight(run=run) is False
    assert seen == [[str(helper), "--preflight"]]
    assert audiotap.preflight(run=lambda *a, **k: SimpleNamespace(returncode=0)) is True
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
    assert audiotap.preflight(run=run) is False


# --- медленное рукопожатие: тишина перед первыми данными ---------------------------


class _Clock:
    """Часы записи (`recorder.time.monotonic`), которые двигает тест."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _record_clock(monkeypatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(recorder, "time", SimpleNamespace(monotonic=clock, sleep=time.sleep))
    return clock


class _SlowHandshakeOut:
    """stdout помощника: рукопожатие приходит через `delay_s` по часам записи, потом PCM."""

    def __init__(self, clock: _Clock, delay_s: float, pcm: bytes) -> None:
        self._clock, self._delay = clock, delay_s
        self._body = io.BytesIO(HANDSHAKE + pcm)

    def readline(self):
        self._clock.t += self._delay
        return self._body.readline()

    def read(self, size):
        return self._body.read(size)


def _slow_popen(clock: _Clock, delay_s: float, pcm: bytes):
    def popen(argv, **kwargs):
        helper = FakeHelper(b"")
        helper.stdout = _SlowHandshakeOut(clock, delay_s, pcm)
        FakeHelper.instances.append(helper)
        return helper

    return popen


def _tap_track(tmp_path):
    return recorder._Track("sys.opus", lambda p: mac_audio.tap_device(), 0, tmp_path,
                           log=lambda m: None)


def _wait_written(track, n: int, timeout: float = 2.0) -> bytes:
    deadline = time.monotonic() + timeout
    while track.bytes_written < n and time.monotonic() < deadline:
        time.sleep(0.005)
    assert track.bytes_written >= n, "звук помощника не дошёл до дорожки"
    with track._lock:
        return bytes(track.writer.data)


def _silence_runs(data: bytes, rate: int) -> list:
    """Отрезки тишины (начало, длина) в секундах — моно int16."""
    runs, start = [], None
    samples = memoryview(data).cast("h")
    for i, v in enumerate(samples):
        if v == 0 and start is None:
            start = i
        elif v != 0 and start is not None:
            runs.append((start / rate, (i - start) / rate))
            start = None
    if start is not None:
        runs.append((start / rate, (len(samples) - start) / rate))
    return runs


@pytest.mark.parametrize("delay_s", [2.0, 0.4])
def test_slow_handshake_is_silence_before_the_first_pcm(mac_recorder, monkeypatch, tmp_path,
                                                        delay_s):
    """Помощник ответил через `delay_s` — это время тишиной ПЕРЕД его звуком, а
    не после (раньше его доливал только такт вотчдога, посреди звука; задержку
    меньше порога такта — никто)."""
    clock = _record_clock(monkeypatch)
    pcm_frames = 4800  # 0,1 с звука
    monkeypatch.setattr(mac_audio, "_popen", _slow_popen(clock, delay_s, _pcm(pcm_frames)))
    track = _tap_track(tmp_path)
    t0 = clock.t
    track.first_open(mac_audio.PyAudio(), t0)
    data = _wait_written(track, int((delay_s + 0.1) * 48000) * 2 - 2 * 48)
    runs = _silence_runs(data, 48000)
    assert runs and runs[0][0] == 0.0
    assert runs[0][1] == pytest.approx(delay_s, abs=0.06)
    assert len(runs) == 1  # дальше — только звук помощника
    clock.t = t0 + delay_s + 0.1  # звук кончился в своё время
    track.tick_pad()
    assert track.bytes_written == len(data)  # такт ничего не вставил посреди звука
    track.close_stream()


def test_reopened_helper_pause_is_silence_before_its_new_data(mac_recorder, monkeypatch, tmp_path):
    clock = _record_clock(monkeypatch)
    monkeypatch.setattr(mac_audio, "_popen", _slow_popen(clock, 0.0, _pcm(4800)))
    track = _tap_track(tmp_path)
    t0 = clock.t
    p = mac_audio.PyAudio()
    track.first_open(p, t0)
    _wait_written(track, 4800 * 2)
    track.close_stream()
    clock.t = t0 + 0.1  # первый помощник отдал 0,1 с и умер
    monkeypatch.setattr(mac_audio, "_popen", _slow_popen(clock, 1.0, _pcm(4800)))
    assert track.reopen(p)
    data = _wait_written(track, int(1.2 * 48000) * 2 - 2 * 48)
    runs = _silence_runs(data, 48000)
    assert len(runs) == 1
    start, length = runs[0]
    assert start == pytest.approx(0.1, abs=0.001)
    assert length == pytest.approx(1.0, abs=0.06)  # рукопожатие нового — до его звука
    track.close_stream()


def test_tap_stream_calls_the_hook_after_the_handshake_before_data():
    order = []
    popen, _ = _popen_factory(HANDSHAKE + _pcm(1024))
    stream = mac_audio.TapStream(48000, 1, lambda *a: (order.append("data"), (None, 0))[1], 1024,
                                 popen=popen, helper="/r/meet-audiotap")
    stream.before_data = lambda: order.append("hook")
    stream.start_stream()
    deadline = time.monotonic() + 2.0
    while "data" not in order and time.monotonic() < deadline:
        time.sleep(0.005)
    stream.close()
    assert order[:2] == ["hook", "data"]


def test_tap_stream_without_handshake_never_calls_the_hook(monkeypatch):
    monkeypatch.setattr(mac_audio, "HANDSHAKE_TIMEOUT_S", 0.2)
    called = []
    for popen in (lambda argv, **kw: _SilentHelper(),
                  _popen_factory(b"", code=audiotap.EXIT_FAILED)[0],
                  _popen_factory(b'{"meet_audiotap": 1, "rate": 16000, "channels": 1}\n')[0]):
        stream = mac_audio.TapStream(48000, 1, lambda *a: (None, 0), 1024, popen=popen,
                                     helper="/r/meet-audiotap")
        stream.before_data = lambda: called.append(1)
        with pytest.raises(audiotap.TapError):
            stream.start_stream()
    assert called == []


def test_tap_stream_survives_a_failing_hook():
    got = []
    popen, _ = _popen_factory(HANDSHAKE + _pcm(1024))
    stream = mac_audio.TapStream(48000, 1, lambda *a: (got.append(1), (None, 0))[1], 1024,
                                 popen=popen, helper="/r/meet-audiotap")
    stream.before_data = lambda: 1 / 0
    stream.start_stream()
    deadline = time.monotonic() + 2.0
    while not got and time.monotonic() < deadline:
        time.sleep(0.005)
    stream.close()
    assert got  # долив не удался — звук всё равно идёт


def test_tap_stream_reports_capture_time_from_its_frame_counter():
    """Помощник пишет без пропусков (тишину — тоже): момент захвата буфера —
    отметка старта плюс уже отданные кадры, а не время прихода."""
    infos = []
    popen, _ = _popen_factory(HANDSHAKE + _pcm(1024 * 3))
    stream = mac_audio.TapStream(48000, 1, lambda d, n, info, s: (infos.append(info), (None, 0))[1],
                                 1024, popen=popen, helper="/r/meet-audiotap")
    before = time.monotonic()
    stream.start_stream()
    after = time.monotonic()
    deadline = time.monotonic() + 2.0
    while len(infos) < 3 and time.monotonic() < deadline:
        time.sleep(0.005)
    stream.close()
    adc = [i["input_buffer_adc_time"] for i in infos[:3]]
    assert before <= adc[0] <= after
    assert adc[1] - adc[0] == pytest.approx(1024 / 48000)
    assert adc[2] - adc[1] == pytest.approx(1024 / 48000)
    assert all(i["current_time"] >= before for i in infos[:3])


# --- где искать помощника: внутри Meet.app ---------------------------------------


def _bundle(apps: Path, name: str = audiotap.HELPER) -> Path:
    """Файл в ресурсах Meet.app внутри папки программ `apps`."""
    res = apps / "Meet.app" / "Contents" / "Resources" / "resources"
    res.mkdir(parents=True, exist_ok=True)
    (res / name).write_bytes(b"")
    return res / name


@pytest.fixture
def no_helper(monkeypatch, tmp_path):
    """macOS из Терминала: ни MEET_AUDIOTAP, ни помощника в PATH."""
    from meet import plat

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv(audiotap.ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(audiotap.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(plat, "SYSTEM_APPLICATIONS", tmp_path / "Applications")
    return tmp_path


def test_helper_found_inside_user_applications(no_helper):
    helper = _bundle(no_helper / "home" / "Applications")
    assert audiotap.helper_path() == str(helper)


def test_helper_in_system_applications_wins(no_helper):
    system = _bundle(no_helper / "Applications")
    _bundle(no_helper / "home" / "Applications")
    assert audiotap.helper_path() == str(system)


def test_no_helper_anywhere_keeps_the_old_answer(no_helper):
    assert audiotap.helper_path() is None
    assert audiotap.MISSING_NOTICE.startswith("Не найден помощник записи системного звука")


def test_override_and_path_come_before_the_bundle(no_helper, monkeypatch):
    _bundle(no_helper / "Applications")
    on_path = str(no_helper / "bin" / audiotap.HELPER)
    monkeypatch.setattr(audiotap.shutil, "which", lambda name, *a, **k: on_path)
    assert audiotap.helper_path() == on_path
    own = no_helper / "own-helper"
    own.write_bytes(b"")
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(own))
    assert audiotap.helper_path() == str(own)


def test_bundle_is_not_searched_on_windows(no_helper, monkeypatch):
    _bundle(no_helper / "Applications")
    monkeypatch.setattr(sys, "platform", "win32")
    assert audiotap.helper_path() is None


# --- устройства ввода: `--watch-devices`, Bluetooth, микрофон без вызовов ------------


BUILTIN = {"uid": "BuiltInMicrophoneDevice", "name": "Микрофон MacBook Pro",
           "transport": "builtin", "alive": True}
AIRPODS = {"uid": "70-3C-AA:input", "name": "AirPods Анны", "transport": "bluetooth", "alive": True}
USB_MIC = {"uid": "usb-mic", "name": "Микрофон USB", "transport": "usb", "alive": True}
# Устройства sounddevice с наушниками и USB-микрофоном (индексы 3 и 4).
MORE_DEVICES = DEVICES + [
    {"index": 3, "name": "AirPods Анны", "max_input_channels": 1,
     "max_output_channels": 2, "default_samplerate": 24000.0, "hostapi": 0},
    {"index": 4, "name": "Микрофон USB", "max_input_channels": 1,
     "max_output_channels": 0, "default_samplerate": 48000.0, "hostapi": 0},
]


def _watch_line(default, *others, event="default") -> dict:
    inputs = [default, *others] if default else list(others)
    return {"meet_audiotap": 1, "watch": event, "default_input": default, "inputs": inputs}


class _LineFeed:
    """stdout помощника `--watch-devices`: строки по одной, пока его не закроют."""

    def __init__(self, ended: bool = False) -> None:
        self.lines: "queue.Queue[bytes]" = queue.Queue()
        if ended:
            self.end()

    def push(self, obj) -> None:
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        self.lines.put((text + "\n").encode("utf-8"))

    def end(self) -> None:
        self.lines.put(b"")

    def readline(self) -> bytes:
        try:
            return self.lines.get(timeout=5.0)
        except queue.Empty:
            return b""


class _WatchProc:
    instances: list = []

    def __init__(self, feed: _LineFeed) -> None:
        self.stdout = feed
        self.stdin = io.BytesIO()
        self.killed = False
        _WatchProc.instances.append(self)

    def poll(self):
        return 0 if (self.stdin.closed or self.killed) else None

    def wait(self, timeout=None):
        self.stdout.end()
        return 0

    def kill(self):
        self.killed = True
        self.stdout.end()


def _watch(monkeypatch, *lines) -> "tuple[_LineFeed, list]":
    """Помощник `--watch-devices` с управляемым stdout; `lines` — уже напечатанное."""
    feed = _LineFeed()
    for line in lines:
        feed.push(line)
    calls = []

    def popen(argv, **kwargs):
        calls.append(argv)
        return _WatchProc(feed)

    monkeypatch.setattr(mac_audio, "_watch_popen", popen)
    return feed, calls


def _wait_for(check, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("не дождался")
        time.sleep(0.01)


def _devices(sd, default_index: int) -> None:
    sd.query_devices = lambda index=None: list(MORE_DEVICES) if index is None else MORE_DEVICES[index]
    sd.default.device = (default_index, 1)


def test_watch_devices_lines_are_parsed():
    lines = (FIXTURES / "watch_devices.jsonl").read_text(encoding="utf-8").splitlines()
    first, second = (audiotap.parse_devices(line) for line in lines)
    assert first["event"] == "start"
    assert first["default"] == BUILTIN
    assert [d["name"] for d in first["inputs"]] == ["Микрофон MacBook Pro", "BlackHole 2ch"]
    assert second["event"] == "default"
    assert second["default"]["transport"] == audiotap.TRANSPORT_BLUETOOTH
    assert second["default"]["uid"] == "70-3C-AA:input"
    # Нет устройства ввода по умолчанию — None, список остаётся.
    none = audiotap.parse_devices(json.dumps(_watch_line(None, BUILTIN)))
    assert none["default"] is None and none["inputs"] == [BUILTIN]
    assert audiotap.watch_command("/r/meet-audiotap") == ["/r/meet-audiotap", "--watch-devices"]


@pytest.mark.parametrize("text", [
    "", "мусор", "[]", HANDSHAKE.decode(), '{"meet_audiotap": 2, "watch": "start", "inputs": []}',
    '{"meet_audiotap": 1, "watch": "start", "default_input": 5, "inputs": []}',
    '{"meet_audiotap": 1, "watch": "start", "default_input": null, "inputs": 3}',
])
def test_watch_line_out_of_protocol_is_skipped(text):
    assert audiotap.parse_devices(text) is None


def test_mic_users_carry_input_devices_when_the_helper_knows_them():
    users = audiotap.parse_mic_users((FIXTURES / "mic_users_devices.json").read_text(encoding="utf-8"))
    assert users[0]["input_devices"] == ["BuiltInMicrophoneDevice"]
    # Прежний помощник устройств не сообщает — ключа нет, как раньше.
    old = audiotap.parse_mic_users((FIXTURES / "mic_users.json").read_text(encoding="utf-8"))
    assert "input_devices" not in old[0]


def test_who_listens_to_the_bluetooth_mic():
    uid = AIRPODS["uid"]

    def user(pid=501, input=True, devices=None):
        found = {"pid": pid, "name": "zoom.us", "bundle": "", "input": input, "output": True}
        if devices is not None:
            found["input_devices"] = devices
        return found

    in_use = audiotap.bluetooth_mic_in_use
    assert in_use([user(devices=[uid])], uid) is True
    assert in_use([user(devices=[BUILTIN["uid"]])], uid) is False  # звонок — со встроенного
    assert in_use([user(input=False, devices=[uid])], uid) is False
    assert in_use([], uid) is False  # микрофон никто не слушает
    # Устройств процесса помощник не знает — считаем, что слушают системный.
    assert in_use([user()], uid) is True
    # Сама запись Meet — не программа звонка.
    assert in_use([user(pid=os.getpid(), devices=[uid])], uid, own_pids={os.getpid()}) is False
    # Узнать нечем (macOS 13, сбой помощника) — как раньше: не подменяем.
    assert in_use(None, uid) is True


def test_builtin_mic_instead_of_bluetooth_headphones():
    snap = audiotap.parse_devices(json.dumps(_watch_line(AIRPODS, BUILTIN)))
    nobody = lambda: []  # noqa: E731
    assert mac_audio.bluetooth_substitute(snap, "AirPods Анны", nobody) == "Микрофон MacBook Pro"
    # Звонок слушает наушники — их и пишем.
    calling = lambda: [{"pid": 501, "input": True, "input_devices": [AIRPODS["uid"]]}]  # noqa: E731
    assert mac_audio.bluetooth_substitute(snap, "AirPods Анны", calling) is None
    # Системный — не Bluetooth, или пишется не он, или встроенного нет (Mac mini) — не трогаем.
    usb = audiotap.parse_devices(json.dumps(_watch_line(USB_MIC, BUILTIN)))
    assert mac_audio.bluetooth_substitute(usb, "Микрофон USB", nobody) is None
    assert mac_audio.bluetooth_substitute(snap, "Микрофон USB", nobody) is None
    alone = audiotap.parse_devices(json.dumps(_watch_line(AIRPODS)))
    assert mac_audio.bluetooth_substitute(alone, "AirPods Анны", nobody) is None
    assert mac_audio.bluetooth_substitute(None, "AirPods Анны", nobody) is None


def test_watcher_keeps_the_last_snapshot_and_stops_with_the_recording(mac_recorder, monkeypatch):
    feed, calls = _watch(monkeypatch, _watch_line(BUILTIN, event="start"))
    watcher = mac_audio.DeviceWatcher()
    watcher.start()
    assert calls == [[str(mac_recorder.helper), "--watch-devices"]]
    assert watcher.snapshot["default"] == BUILTIN  # первая строка — ещё до записи
    feed.push("шум в stdout")
    feed.push(_watch_line(AIRPODS, BUILTIN))
    _wait_for(lambda: watcher.snapshot["default"] == AIRPODS)
    watcher.close()
    assert _WatchProc.instances[-1].stdin.closed  # помощник выходит по концу stdin


def test_watcher_without_answers_knows_nothing(mac_recorder, monkeypatch, tmp_path):
    watcher = mac_audio.DeviceWatcher()
    watcher.start()
    _wait_for(lambda: watcher.done)
    assert watcher.snapshot is None
    watcher.close()
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
    missing = mac_audio.DeviceWatcher()
    missing.start()
    assert missing.snapshot is None
    missing.close()


def test_mac_endpoints_report_the_default_input_and_its_liveness(mac_recorder, monkeypatch):
    feed, _ = _watch(monkeypatch, _watch_line(BUILTIN, event="start"))
    endpoints = recorder._MacEndpoints()
    assert endpoints.ids() == (None, BUILTIN["uid"])
    feed.push(_watch_line({**USB_MIC, "alive": False}, BUILTIN, event="alive"))
    _wait_for(lambda: endpoints.ids() == (None, None))  # устройство пропало
    feed.push(_watch_line(None, BUILTIN, event="devices"))
    _wait_for(lambda: endpoints.snapshot()["default"] is None)
    assert endpoints.ids() == (None, None)
    endpoints.close()
    quiet = recorder._MacEndpoints()  # помощник прежний: следить нечем
    assert quiet.ids() is None
    quiet.close()


def _mac_session(monkeypatch, tmp_path, **kw):
    popen, calls = _popen_factory(HANDSHAKE + _pcm(48000))
    monkeypatch.setattr(mac_audio, "_popen", popen)
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    s = recorder._Session(tmp_path, bus, mic_device=kw.pop("mic_device", None),
                          output_device=None, **kw)
    s.start()
    return s, seen, calls


def _log(tmp_path) -> str:
    return (tmp_path / "record.log").read_text(encoding="utf-8")


def test_default_input_change_from_the_helper_reopens_the_mic(mac_recorder, monkeypatch, tmp_path):
    """Помощник сообщил: сменилось устройство ввода по умолчанию — дорожка
    микрофона переоткрыта на новом, помощник системного звука не тронут."""
    feed, _ = _watch(monkeypatch, _watch_line(BUILTIN, event="start"))
    s, _, calls = _mac_session(monkeypatch, tmp_path)
    mic = s.tracks[1]
    assert mic.device_name == "Микрофон MacBook Pro"
    spawned = len(calls)
    s.tick()
    assert len(FakeRawInputStream.instances) == 1  # без смены — без перезапуска
    _devices(mac_recorder.sd, 4)
    feed.push(_watch_line(USB_MIC, BUILTIN))
    _wait_for(lambda: s.endpoints.ids() == (None, USB_MIC["uid"]))
    s.tick()
    assert mic.device_name == "Микрофон USB"
    assert FakeRawInputStream.instances[-1].device == 4
    assert len(calls) == spawned  # системный звук не перезапускался
    s.close()
    assert _WatchProc.instances[-1].stdin.closed
    assert "сменилось дефолтное аудио-устройство" in _log(tmp_path)


def test_mac_mic_without_callbacks_for_3_5_s_is_reopened(mac_recorder, monkeypatch, tmp_path):
    """PortAudio на CoreAudio оставляет поток пропавшего устройства «активным»
    без вызовов: на Mac это смерть потока — переоткрыть."""
    s, _, _ = _mac_session(monkeypatch, tmp_path)
    mic = s.tracks[1]
    first = mic.stream
    mic.last_callback = time.monotonic() - 2.5
    s.tick()
    assert mic.stream is first  # 2,5 с — ещё жив
    mic.last_callback = time.monotonic() - 3.5
    s.tick()
    assert mic.stream is not first and mic.stream is not None
    assert len(FakeRawInputStream.instances) == 2
    assert "микрофон не отдаёт звук" in _log(tmp_path)
    # Новый поток считает время без вызовов заново.
    s.tick()
    assert len(FakeRawInputStream.instances) == 2
    s.close()


def test_mac_mic_callbacks_keep_it_alive(mac_recorder, monkeypatch, tmp_path):
    s, _, _ = _mac_session(monkeypatch, tmp_path)
    mic = s.tracks[1]
    mic.last_callback = time.monotonic() - 10
    FakeRawInputStream.instances[0].feed(_pcm(1024))
    s.tick()
    assert len(FakeRawInputStream.instances) == 1
    s.close()


def _bluetooth_session(mac_recorder, monkeypatch, tmp_path, users, **kw):
    _watch(monkeypatch, _watch_line(AIRPODS, BUILTIN, event="start"))
    _devices(mac_recorder.sd, 3)
    asked = []

    def mic_users(*a, **k):
        asked.append(1)
        return users

    monkeypatch.setattr(audiotap, "mic_users", mic_users)
    s, seen, _ = _mac_session(monkeypatch, tmp_path, **kw)
    return s, seen, asked


def test_bluetooth_mic_the_call_does_not_use_records_the_builtin_one(mac_recorder, monkeypatch, tmp_path):
    """Системный ввод — AirPods, программа звонка слушает встроенный микрофон:
    запись берёт встроенный (иначе наушники ушли бы в режим гарнитуры), в журнале — строка."""
    zoom = [{"pid": 501, "name": "zoom.us", "bundle": "", "input": True, "output": True,
             "input_devices": [BUILTIN["uid"]]}]
    s, _, asked = _bluetooth_session(mac_recorder, monkeypatch, tmp_path, zoom)
    mic = s.tracks[1]
    assert mic.device_name == "Микрофон MacBook Pro"
    assert FakeRawInputStream.instances[-1].device == 0
    assert asked
    assert mic.follows_default  # смена системного по-прежнему переоткрывает дорожку
    s.close()
    assert recorder.bluetooth_text("AirPods Анны", "Микрофон MacBook Pro") in _log(tmp_path)


@pytest.mark.parametrize("users", [
    # Звонок слушает наушники — они уже в режиме гарнитуры, пишем их.
    [{"pid": 501, "name": "zoom.us", "bundle": "", "input": True, "output": True,
      "input_devices": [AIRPODS["uid"]]}],
    None,  # узнать нечем — как раньше
])
def test_bluetooth_mic_stays_when_the_call_uses_it_or_nobody_knows(mac_recorder, monkeypatch, tmp_path, users):
    s, _, _ = _bluetooth_session(mac_recorder, monkeypatch, tmp_path, users)
    assert s.tracks[1].device_name == "AirPods Анны"
    s.close()
    assert "Bluetooth" not in _log(tmp_path)


def test_bluetooth_mic_is_taken_when_the_setting_is_off(mac_recorder, monkeypatch, tmp_path):
    s, _, asked = _bluetooth_session(mac_recorder, monkeypatch, tmp_path, [], skip_bluetooth_mic=False)
    assert s.tracks[1].device_name == "AirPods Анны"
    assert not asked  # без настройки помощника не спрашиваем
    s.close()


def test_pinned_bluetooth_mic_is_respected(mac_recorder, monkeypatch, tmp_path):
    s, _, asked = _bluetooth_session(mac_recorder, monkeypatch, tmp_path, [], mic_device="AirPods Анны")
    assert s.tracks[1].device_name == "AirPods Анны"
    assert not asked
    s.close()


def test_record_takes_the_bluetooth_choice_from_settings(monkeypatch, tmp_path):
    from meet import settings

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(recorder, "_MAC", True)
    seen = []

    class Stop(Exception):
        pass

    def session(out_dir, bus, **kw):
        seen.append(kw)
        raise Stop

    monkeypatch.setattr(recorder, "_Session", session)
    with pytest.raises(Stop):
        recorder.record(str(tmp_path / "rec"), mic_device=None, output_device=None)
    assert seen[-1]["skip_bluetooth_mic"] is True  # по умолчанию — не брать
    settings.patch({"recording": {"skip_bluetooth_mic": False}})
    with pytest.raises(Stop):
        recorder.record(str(tmp_path / "rec2"), mic_device=None, output_device=None)
    assert seen[-1]["skip_bluetooth_mic"] is False


def test_mac_device_list_marks_bluetooth_and_builtin_inputs(mac_recorder, monkeypatch):
    _watch(monkeypatch, _watch_line(AIRPODS, BUILTIN, event="start"))
    _devices(mac_recorder.sd, 3)
    devices = recorder.list_devices(mac_audio.PyAudio())
    by_name = {d["name"]: d for d in devices["inputs"]}
    assert by_name["AirPods Анны"] == {"name": "AirPods Анны", "default": True, "transport": "bluetooth"}
    assert by_name["Микрофон MacBook Pro"]["transport"] == "builtin"
    assert "transport" not in by_name["Микрофон USB"]  # помощник его не назвал
    assert _WatchProc.instances[-1].stdin.closed  # спросили один раз и закрыли


# --- process tap (macOS 14.4+): системный звук без «Записи экрана» ---------------


def test_stream_command_names_the_source_only_for_process_tap():
    base = ["/r/meet-audiotap", "--stream", "--rate", "48000", "--channels", "1"]
    assert audiotap.stream_command("/r/meet-audiotap", 48000, 1, source=audiotap.SOURCE_SCREEN) == base
    assert audiotap.stream_command("/r/meet-audiotap", 48000, 1, exclude_pid=4242,
                                   source=audiotap.SOURCE_PROCESS_TAP) == [
        *base, "--exclude-pid", "4242", "--source", "process_tap"]


@pytest.mark.parametrize("version,supported", [
    ("13.6.1", False), ("14.2", False), ("14.3.1", False), ("14.4", True), ("14.4.1", True),
    ("15.0", True), ("26.1", True), ("", False), ("мусор", False), (None, False),
])
def test_process_tap_needs_macos_14_4(monkeypatch, tmp_path, version, supported):
    from meet import plat

    helper = tmp_path / "meet-audiotap"
    helper.write_bytes(b"")
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(helper))
    monkeypatch.setattr(plat, "is_macos", lambda: True)
    monkeypatch.setattr(audiotap, "macos_version", lambda: version)
    assert audiotap.process_tap_supported() is supported


def test_process_tap_is_unavailable_off_macos_or_without_the_helper(monkeypatch, tmp_path):
    from meet import plat

    monkeypatch.setattr(audiotap, "macos_version", lambda: "15.0")
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    assert audiotap.process_tap_supported() is False
    monkeypatch.setattr(plat, "is_macos", lambda: True)
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
    assert audiotap.process_tap_supported() is False


def test_tap_stream_runs_the_helper_in_its_source():
    popen, calls = _popen_factory(HANDSHAKE)
    stream = mac_audio.TapStream(48000, 1, lambda *a: (None, mac_audio.paContinue), 1024,
                                 popen=popen, helper="/r/meet-audiotap",
                                 source=audiotap.SOURCE_PROCESS_TAP)
    stream.start_stream()
    stream.close()
    assert calls[0][-2:] == ["--source", "process_tap"]


def _tap_session(mac_recorder, monkeypatch, tmp_path, version, **kw):
    from meet import plat

    monkeypatch.setattr(audiotap, "macos_version", lambda: version)
    monkeypatch.setattr(plat, "is_macos", lambda: True)
    return _mac_session(monkeypatch, tmp_path, **kw)


def test_process_tap_setting_starts_the_helper_in_that_mode(mac_recorder, monkeypatch, tmp_path):
    s, _, calls = _tap_session(mac_recorder, monkeypatch, tmp_path, "14.4",
                               mac_capture=audiotap.SOURCE_PROCESS_TAP)
    s.close()
    assert calls[0][-2:] == ["--source", "process_tap"]
    assert "process tap" in _log(tmp_path)


def test_process_tap_on_older_macos_records_with_screencapturekit(mac_recorder, monkeypatch, tmp_path):
    s, _, calls = _tap_session(mac_recorder, monkeypatch, tmp_path, "14.3",
                               mac_capture=audiotap.SOURCE_PROCESS_TAP)
    s.close()
    assert "--source" not in calls[0]
    assert "ScreenCaptureKit" in _log(tmp_path)


def test_default_capture_is_screencapturekit(mac_recorder, monkeypatch, tmp_path):
    s, _, calls = _tap_session(mac_recorder, monkeypatch, tmp_path, "15.0")
    s.close()
    assert "--source" not in calls[0]
    assert "process tap" not in _log(tmp_path)


def test_record_takes_the_capture_mode_from_settings(monkeypatch, tmp_path):
    from meet import settings

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(recorder, "_MAC", True)
    seen = []

    class Stop(Exception):
        pass

    def session(out_dir, bus, **kw):
        seen.append(kw)
        raise Stop

    monkeypatch.setattr(recorder, "_Session", session)
    with pytest.raises(Stop):
        recorder.record(str(tmp_path / "rec"), mic_device=None, output_device=None)
    assert seen[-1]["mac_capture"] == audiotap.SOURCE_SCREEN
    settings.patch({"recording": {"mac_capture": "process_tap"}})
    with pytest.raises(Stop):
        recorder.record(str(tmp_path / "rec2"), mic_device=None, output_device=None)
    assert seen[-1]["mac_capture"] == audiotap.SOURCE_PROCESS_TAP


class _ZeroHelper(_HungHelper):
    """Помощник process tap без разрешения: рукопожатие и сплошные нули, дальше ждёт."""

    def __init__(self, seconds: float) -> None:
        super().__init__()
        self.stdout = _BlockingOut(_pcm(int(48000 * seconds), value=0))
        self.stdin = _ClosingIn(self.stdout.closed)


def _zero_stream(source, seconds=6.0):
    lines = []
    got = []
    helper = _ZeroHelper(seconds)

    def callback(data, *_):
        got.append(len(data))
        return (None, mac_audio.paContinue)

    stream = mac_audio.TapStream(48000, 1, callback, 1024, popen=lambda argv, **kw: helper,
                                 helper="meet-audiotap", log=lines.append, source=source)
    stream.start_stream()
    _wait_for(lambda: sum(got) >= int(48000 * seconds) * 2 - 2048)
    return stream, helper, lines


def test_zeros_during_process_tap_recording_are_only_counted():
    """Нули неотличимы от тишины до звонка: запись не откатывается на
    ScreenCaptureKit и не перезапускает помощника — только строка-счётчик."""
    stream, helper, lines = _zero_stream(audiotap.SOURCE_PROCESS_TAP)
    assert stream.is_active()  # поток жив: отката нет
    zero_lines = [line for line in lines if "нули" in line]
    assert len(zero_lines) == 1
    assert "5 с" in zero_lines[0] and "серия 1" in zero_lines[0]
    helper.stdout.closed.set()
    stream.close()


def test_zeros_from_screencapturekit_are_not_counted():
    stream, helper, lines = _zero_stream(audiotap.SOURCE_SCREEN)
    assert not [line for line in lines if "нули" in line]
    helper.stdout.closed.set()
    stream.close()


def test_zero_runs_count_each_run_once_and_note_the_sound_back():
    runs = audiotap.ZeroRuns(rate=1000, channels=1)
    zeros, sound = b"\x00\x00" * 1000, (5).to_bytes(2, "little", signed=True) * 1000
    assert [runs.feed(zeros) for _ in range(4)] == [None] * 4
    first = runs.feed(zeros)
    assert first is not None and "5 с" in first and "серия 1" in first
    assert runs.feed(zeros) is None  # та же серия — не повторяется
    back = runs.feed(sound)
    assert back is not None and "6 с" in back
    assert runs.feed(sound) is None
    for _ in range(4):
        runs.feed(zeros)
    second = runs.feed(zeros)
    assert second is not None and "серия 2" in second
    assert runs.runs == 2
    # Короткая пауза не считается серией.
    quiet = audiotap.ZeroRuns(rate=1000, channels=1)
    assert [quiet.feed(z) for z in (zeros, zeros, sound)] == [None, None, None]
    assert quiet.runs == 0


# --- проверка разрешения при включении (`--check-tap`) ------------------------------


def _check_run(code=0, stdout="", seen=None):
    def run(argv, **kwargs):
        if seen is not None:
            seen.append((argv[1:], kwargs["timeout"]))
        return SimpleNamespace(returncode=code, stdout=stdout)

    return run


@pytest.fixture
def tap_helper(monkeypatch, tmp_path):
    helper = tmp_path / "meet-audiotap"
    helper.write_bytes(b"")
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(helper))
    monkeypatch.setenv("MEET_EXCLUDE_PID", "4242")
    monkeypatch.delenv("MEET_PROCESS_LOOPBACK", raising=False)
    return helper


def test_check_with_sound_turns_the_switch_on(tap_helper):
    seen = []
    line = '{"meet_audiotap": 1, "check": "process_tap", "frames": 240000, "peak": 1200}\n'
    got = audiotap.check_process_tap(run=_check_run(0, line, seen))
    assert got["result"] == audiotap.CHECK_SOUND and got["ok"] is True
    assert seen == [(["--check-tap", "--seconds", "5", "--exclude-pid", "4242"],
                     audiotap.CHECK_TIMEOUT_S)]
    assert audiotap.CHECK_TIMEOUT_S > audiotap.CHECK_SECONDS


def test_check_with_only_zeros_keeps_the_switch_off_with_a_hint(tap_helper):
    line = '{"meet_audiotap": 1, "check": "process_tap", "frames": 240000, "peak": 0}\n'
    got = audiotap.check_process_tap(run=_check_run(0, line))
    assert got["result"] == audiotap.CHECK_SILENCE and got["ok"] is False
    assert "Запись системного звука" in got["notice"]
    assert got["pane"] == "system_audio"


@pytest.mark.parametrize("code,stdout,result", [
    (audiotap.EXIT_PERMISSION, "", audiotap.CHECK_DENIED),
    (audiotap.EXIT_UNSUPPORTED, "", audiotap.CHECK_UNSUPPORTED),
    (audiotap.EXIT_USAGE, "", audiotap.CHECK_UNSUPPORTED),  # прежний помощник режима не знает
    (audiotap.EXIT_FAILED, "", audiotap.CHECK_FAILED),
    (0, "мусор\n", audiotap.CHECK_FAILED),
    (0, '{"meet_audiotap": 1, "check": "process_tap", "frames": 0, "peak": 0}\n', audiotap.CHECK_FAILED),
])
def test_check_without_sound_never_turns_the_switch_on(tap_helper, code, stdout, result):
    got = audiotap.check_process_tap(run=_check_run(code, stdout))
    assert got["result"] == result and got["ok"] is False
    assert got["notice"]


def test_check_denied_points_to_system_audio_recording(tap_helper):
    got = audiotap.check_process_tap(run=_check_run(audiotap.EXIT_PERMISSION))
    assert "Запись системного звука" in got["notice"] and got["pane"] == "system_audio"


def test_check_hung_or_without_helper_fails(tap_helper, monkeypatch, tmp_path):
    def hung(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    assert audiotap.check_process_tap(run=hung)["result"] == audiotap.CHECK_FAILED
    monkeypatch.setenv(audiotap.ENV_OVERRIDE, str(tmp_path / "нет"))
    got = audiotap.check_process_tap(run=lambda *a, **k: pytest.fail("помощника нет"))
    assert got["result"] == audiotap.CHECK_FAILED
