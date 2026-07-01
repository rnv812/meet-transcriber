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
