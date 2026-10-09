"""«Быстрее разделять на спикеров» (0.5.1, `asr.fast_diarization`, по умолчанию
выкл.): окно сегментации шагает на 2 с вместо 1 с — голоса считаются вдвое
реже. 66 минут: 33 → 17,5 с, расхождение разметки 2,6%, но двое, сказавшие
за встречу 12–14 с, растворились в других спикерах — поэтому только по выбору."""

import sys
import types

import pytest

from meet import settings
from meet.diarize import Diarization


def _setup(monkeypatch, fast_setting=False):
    from meet import credentials, diarize

    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)
    torch.device = lambda name: types.SimpleNamespace(type=name)
    monkeypatch.setitem(sys.modules, "torch", torch)

    class Pipe:
        def __init__(self):
            self._segmentation = types.SimpleNamespace(duration=10.0, step=1.0)
            self.steps = []

        def to(self, dev):
            pass

        def __call__(self, *a, **k):
            self.steps.append(self._segmentation.step)

    pipe = Pipe()
    monkeypatch.setattr(credentials, "get_hf_token", lambda: "hf_x")
    monkeypatch.setattr(diarize, "_local_snapshot", lambda: None)
    monkeypatch.setattr(diarize, "_load_pipeline", lambda token: pipe)
    monkeypatch.setattr(diarize, "_install_fast_embeddings", lambda p: False)
    monkeypatch.setattr(diarize, "pick_device", lambda torch, use_cuda: types.SimpleNamespace(type="cpu"))
    monkeypatch.setattr(diarize, "_load_wav", lambda path: (None, 16000))
    monkeypatch.setattr(diarize, "_to_diarization", lambda result, exclusive=False: Diarization(turns=[]))
    monkeypatch.setattr(diarize, "_log", lambda text: None)
    cfg = types.SimpleNamespace(asr=settings.Asr(fast_diarization=fast_setting))
    monkeypatch.setattr(settings, "load", lambda *a, **k: cfg)
    return diarize, pipe


@pytest.mark.parametrize("fast_setting, step", [(False, 1.0), (True, 2.0)])
def test_step_follows_the_setting(monkeypatch, tmp_path, fast_setting, step):
    diarize, pipe = _setup(monkeypatch, fast_setting)
    diarize.diarize_wav(tmp_path / "x.wav")
    assert pipe.steps == [step]


def test_explicit_choice_wins_over_the_setting(monkeypatch, tmp_path):
    diarize, pipe = _setup(monkeypatch, fast_setting=True)
    diarize.diarize_wav(tmp_path / "x.wav", fast=False)
    assert pipe.steps == [1.0]


def test_setting_is_off_by_default_and_round_trips():
    assert settings.Asr().fast_diarization is False
    raw = settings.Asr(fast_diarization=True).to_raw()
    assert raw["fast_diarization"] is True
    assert settings.Asr.from_raw(raw).fast_diarization is True
    assert settings.Asr.from_raw({}).fast_diarization is False
