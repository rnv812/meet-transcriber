"""Нехватка памяти (0.5.1): диаризация не падает, а повторяет мелкими пачками,
затем на процессоре; задача, которой памяти всё же не хватило, говорит об
этом по-человечески, а не «MemoryError: »."""

import sys
import types

import pytest

from meet import memory
from meet.diarize import Diarization


class OutOfMemoryError(RuntimeError):
    """Как torch.cuda.OutOfMemoryError: класс с этим именем."""


@pytest.mark.parametrize("error, oom, gpu", [
    (MemoryError(), True, False),
    (OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB"), True, True),
    (RuntimeError("CUDA out of memory. Tried to allocate 20.00 MiB"), True, True),
    (RuntimeError("CUDA failed with error out of memory"), True, True),
    (RuntimeError("[enforce fail at alloc_cpu.cpp:117] DefaultCPUAllocator: not enough memory"), True, False),
    (RuntimeError("Could not load library cudnn_ops64_9.dll"), False, False),
    (ValueError("out of range"), False, False),
])
def test_is_oom_and_where(error, oom, gpu):
    assert memory.is_oom(error) is oom
    if oom:
        assert memory.on_gpu(error) is gpu


def test_error_text():
    ram = memory.error_text(MemoryError())
    gpu = memory.error_text(RuntimeError("CUDA out of memory"))
    assert "оперативной памяти" in ram and "MemoryError" not in ram
    assert "памяти видеокарты" in gpu
    assert memory.error_text(ValueError("плохо")) == "ValueError: плохо"


def test_release_frees_gpu_cache_and_survives_without_torch(monkeypatch):
    freed = []
    torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True,
                                                             empty_cache=lambda: freed.append(1)))
    memory.release(torch)
    assert freed == [1]
    memory.release(None)  # без torch — только сборка мусора


# --- диаризация --------------------------------------------------------------------


def _setup(monkeypatch, tmp_path, device, fail):
    """Пайплайн, который падает `fail` раз подряд; записывает пачки и устройство."""
    from meet import credentials, diarize

    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: True, empty_cache=lambda: None)
    torch.device = lambda name: types.SimpleNamespace(type=name)
    monkeypatch.setitem(sys.modules, "torch", torch)

    class Pipe:
        segmentation_batch_size = 32
        embedding_batch_size = 16

        def __init__(self):
            self.calls = []
            self.device = None

        def to(self, dev):
            self.device = dev.type

        def __call__(self, *a, **k):
            self.calls.append((self.device, self.segmentation_batch_size, self.embedding_batch_size))
            if len(self.calls) <= len(fail):
                raise fail[len(self.calls) - 1]
            return None

    pipe = Pipe()
    monkeypatch.setattr(credentials, "get_hf_token", lambda: "hf_x")
    monkeypatch.setattr(diarize, "_local_snapshot", lambda: None)
    monkeypatch.setattr(diarize, "_load_pipeline", lambda token: pipe)
    monkeypatch.setattr(diarize, "_install_fast_embeddings", lambda p: False)
    monkeypatch.setattr(diarize, "EMBEDDING_BATCH_SIZE", 16)
    monkeypatch.setattr(diarize, "pick_device", lambda torch, use_cuda: types.SimpleNamespace(type=device))
    monkeypatch.setattr(diarize, "_load_wav", lambda path: (None, 16000))
    monkeypatch.setattr(diarize, "_to_diarization", lambda result, exclusive=False: Diarization(turns=[]))
    monkeypatch.setattr(diarize, "_log", lambda text: None)
    return diarize, pipe


def test_diarization_out_of_gpu_memory_retries_with_small_batches(monkeypatch, tmp_path):
    diarize, pipe = _setup(monkeypatch, tmp_path, "cuda", [OutOfMemoryError("CUDA out of memory")])
    diar = diarize.diarize_wav(tmp_path / "x.wav")
    assert pipe.calls == [("cuda", 32, 16), ("cuda", diarize.SMALL_BATCH, diarize.SMALL_BATCH)]
    assert diar.device == "cuda"


def test_diarization_still_out_of_gpu_memory_goes_to_cpu(monkeypatch, tmp_path):
    oom = OutOfMemoryError("CUDA out of memory")
    diarize, pipe = _setup(monkeypatch, tmp_path, "cuda", [oom, oom])
    diar = diarize.diarize_wav(tmp_path / "x.wav")
    assert [c[0] for c in pipe.calls] == ["cuda", "cuda", "cpu"]
    assert diar.device == "cpu"


def test_diarization_out_of_ram_retries_once_with_small_batches(monkeypatch, tmp_path):
    diarize, pipe = _setup(monkeypatch, tmp_path, "cpu", [MemoryError(), MemoryError()])
    with pytest.raises(MemoryError):
        diarize.diarize_wav(tmp_path / "x.wav")
    assert pipe.calls == [("cpu", 32, 16), ("cpu", diarize.SMALL_BATCH, diarize.SMALL_BATCH)]


def test_diarization_other_errors_are_not_retried(monkeypatch, tmp_path):
    diarize, pipe = _setup(monkeypatch, tmp_path, "cuda", [ValueError("плохая запись")])
    with pytest.raises(ValueError):
        diarize.diarize_wav(tmp_path / "x.wav")
    assert len(pipe.calls) == 1


# --- задача расшифровки --------------------------------------------------------------


def test_transcribe_job_reports_memory_shortage_plainly(monkeypatch, tmp_path):
    import contextlib

    from meet import gpu_lock, job_worker, transcribe

    sent = []
    monkeypatch.setattr(job_worker, "_emit", sent.append)
    monkeypatch.setattr(job_worker, "_apply_hf_token", lambda: None)
    monkeypatch.setattr(job_worker, "_diarize_log_to_resident", lambda: None)
    monkeypatch.setattr(gpu_lock, "hold_gpu_lock", lambda reason: contextlib.nullcontext())

    def boom(*a, **k):
        raise MemoryError()

    monkeypatch.setattr(transcribe, "transcribe", boom)
    args = types.SimpleNamespace(kind="transcribe", path=tmp_path, speakers=None, hotwords=None,
                                 align=False, overlap=False)
    assert job_worker._dispatch(args) == 1
    errors = [e["text"] for e in sent if e.get("kind") == "error"]
    assert errors and "оперативной памяти" in errors[0]
