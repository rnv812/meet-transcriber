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


# --- C3: голоса за один проход на окно -----------------------------------------


class _StockPipe:
    def __init__(self):
        self.calls = 0

    def get_embeddings(self, file, binary_segmentations, exclude_overlap=False, hook=None):
        self.calls += 1
        return "штатно"


def test_fast_embeddings_fall_back_to_stock_once_and_say_it(monkeypatch):
    pipe = _StockPipe()
    monkeypatch.setattr(diarize, "_fast_embeddings_ok", lambda p: True)
    tries = []

    def broken(p, file, segs, exclude_overlap, hook):
        tries.append(1)
        raise ValueError("форма масок")

    monkeypatch.setattr(diarize, "_shared_embeddings", broken)
    lines = []
    monkeypatch.setattr(diarize, "_log_sink", lines.append)
    assert diarize._install_fast_embeddings(pipe) is True
    assert pipe.get_embeddings("f", "segs") == "штатно"
    assert pipe.get_embeddings("f", "segs") == "штатно"
    assert len(tries) == 1 and pipe.calls == 2
    assert len(lines) == 1 and "ValueError" in lines[0]


def test_fast_embeddings_stay_on_when_stock_fails_too(monkeypatch):
    """Падает и штатный способ (MPS без операции) — виноват не наш проход:
    он остаётся, ошибка уходит наверх (диаризация повторит на процессоре)."""

    class Pipe:
        def get_embeddings(self, file, binary_segmentations, exclude_overlap=False, hook=None):
            raise RuntimeError("MPS")

    pipe = Pipe()
    monkeypatch.setattr(diarize, "_fast_embeddings_ok", lambda p: True)
    calls = []

    def fast(p, file, segs, exclude_overlap, hook):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("MPS")
        return "быстро"

    monkeypatch.setattr(diarize, "_shared_embeddings", fast)
    monkeypatch.setattr(diarize, "_log_sink", lambda text: pytest.fail("не о чем сообщать"))
    diarize._install_fast_embeddings(pipe)
    with pytest.raises(RuntimeError):
        pipe.get_embeddings("f", "segs")
    assert pipe.get_embeddings("f", "segs") == "быстро"


def test_fast_embeddings_not_installed_on_unknown_pipeline():
    pipe = _StockPipe()
    assert diarize._install_fast_embeddings(pipe) is False
    assert "get_embeddings" not in vars(pipe)


def test_fast_embeddings_guard_wants_pyannote_4_0_and_its_embedding(monkeypatch):
    pa = pytest.importorskip("pyannote.audio")
    from pyannote.audio.pipelines.speaker_diarization import SpeakerDiarization

    class PyannoteAudioPretrainedSpeakerEmbedding:
        pass

    pipe = SpeakerDiarization.__new__(SpeakerDiarization)
    object.__setattr__(pipe, "_embedding", PyannoteAudioPretrainedSpeakerEmbedding())
    monkeypatch.setattr(pa, "__version__", "4.0.7")
    assert diarize._fast_embeddings_ok(pipe)
    monkeypatch.setattr(pa, "__version__", "4.1.0")
    assert not diarize._fast_embeddings_ok(pipe)
    monkeypatch.setattr(pa, "__version__", "4.0.7")
    object.__setattr__(pipe, "_embedding", object())
    assert not diarize._fast_embeddings_ok(pipe)

    class Custom(SpeakerDiarization):
        def get_embeddings(self, *a, **k):
            return None

    other = Custom.__new__(Custom)
    object.__setattr__(other, "_embedding", PyannoteAudioPretrainedSpeakerEmbedding())
    assert not diarize._fast_embeddings_ok(other)


class _ToyEmbedding:
    """Детерминированная «модель голоса» с тем же контрактом, что у
    PyannoteAudioPretrainedSpeakerEmbedding: маски (B, F) или (B, S, F)."""

    sample_rate = 16000
    dimension = 3
    min_num_samples = 640

    def __call__(self, waveforms, masks=None):
        import torch

        b, _, n = waveforms.shape
        frames = masks.shape[-1]
        feats = waveforms[:, 0, : n - n % frames].reshape(b, frames, -1)
        feats = torch.stack([feats.mean(-1), feats.abs().mean(-1), (feats ** 2).mean(-1)], dim=-1)  # (B, F, 3)
        w = masks if masks.dim() == 3 else masks[:, None, :]
        out = torch.einsum("bsf,bfd->bsd", w, feats) / (w.sum(-1, keepdim=True) + 1e-8)
        out = out.numpy()
        return out if masks.dim() == 3 else out[:, 0]


def _toy_segmentations(num_chunks=23, num_frames=50, num_speakers=3, seed=0):
    from pyannote.core import SlidingWindow, SlidingWindowFeature

    rng = np.random.default_rng(seed)
    data = (rng.random((num_chunks, num_frames, num_speakers)) > 0.6).astype(np.float64)
    data[3] = 0.0  # тишина во всём окне
    data[7] = 0.0
    data[5, :, 1] = 1.0  # много нахлёста: чистых кадров не хватит
    data[5, :, 2] = 1.0
    data[9, :, :] = 0.0
    data[9, :2, 0] = 1.0  # мало речи — короче min_num_frames
    return SlidingWindowFeature(data, SlidingWindow(start=0.0, duration=1.0, step=0.5))


class _ToyAudio:
    def __init__(self, waveform):
        self.waveform = waveform

    def crop(self, file, chunk, mode="pad"):
        import torch

        start = int(round(chunk.start * 16000))
        piece = self.waveform[:, start:start + 16000]
        if piece.shape[1] < 16000:
            piece = torch.nn.functional.pad(piece, (0, 16000 - piece.shape[1]))
        return piece, 16000


@pytest.mark.parametrize("exclude_overlap", [True, False])
@pytest.mark.parametrize("batch", [1, 4, 32])
def test_shared_embeddings_equal_stock_on_synthetic_input(exclude_overlap, batch):
    """Один проход на окно со всеми масками — те же голоса, что штатные
    (chunk x speaker) проходы pyannote: на каждом слоте, где есть речь."""
    torch = pytest.importorskip("torch")
    pytest.importorskip("pyannote.audio")
    from pyannote.audio.pipelines.speaker_diarization import SpeakerDiarization

    segs = _toy_segmentations()
    waveform = torch.from_numpy(np.random.default_rng(1).standard_normal((1, 16000 * 13)).astype(np.float32))
    fake = types.SimpleNamespace(training=False, _embedding=_ToyEmbedding(), _audio=_ToyAudio(waveform),
                                 embedding_batch_size=batch)
    marks = []
    stock = SpeakerDiarization.get_embeddings(fake, {"uri": "x"}, segs, exclude_overlap=exclude_overlap)
    fast = diarize._shared_embeddings(fake, {"uri": "x"}, segs, exclude_overlap,
                                      lambda name, art=None, total=None, completed=None: marks.append(
                                          (name, total, completed)))
    assert fast.shape == stock.shape and fast.dtype == np.float32
    active = np.nan_to_num(segs.data).sum(axis=1) > 0  # (chunk, speaker)
    assert active.any() and not active.all()
    np.testing.assert_allclose(fast[active], stock[active], rtol=1e-5, atol=1e-6)
    assert np.isfinite(fast).all()
    silent = ~active.any(axis=1)
    assert silent[3] and silent[7]
    assert marks[0][2] == 0 and marks[-1][1] == marks[-1][2]  # шкала хода — от 0 до конца


# --- настоящая модель (если она в кэше) ---------------------------------------


def _bundled_sample():
    pa = pytest.importorskip("pyannote.audio")
    path = Path(pa.__file__).parent / "sample" / "sample.wav"
    if not path.is_file():
        pytest.skip("нет sample.wav у pyannote")
    return path


def test_real_model_loads_offline_and_fast_embeddings_match(monkeypatch, tmp_path):
    """Модель из кэша — без сети (conftest рвёт любое соединение наружу);
    голоса за один проход на окно дают тот же результат, что штатные."""
    torch = pytest.importorskip("torch")
    sample = _bundled_sample()
    monkeypatch.setenv("HF_HUB_CACHE", str(_REAL_CACHE))
    if diarize._local_snapshot() is None:
        pytest.skip("модели диаризации нет в кэше Hugging Face")
    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "false")
    pipe = diarize._load_local()
    assert pipe is not None
    threads = torch.get_num_threads()
    torch.set_num_threads(min(threads, 4))
    try:
        with wave.open(str(sample), "rb") as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        audio = {"waveform": torch.from_numpy(pcm[: 16000 * 15].astype(np.float32) / 32768.0)[None],
                 "sample_rate": 16000}
        stock = pipe(dict(audio))
        assert diarize._install_fast_embeddings(pipe)
        fast = pipe(dict(audio))
    finally:
        torch.set_num_threads(threads)
    turns = lambda r: [(round(s.start, 3), round(s.end, 3), label)  # noqa: E731
                       for s, _, label in r.speaker_diarization.itertracks(yield_label=True)]
    assert turns(fast) == turns(stock) and turns(stock)
    np.testing.assert_allclose(fast.speaker_embeddings, stock.speaker_embeddings, rtol=1e-4, atol=1e-5)
