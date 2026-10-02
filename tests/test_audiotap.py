"""macOS: помощник meet-audiotap и запись через него — без Mac.

Помощник подменяется фейковым процессом (рукопожатие, PCM, коды выхода),
sounddevice — фейковым модулем. Проверяется то, что ломается без Mac
незаметно: протокол, отказ в разрешении «Запись экрана» с понятным текстом,
выбор устройств и то, что дорожки записи получают звук."""

import io
import json
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


class _HungHelper(FakeHelper):
    def __init__(self) -> None:
        super().__init__(b"")
        self.stdout = _BlockingOut(_pcm(1024))

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
