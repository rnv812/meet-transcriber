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


# --- C1: модель из кэша без сети ---------------------------------------------


def test_local_snapshot_follows_refs_main(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert diarize._local_snapshot() is None
    snap = _snapshot(tmp_path)
    assert diarize._local_snapshot() == snap


def test_local_snapshot_without_config_is_not_a_model(monkeypatch, tmp_path):
    """Оборванная загрузка: папка снапшота есть, а config.yaml нет."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _snapshot(tmp_path, config=False)
    assert diarize._local_snapshot() is None


def test_cached_model_loads_from_disk_without_token_and_keychain(monkeypatch, tmp_path):
    """Модель в кэше — пайплайн из папки снапшота: ни запросов к Hub, ни
    чтения токена (на macOS второе чтение связки ключей — лишний вопрос)."""
    from meet import credentials

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    snap = _snapshot(tmp_path)
    _fake_torch(monkeypatch)
    calls = []
    pipe = _Pipe()
    _fake_pyannote(monkeypatch, lambda checkpoint, token: calls.append((checkpoint, token)) or pipe)
    monkeypatch.setattr(credentials, "get_hf_token", lambda: pytest.fail("токен не нужен"))
    _wire(monkeypatch, pipe)
    diar = diarize.diarize_wav(tmp_path / "x.wav")
    assert diar.skipped is None and diar.device == "cpu"
    assert calls == [(snap, None)]


def test_broken_cache_goes_online_with_the_token(monkeypatch, tmp_path, capsys):
    from meet import credentials

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    snap = _snapshot(tmp_path)
    _fake_torch(monkeypatch)
    pipe = _Pipe()
    calls = []

    def load(checkpoint, token):
        calls.append((checkpoint, token))
        if checkpoint == snap:
            raise FileNotFoundError("segmentation/pytorch_model.bin")
        return pipe

    _fake_pyannote(monkeypatch, load)
    monkeypatch.setattr(credentials, "get_hf_token", lambda: "hf_x")
    _wire(monkeypatch, pipe)
    assert diarize.diarize_wav(tmp_path / "x.wav").skipped is None
    assert calls == [(snap, None), (diarize.DIARIZATION_MODEL, "hf_x")]
    assert "FileNotFoundError" in capsys.readouterr().out


def test_broken_cache_without_token_is_no_token(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _snapshot(tmp_path)
    _fake_torch(monkeypatch)
    _fake_pyannote(monkeypatch, lambda checkpoint, token: None)
    assert diarize.diarize_wav(tmp_path / "x.wav").skipped == diarize.SKIPPED_NO_TOKEN


def test_not_cached_without_token_is_skipped_before_pyannote(monkeypatch, tmp_path):
    """Без кэша и без токена — как раньше: пропуск, pyannote не грузится."""
    _fake_pyannote(monkeypatch, lambda checkpoint, token: pytest.fail("не грузить"))
    assert diarize.diarize_wav(tmp_path / "x.wav").skipped == diarize.SKIPPED_NO_TOKEN


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


# --- C7: время стадий — в журнал резидента --------------------------------------


def test_stage_timing_line_reaches_the_log_sink(monkeypatch, tmp_path, capsys):
    from meet import credentials

    clock = [100.0]
    monkeypatch.setattr(diarize.time, "perf_counter", lambda: clock[0])
    _fake_torch(monkeypatch)
    pipe = _Pipe(clock=clock)

    def load(token):
        clock[0] += 2.0
        return pipe

    monkeypatch.setattr(credentials, "get_hf_token", lambda: "hf_x")
    monkeypatch.setattr(diarize, "_load_pipeline", load)
    _wire(monkeypatch, pipe)
    lines = []
    monkeypatch.setattr(diarize, "_log_sink", lines.append)
    diar = diarize.diarize_wav(tmp_path / "x.wav")
    t = diar.timings
    assert t["load"] == pytest.approx(3.0)  # загрузка 2 с + до первого отчёта сегментации 1 с
    assert t["segmentation"] == pytest.approx(4.0)  # 3 с сегментации + подсчёт 0,5 + 0,5 до голосов
    assert t["embeddings"] == pytest.approx(60.0)
    assert t["clustering"] == pytest.approx(0.3)
    assert t["total"] == pytest.approx(67.3)
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("время диаризации (cpu")
    for part in ("загрузка 3.0 с", "сегментация 4.0 с", "голоса 60.0 с", "кластеризация 0.3 с", "всего 67.3 с",
                 "модель 2.0 с из сети"):
        assert part in line
    assert line in capsys.readouterr().out
    line.encode("cp866")  # печатается и в консоль


def test_job_worker_forwards_diarization_lines_as_timing_logs(monkeypatch, tmp_path, capsys):
    from meet import job_worker

    monkeypatch.setattr(diarize, "_log_sink", None)

    def fake_transcribe(path, **kw):
        diarize._log("время диаризации (cpu): всего 1.0 с")
        return tmp_path / "x.md"

    monkeypatch.setitem(sys.modules, "meet.transcribe", types.SimpleNamespace(transcribe=fake_transcribe))
    assert job_worker.main(["transcribe", str(tmp_path)]) == 0
    events = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    assert {"kind": "log", "text": "время диаризации (cpu): всего 1.0 с", "source": "timing"} in events


