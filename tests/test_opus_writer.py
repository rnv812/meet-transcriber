import shutil
import wave

import pytest

from meet.audio import to_wav16k
from meet.recorder import OpusWriter

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg не установлен"
)


def test_opus_writer_output_decodes_to_16k_mono(tmp_path):
    path = tmp_path / "sys.opus"
    w = OpusWriter(path, channels=2, rate=48000)
    w.write(b"\x00\x00" * 2 * 48000)  # 1 с стерео 48 кГц int16 (тишина)
    w.close()
    assert path.exists() and path.stat().st_size > 0

    # Тот же путь, что и у транскрибатора: .opus → 16 кГц моно wav.
    wav = to_wav16k(path, tmp_path / "out.wav")
    with wave.open(str(wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16000
        assert 15000 <= wf.getnframes() <= 17000  # ~1 с, допуск на паддинг Opus
