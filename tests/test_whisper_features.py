"""Спектр Whisper блоками (0.5.1): тот же результат, что у faster-whisper, без
огромного выделения памяти. Эталон — формула FeatureExtractor.__call__ 1.2
(stft целиком: отражающие поля n_fft/2, окно Ханна, rfft, complex64, последний
кадр отброшен, ограничение по максимуму всей записи)."""

import numpy as np
import pytest

from meet import whisper_features as wf


class Extractor:
    """Нужное из faster_whisper.FeatureExtractor: размеры и мел-фильтры."""

    def __init__(self, n_mels=8, n_fft=400, hop=160, sr=16000, chunk_length=30):
        rng = np.random.default_rng(1)
        self.n_fft, self.hop_length, self.sampling_rate = n_fft, hop, sr
        self.chunk_length = chunk_length
        self.n_samples = chunk_length * sr
        self.nb_max_frames = self.n_samples // hop
        self.mel_filters = rng.random((n_mels, n_fft // 2 + 1)).astype(np.float32)
        self.calls = 0

    def __call__(self, waveform, padding=160, chunk_length=None):
        self.calls += 1
        return reference(self, waveform, padding)


def reference(ex, waveform, padding=160):
    w = waveform.astype(np.float32)
    if padding:
        w = np.pad(w, (0, padding))
    window = np.hanning(ex.n_fft + 1)[:-1].astype("float32")
    x = np.pad(w, (ex.n_fft // 2, ex.n_fft // 2), mode="reflect")
    n = 1 + (len(x) - ex.n_fft) // ex.hop_length
    frames = np.lib.stride_tricks.as_strided(x, (n, ex.n_fft), (ex.hop_length * x.strides[0], x.strides[0]))
    stft = np.fft.rfft(frames * window, n=ex.n_fft, axis=-1).T.astype("complex64")
    mag = np.abs(stft[..., :-1]) ** 2
    log = np.log10(np.clip(ex.mel_filters @ mag, a_min=1e-10, a_max=None))
    log = np.maximum(log, log.max() - 8.0)
    return (log + 4.0) / 4.0


@pytest.mark.parametrize("seconds", [0.5, 7.3, 31.0])
def test_blocks_give_the_same_spectrogram(seconds):
    ex = Extractor()
    audio = np.random.default_rng(2).standard_normal(int(seconds * 16000)).astype(np.float32) * 0.1
    got = wf.log_mel(ex, audio, block_frames=97)
    want = reference(ex, audio)
    assert got.shape == want.shape and got.dtype == want.dtype
    np.testing.assert_allclose(got, want, rtol=1e-6, atol=1e-6)


def test_chunk_length_updates_the_extractor_like_faster_whisper():
    ex = Extractor()
    wf.log_mel(ex, np.zeros(16000, dtype=np.float32), chunk_length=20)
    assert ex.n_samples == 20 * 16000 and ex.nb_max_frames == 20 * 16000 // 160


def test_install_swaps_only_the_feature_extractor_and_keeps_its_attributes():
    ex = Extractor()

    class Model:
        feature_extractor = ex

    model = Model()
    assert wf.install(model) is True
    assert model.feature_extractor.hop_length == 160 and model.feature_extractor.sampling_rate == 16000
    audio = np.random.default_rng(3).standard_normal(16000 * 3).astype(np.float32)
    np.testing.assert_allclose(model.feature_extractor(audio), reference(ex, audio), rtol=1e-6, atol=1e-6)
    assert ex.calls == 0  # своя формула, не стандартный вызов
    assert wf.install(model) is True  # повторно — не оборачивает обёртку
    assert model.feature_extractor.base is ex


def test_transcription_uses_the_block_spectrogram(monkeypatch, tmp_path):
    import sys
    import types

    from meet import asr

    models = []

    class FakeModel:
        def __init__(self, *a, **kw):
            self.feature_extractor = Extractor()
            models.append(self)

        def transcribe(self, *a, **kw):
            return iter(()), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(asr, "_add_nvidia_dll_dirs", lambda: None)
    monkeypatch.setattr(asr, "_apply_hf_token", lambda: None)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cuda")
    asr.transcribe_wav(tmp_path / "a.wav")
    assert isinstance(models[0].feature_extractor, wf.BlockExtractor)


def test_unknown_extractor_is_left_alone():
    class Model:
        feature_extractor = object()

    model = Model()
    assert wf.install(model) is False and not isinstance(model.feature_extractor, wf.BlockExtractor)


def test_matches_real_faster_whisper_when_installed():
    fw = pytest.importorskip("faster_whisper.feature_extractor")
    ex = fw.FeatureExtractor(feature_size=128)
    audio = np.random.default_rng(4).standard_normal(16000 * 45).astype(np.float32) * 0.1
    np.testing.assert_allclose(wf.log_mel(ex, audio, block_frames=500), ex(audio), rtol=1e-5, atol=1e-5)
