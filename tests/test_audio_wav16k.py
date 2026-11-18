"""Перевод дорожки в WAV 16 кГц со сдвигом (`audio.to_wav16k(offset_s=…)`).

Сдвиг микрофона старой записи применяется к временной копии: задержка —
тишина в начале, отрицательный сдвиг — обрезка начала. Исходный файл не
меняется. Здесь настоящий ffmpeg: проверяем сам звук, а не команду.
"""

import shutil
import wave

import numpy as np
import pytest

from meet.audio import to_wav16k

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg не установлен")

RATE = 48000


def _write(path, silence_s: float, tone_s: float):
    """WAV 48 кГц моно: `silence_s` тишины, потом `tone_s` тона 440 Гц."""
    t = np.arange(int(tone_s * RATE)) / RATE
    tone = (0.5 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    samples = np.concatenate([np.zeros(int(silence_s * RATE), dtype=np.int16), tone])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    return path


def _read(path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    return data, rate


def _first_sound_s(data: np.ndarray, rate: int) -> float:
    """Где начинается звук: первый отсчёт громче порога."""
    loud = np.flatnonzero(np.abs(data) > 0.05)
    return float(loud[0]) / rate if len(loud) else float("inf")


def test_without_offset_the_track_keeps_its_start(tmp_path):
    src = _write(tmp_path / "mic.wav", 0.0, 1.0)
    data, rate = _read(to_wav16k(src, tmp_path / "mic16.wav"))
    assert rate == 16000
    assert len(data) / rate == pytest.approx(1.0, abs=0.01)
    assert _first_sound_s(data, rate) < 0.01


def test_positive_offset_prepends_silence(tmp_path):
    src = _write(tmp_path / "mic.wav", 0.0, 1.0)
    data, rate = _read(to_wav16k(src, tmp_path / "mic16.wav", offset_s=0.4))
    assert len(data) / rate == pytest.approx(1.4, abs=0.01)
    assert _first_sound_s(data, rate) == pytest.approx(0.4, abs=0.01)


def test_negative_offset_trims_the_start(tmp_path):
    src = _write(tmp_path / "mic.wav", 0.3, 0.7)
    data, rate = _read(to_wav16k(src, tmp_path / "mic16.wav", offset_s=-0.3))
    assert len(data) / rate == pytest.approx(0.7, abs=0.01)
    assert _first_sound_s(data, rate) < 0.01


def test_offset_leaves_the_source_file_untouched(tmp_path):
    src = _write(tmp_path / "mic.wav", 0.3, 0.7)
    before = src.read_bytes()
    to_wav16k(src, tmp_path / "a16.wav", offset_s=0.4)
    to_wav16k(src, tmp_path / "b16.wav", offset_s=-0.3)
    assert src.read_bytes() == before


def test_offset_combines_with_normalization(tmp_path):
    """Нормализация и сдвиг — одна цепочка фильтров: ни один не теряется."""
    src = _write(tmp_path / "sys.wav", 0.0, 1.0)
    data, rate = _read(to_wav16k(src, tmp_path / "sys16.wav", normalize=True, offset_s=0.4))
    assert len(data) / rate == pytest.approx(1.4, abs=0.02)
    assert _first_sound_s(data, rate) == pytest.approx(0.4, abs=0.02)
