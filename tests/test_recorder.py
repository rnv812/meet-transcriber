import struct
import wave

from meet.recorder import WavWriter


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
