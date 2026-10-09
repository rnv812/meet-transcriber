"""GigaAM пачками (0.5.1): куски из памяти, по длине, пачкой через forward/_decode
модели — без 265 временных WAV и 265 отдельных проходов сети. Слова — в
порядке времени; пачка не прошла — её куски по одному."""

import wave
from types import SimpleNamespace

import contextlib

import numpy as np
import pytest

from meet import gigaam_asr as g


@pytest.fixture(autouse=True)
def _numpy_tensors(monkeypatch):
    """Без torch: пачка — массивы numpy, как их видит подделка модели."""
    monkeypatch.setattr(g, "_tensors", lambda model, wav, lengths: (wav, np.array(lengths), contextlib.nullcontext()))


def _wav(path, seconds, sr=16000):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((np.ones(int(seconds * sr)) * 1000).astype(np.int16).tobytes())
    return path


class BatchModel:
    """Пакетный API GigaAM: forward(волна, длины) и _decode(…, word_timestamps)."""

    _device = "cpu"
    _dtype = None

    def __init__(self, fail_batches=False):
        self.batches = []
        self.fail_batches = fail_batches
        self.single = 0

    def forward(self, wav, lengths):
        if self.fail_batches and wav.shape[0] > 1:
            raise RuntimeError("CUDA out of memory")
        self.batches.append([int(x) for x in lengths])
        return wav, lengths

    def _decode(self, encoded, encoded_len, wav_lens, word_timestamps=False):
        assert word_timestamps
        return [(f"кусок {int(n)}", [SimpleNamespace(text=f"w{int(n)}", start=0.1, end=0.2)])
                for n in wav_lens]

    def transcribe(self, path, word_timestamps=False):
        self.single += 1
        with wave.open(str(path), "rb") as wf:
            n = wf.getnframes()
        return SimpleNamespace(text=f"кусок {n}", words=[SimpleNamespace(text=f"w{n}", start=0.1, end=0.2)])


# Куски: 0–20 с, 23–43 с, 46–52 с (группы не длиннее 22 с).
REGIONS = [(0.0, 10.0), (12.0, 20.0), (23.0, 30.0), (33.0, 43.0), (46.0, 52.0)]


def test_batches_are_sorted_by_length_and_words_stay_in_time_order(monkeypatch, tmp_path):
    monkeypatch.setattr(g, "BATCH_CHUNKS", 2)
    model = BatchModel()
    segs = g.transcribe(_wav(tmp_path / "a.wav", 53.0), regions=REGIONS, model=model)
    lengths = [n for batch in model.batches for n in batch]
    # Пачка — по возрастанию длины; кусок, оставшийся один, — обычным путём.
    assert lengths == sorted(lengths) and len(model.batches) == 1 and model.single == 1
    assert all(len(b) <= 2 for b in model.batches)
    words = [w for s in segs for w in s.words]
    assert [w.start for w in words] == sorted(w.start for w in words)


def test_batch_limit_in_seconds(monkeypatch, tmp_path):
    monkeypatch.setattr(g, "BATCH_SECONDS", 20.0)
    model = BatchModel()
    g.transcribe(_wav(tmp_path / "a.wav", 53.0), regions=REGIONS, model=model)
    assert all(sum(b) <= 20 * 16000 or len(b) == 1 for b in model.batches)


def test_failed_batch_falls_back_to_one_by_one(tmp_path):
    model = BatchModel(fail_batches=True)
    batched = g.transcribe(_wav(tmp_path / "a.wav", 53.0), regions=REGIONS, model=model)
    reference = g.transcribe(_wav(tmp_path / "b.wav", 53.0), regions=REGIONS, model=BatchModel())
    assert [w.text for s in batched for w in s.words] == [w.text for s in reference for w in s.words]


def test_model_without_batch_api_keeps_the_old_path(tmp_path):
    class OldModel:
        calls = 0

        def transcribe(self, path, word_timestamps=False):
            OldModel.calls += 1
            return SimpleNamespace(text="x", words=[SimpleNamespace(text="x", start=0.1, end=0.2)])

    g.transcribe(_wav(tmp_path / "a.wav", 53.0), regions=REGIONS, model=OldModel())
    assert OldModel.calls == 3  # три куска — по одному
