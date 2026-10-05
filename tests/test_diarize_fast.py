"""Ускорение диаризации (0.3.3): загрузка модели без сети, телеметрия pyannote
выключена, голоса за один проход на окно, потоки, размер пачки, время стадий.

Тесты с настоящим pyannote пропускаются там, где его нет (.venv-ci); с
настоящей моделью — где её нет в кэше Hugging Face."""

import json
import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

from meet import diarize, models
from meet.diarize import Diarization

# Настоящий кэш HF машины — до того, как conftest подменит его пустым.
_REAL_CACHE = models.cache_root()


def _snapshot(root: Path, ref: str = "abc123", config: bool = True) -> Path:
    folder = root / ("models--" + diarize.DIARIZATION_MODEL.replace("/", "--"))
    snap = folder / "snapshots" / ref
    snap.mkdir(parents=True)
    (folder / "refs").mkdir()
    (folder / "refs" / "main").write_text(ref + "\n", encoding="utf-8")
    if config:
        (snap / "config.yaml").write_text("pipeline: {}\n", encoding="utf-8")
    return snap


def _fake_pyannote(monkeypatch, from_pretrained):
    fake = types.ModuleType("pyannote.audio")

    class Pipeline:
        @staticmethod
        def from_pretrained(checkpoint, token=None, **kw):
            return from_pretrained(checkpoint, token)

    fake.Pipeline = Pipeline
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake)


def _fake_torch(monkeypatch, threads=14):
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch.device = lambda name: types.SimpleNamespace(type=name)
    state = {"threads": threads, "set": []}

    def set_num_threads(n):
        state["threads"] = n
        state["set"].append(n)

    torch.set_num_threads = set_num_threads
    torch.get_num_threads = lambda: state["threads"]
    monkeypatch.setitem(sys.modules, "torch", torch)
    return state


class _Pipe:
    """Пайплайн с `hook`, как у pyannote 3.1+: сообщает шаги по ходу."""

    embedding_batch_size = 32

    def __init__(self, clock=None, threads=None):
        self.clock = clock
        self.threads = threads
        self.seen_threads = None

    def to(self, device):
        self.device = device

    def apply(self, file, num_speakers=None, min_speakers=None, max_speakers=None, hook=None):
        if self.threads is not None:
            self.seen_threads = self.threads["threads"]
        for name, total, done, step in (("segmentation", 2, 0, 1.0), ("segmentation", 2, 2, 3.0),
                                        ("speaker_counting", None, None, 0.5),
                                        ("embeddings", 4, 0, 0.5), ("embeddings", 4, 4, 60.0),
                                        ("discrete_diarization", None, None, 0.3)):
            if self.clock is not None:
                self.clock[0] += step
            if hook is not None:
                hook(name, None, total=total, completed=done)
        return "результат"

    __call__ = apply


def _wire(monkeypatch, pipe, device="cpu"):
    monkeypatch.setattr(diarize, "pick_device", lambda torch, use_cuda: types.SimpleNamespace(type=device))
    monkeypatch.setattr(diarize, "_load_wav", lambda path: (None, 16000))
    monkeypatch.setattr(diarize, "_to_diarization", lambda result, exclusive=False: Diarization(turns=[]))


# --- C2: телеметрия pyannote ---------------------------------------------------


def test_pyannote_telemetry_is_off_before_import(monkeypatch, tmp_path):
    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "x")  # вернётся как было после теста
    monkeypatch.delenv("PYANNOTE_METRICS_ENABLED")
    diarize.diarize_wav(tmp_path / "x.wav")  # без токена и кэша — пропуск
    import os

    assert os.environ["PYANNOTE_METRICS_ENABLED"] == "false"


def test_explicit_telemetry_setting_is_kept(monkeypatch, tmp_path):
    import os

    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "true")
    diarize.diarize_wav(tmp_path / "x.wav")
    assert os.environ["PYANNOTE_METRICS_ENABLED"] == "true"


def test_job_worker_turns_telemetry_off(monkeypatch, tmp_path, capsys):
    import os

    from meet import job_worker

    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "x")  # вернётся как было после теста
    monkeypatch.delenv("PYANNOTE_METRICS_ENABLED")
    monkeypatch.setattr(job_worker, "_merge", lambda path: 0)
    assert job_worker.main(["merge", str(tmp_path)]) == 0
    assert os.environ["PYANNOTE_METRICS_ENABLED"] == "false"


