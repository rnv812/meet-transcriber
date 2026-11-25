"""Apple Silicon (MPS): GigaAM, выравнивание слов и голоса считаются на
видеоядрах, сбой операции на MPS — повтор на процессоре, кэш видеоядер
освобождается (`asr.torch_device`, `memory`). torch подменён: тест не тянет MPS."""

import sys
import types

import numpy as np
import pytest

from meet import asr, memory


def _torch(monkeypatch, mps: bool, cuda: bool = False, freed=None):
    freed = [] if freed is None else freed
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: cuda,
                                       empty_cache=lambda: freed.append("cuda"))
    torch.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps))
    torch.mps = types.SimpleNamespace(empty_cache=lambda: freed.append("mps"))
    torch.device = lambda name: types.SimpleNamespace(type=name)
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    asr._reset_cuda_state()
    monkeypatch.setattr(asr, "device_setting", lambda setting=None: "auto")
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    yield
    asr._reset_cuda_state()


def _profile(monkeypatch, name):
    monkeypatch.setattr(asr, "engine_profile", lambda prefix=None: name)


# --- устройство -----------------------------------------------------------------


def test_mac_profile_with_mps_gives_mps_to_torch_steps(monkeypatch):
    _profile(monkeypatch, "mac")
    _torch(monkeypatch, mps=True)
    assert asr.torch_device() == "mps"
    from meet import align, voice_id

    assert align._align_device() == "mps"
    assert voice_id._embedder_device() == "mps"


def test_mac_without_mps_or_after_failure_stays_on_cpu_whatever_the_setting(monkeypatch):
    _profile(monkeypatch, "mac")
    _torch(monkeypatch, mps=False)
    assert asr.torch_device() == "cpu"
    _torch(monkeypatch, mps=True)
    # 0.6.2: на Mac видеоядра всегда — «Процессор» прежних версий их не выключает.
    monkeypatch.setattr(asr, "device_setting", lambda setting=None: "cpu")
    assert asr.torch_device() == "mps"
    monkeypatch.setattr(asr, "device_setting", lambda setting=None: "auto")
    monkeypatch.setattr(asr, "_torch_failure", "сбой")
    assert asr.torch_device() == "cpu"


def test_cpu_profile_never_gets_mps(monkeypatch):
    _profile(monkeypatch, "cpu")
    _torch(monkeypatch, mps=True)
    assert asr.torch_device() == "cpu"


def test_gigaam_gets_mps_from_the_cpu_choice_on_mac(monkeypatch, tmp_path):
    from meet import gigaam_asr

    _profile(monkeypatch, "mac")
    _torch(monkeypatch, mps=True)
    seen = {}
    monkeypatch.setattr(gigaam_asr, "transcribe", lambda path, **kw: seen.update(kw) or [])
    choice = asr.Choice("gigaam", "cpu", "v3_e2e_ctc")
    asr.transcribe_wav(tmp_path / "a.wav", choice=choice)
    assert seen["device"] == "mps"
    _torch(monkeypatch, mps=False)
    asr.transcribe_wav(tmp_path / "a.wav", choice=choice)
    assert seen["device"] == "cpu"


# --- откат на процессор -----------------------------------------------------------


def test_gigaam_retries_on_cpu_when_mps_lacks_an_operation(monkeypatch, tmp_path, capsys):
    from meet import gigaam_asr

    devices = []

    def load(name, device="cpu", on_line=None):
        devices.append(device)
        if device == "mps":
            raise NotImplementedError("The operator aten::foo is not currently implemented for the MPS device")
        return object()

    monkeypatch.setattr(gigaam_asr, "load", load)
    monkeypatch.setattr(gigaam_asr, "_read_wav", lambda p: (np.zeros(16000, dtype=np.int16), 16000))
    monkeypatch.setattr(gigaam_asr, "plan_chunks", lambda regions, audio, sr: [])
    monkeypatch.setattr(gigaam_asr, "speech_regions", lambda audio, sr: [])
    assert gigaam_asr.transcribe(tmp_path / "a.wav", device="mps") == []
    assert devices == ["mps", "cpu"]
    out = capsys.readouterr().out
    assert "MPS" in out and "NotImplementedError" in out and "aten::foo" not in out


def test_gigaam_other_errors_on_mps_are_not_retried(monkeypatch, tmp_path):
    from meet import gigaam_asr

    monkeypatch.setattr(gigaam_asr, "load", lambda *a, **k: (_ for _ in ()).throw(ValueError("плохо")))
    monkeypatch.setattr(gigaam_asr, "_read_wav", lambda p: (np.zeros(16000, dtype=np.int16), 16000))
    monkeypatch.setattr(gigaam_asr, "plan_chunks", lambda regions, audio, sr: [])
    monkeypatch.setattr(gigaam_asr, "speech_regions", lambda audio, sr: [])
    with pytest.raises(ValueError):
        gigaam_asr.transcribe(tmp_path / "a.wav", device="mps")


def test_alignment_retries_on_cpu_when_mps_fails(monkeypatch, capsys):
    from meet import align

    ran = []

    def run(segments, wav_path, device, on_progress):
        ran.append(device)
        if device == "mps":
            raise RuntimeError("MPS: unsupported")
        return ["готово"]

    _torch(monkeypatch, mps=True)
    monkeypatch.setattr(align, "_align", run)
    assert align.align_segments([], "x.wav", device="mps") == ["готово"]
    assert ran == ["mps", "cpu"]
    assert "MPS" in capsys.readouterr().out
    ran.clear()
    with pytest.raises(ValueError):
        monkeypatch.setattr(align, "_align", lambda *a: (_ for _ in ()).throw(ValueError("x")))
        align.align_segments([], "x.wav", device="mps")


def test_voice_embedder_builds_on_mps_and_falls_back_to_cpu(monkeypatch):
    import meet.voice_id as vid

    built = []

    def build(device):
        built.append(device)

        def embed(audio):
            if device == "mps":
                raise NotImplementedError("mps op")
            return np.ones(3, dtype=np.float32)

        return embed

    monkeypatch.setattr(vid, "_build_embedder", build)
    monkeypatch.setattr(vid, "_embedder_device", lambda: "mps")
    embed = vid._load_embedder()  # пробный эмбеддинг сразу упал на MPS
    assert built == ["mps", "cpu"]
    assert embed(np.zeros(10, dtype=np.float32)).tolist() == [1, 1, 1]


def test_voice_embedder_moves_to_cpu_when_a_live_segment_fails_on_mps(monkeypatch):
    import meet.voice_id as vid

    state = {"calls": 0}

    def build(device):
        def embed(audio):
            state["calls"] += 1
            if device == "mps" and state["calls"] > 1:  # проба прошла, живой сегмент — нет
                raise RuntimeError("MPS")
            return np.ones(3, dtype=np.float32)

        return embed

    monkeypatch.setattr(vid, "_build_embedder", build)
    monkeypatch.setattr(vid, "_embedder_device", lambda: "mps")
    embed = vid._load_embedder()
    assert embed(np.zeros(10, dtype=np.float32)).tolist() == [1, 1, 1]
    assert embed(np.zeros(10, dtype=np.float32)).tolist() == [1, 1, 1]


# --- память -----------------------------------------------------------------------


def test_release_empties_mps_cache_on_mac_and_cuda_on_windows(monkeypatch):
    freed = []
    memory.release(_torch(monkeypatch, mps=True, freed=freed))
    assert freed == ["mps"]
    freed.clear()
    memory.release(_torch(monkeypatch, mps=False, cuda=True, freed=freed))
    assert freed == ["cuda"]
    memory.release(types.SimpleNamespace())  # без cuda и mps — без падения


def test_diarization_releases_mps_cache_after_splitting(monkeypatch, tmp_path):
    from tests.test_memory_guard import _setup

    diarize, pipe = _setup(monkeypatch, tmp_path, "mps", [])
    released = []
    monkeypatch.setattr(diarize.memory, "release", lambda torch=None: released.append(1))
    diarize.diarize_wav(tmp_path / "x.wav")
    assert released == [1]
