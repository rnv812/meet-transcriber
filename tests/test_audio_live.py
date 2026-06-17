import numpy as np
import pytest

from meet.audio import (
    apply_gain,
    compute_gain,
    pcm16_to_float32_mono,
    resample_to_16k,
)


def test_pcm16_to_float32_mono_averages_channels():
    data = np.array([16384, 16384, -32768, 0], dtype=np.int16).tobytes()
    mono = pcm16_to_float32_mono(data, channels=2)
    assert mono.dtype == np.float32
    assert mono.shape == (2,)
    np.testing.assert_allclose(mono, [0.5, -0.5], atol=1e-4)


def test_pcm16_to_float32_mono_single_channel():
    data = np.array([16384, -16384], dtype=np.int16).tobytes()
    mono = pcm16_to_float32_mono(data, channels=1)
    np.testing.assert_allclose(mono, [0.5, -0.5], atol=1e-4)


def test_resample_to_16k_changes_length_keeps_dtype():
    sec = np.sin(2 * np.pi * 440 * np.arange(48000) / 48000).astype(np.float32)
    out = resample_to_16k(sec, src_rate=48000)
    assert out.dtype == np.float32
    assert len(out) == 16000


def test_resample_to_16k_passthrough_when_already_16k():
    audio = np.zeros(1600, dtype=np.float32)
    out = resample_to_16k(audio, src_rate=16000)
    assert len(out) == 1600


def test_compute_gain_lifts_rms_to_target():
    quiet = np.full(1000, 0.05, dtype=np.float32)
    assert compute_gain(quiet, target_rms=0.1) == pytest.approx(2.0, rel=1e-3)


def test_compute_gain_silence_is_unity():
    assert compute_gain(np.zeros(10, dtype=np.float32)) == 1.0


def test_apply_gain_clips_to_unit_range():
    loud = np.full(5, 0.8, dtype=np.float32)
    out = apply_gain(loud, 2.0)
    assert out.max() <= 1.0 and out.min() >= -1.0
    assert out.dtype == np.float32
