"""Модели из кэша Hugging Face — без сети (0.3.3): голоса pyannote (разделение
спикера, живой режим), выравнивание wav2vec2, Whisper. Скачанная модель
грузится с диска, не скачанная — как раньше, по имени и с сетью.

Настоящих моделей здесь нет: загрузчики подменены и запоминают, как их
позвали; сеть в тестах закрыта conftest."""

import sys
import types
from pathlib import Path

import numpy as np
import pytest

from meet import models


def _snapshot(root: Path, repo: str, files=("config.yaml",), ref="abc123") -> Path:
    folder = root / ("models--" + repo.replace("/", "--"))
    snap = folder / "snapshots" / ref
    snap.mkdir(parents=True)
    (folder / "refs").mkdir()
    (folder / "refs" / "main").write_text(ref + "\n", encoding="utf-8")
    for name in files:
        (snap / name).parent.mkdir(parents=True, exist_ok=True)
        (snap / name).write_bytes(b"x")
    return snap


# --- models.local_snapshot ------------------------------------------------------


def test_local_snapshot_needs_ref_and_required_files(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert models.local_snapshot("a/b") is None
    snap = _snapshot(tmp_path, "a/b", files=("config.yaml", "embedding/pytorch_model.bin"))
    assert models.local_snapshot("a/b") == snap
    assert models.local_snapshot("a/b", required=("embedding/pytorch_model.bin",)) == snap
    assert models.local_snapshot("a/b", required=("model.bin",)) is None  # загрузка оборвалась


def test_cache_root_honours_xdg_cache_home_like_huggingface_hub(monkeypatch, tmp_path):
    for env in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert models.cache_root() == tmp_path / "huggingface" / "hub"
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    assert models.cache_root() == tmp_path / "hf" / "hub"


# --- голоса pyannote: разделение спикера и живой режим --------------------------------


def _fake_embedding(monkeypatch, fail_local=False, local_error=None):
    calls = []
    for name in ("pyannote", "pyannote.audio", "pyannote.audio.pipelines"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sv = types.ModuleType("pyannote.audio.pipelines.speaker_verification")

    def pretrained(spec, device=None, token=None):
        calls.append((dict(spec), token))
        if fail_local and spec["checkpoint"] != "pyannote/speaker-diarization-community-1":
            raise local_error or FileNotFoundError("embedding/pytorch_model.bin")
        return lambda wav: [np.ones(3, dtype=np.float32)]

    sv.PretrainedSpeakerEmbedding = pretrained
    monkeypatch.setitem(sys.modules, "pyannote.audio.pipelines.speaker_verification", sv)
    torch = types.ModuleType("torch")
    torch.device = lambda name: types.SimpleNamespace(type=name)
    torch.from_numpy = lambda a: a
    monkeypatch.setitem(sys.modules, "torch", torch)
    return calls


def _embedder_builders():
    from meet import segvoices, voice_id

    return [segvoices._build_embedder, voice_id._build_embedder]


@pytest.mark.parametrize("which", [0, 1], ids=["segvoices", "voice_id"])
def test_cached_speaker_embedding_loads_from_disk_without_token(monkeypatch, tmp_path, which):
    from meet import credentials, diarize

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    snap = _snapshot(tmp_path, diarize.DIARIZATION_MODEL, files=("config.yaml", "embedding/pytorch_model.bin"))
    calls = _fake_embedding(monkeypatch)
    monkeypatch.setattr(credentials, "get_hf_token", lambda: pytest.fail("токен не нужен"))
    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "x")
    monkeypatch.delenv("PYANNOTE_METRICS_ENABLED")
    _embedder_builders()[which]("cpu")
    assert calls == [({"checkpoint": str(snap), "subfolder": "embedding"}, None)]
    import os

    assert os.environ["PYANNOTE_METRICS_ENABLED"] == "false"  # и в живом процессе телеметрия выключена


@pytest.mark.parametrize("which", [0, 1], ids=["segvoices", "voice_id"])
def test_speaker_embedding_not_cached_goes_online_with_token(monkeypatch, tmp_path, which):
    from meet import credentials, diarize

    calls = _fake_embedding(monkeypatch)
    monkeypatch.setattr(credentials, "get_hf_token", lambda: "hf_x")
    _embedder_builders()[which]("cpu")
    assert calls == [({"checkpoint": diarize.DIARIZATION_MODEL, "subfolder": "embedding"}, "hf_x")]


def test_broken_cached_speaker_embedding_falls_back_online(monkeypatch, tmp_path):
    from meet import credentials, diarize, segvoices

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _snapshot(tmp_path, diarize.DIARIZATION_MODEL, files=("config.yaml", "embedding/pytorch_model.bin"))
    calls = _fake_embedding(monkeypatch, fail_local=True)
    monkeypatch.setattr(credentials, "get_hf_token", lambda: "hf_x")
    segvoices._build_embedder("cpu")
    assert [token for _, token in calls] == [None, "hf_x"]


def test_cuda_library_error_is_not_mistaken_for_a_broken_cache(monkeypatch, tmp_path):
    """Видеокарта без cuDNN при загрузке с диска — не «битый кэш»: ошибка
    уходит наверх как есть, и вызывающий повторяет на процессоре (а не идёт в
    сеть, где без токена или сети упал бы с другой ошибкой)."""
    from meet import credentials, diarize, segvoices

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _snapshot(tmp_path, diarize.DIARIZATION_MODEL, files=("config.yaml", "embedding/pytorch_model.bin"))
    error = RuntimeError("Could not load library cudnn_ops64_9.dll. Error code 126")
    calls = _fake_embedding(monkeypatch, fail_local=True, local_error=error)
    monkeypatch.setattr(credentials, "get_hf_token", lambda: pytest.fail("в сеть не ходить"))
    with pytest.raises(RuntimeError, match="cudnn"):
        segvoices._build_embedder("cuda")
    assert len(calls) == 1


# --- выравнивание wav2vec2 ---------------------------------------------------------


def _fake_transformers(monkeypatch, fail_local=False):
    calls = []

    class Loader:
        def __init__(self, kind):
            self.kind = kind

        def from_pretrained(self, name, **kw):
            calls.append((self.kind, name, kw))
            if fail_local and kw.get("local_files_only"):
                raise OSError("нет файла в кэше")
            model = types.SimpleNamespace()
            model.to = lambda device: model
            model.eval = lambda: model
            return model

    fake = types.ModuleType("transformers")
    fake.Wav2Vec2Processor = Loader("processor")
    fake.Wav2Vec2ForCTC = Loader("model")
    monkeypatch.setitem(sys.modules, "transformers", fake)
    return calls


def test_cached_align_model_loads_without_network(monkeypatch, tmp_path):
    from meet import align

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _snapshot(tmp_path, align.ALIGN_MODEL, files=("config.json",))
    calls = _fake_transformers(monkeypatch)
    align._load_align_model("cpu")
    assert [(kind, kw) for kind, _, kw in calls] == [("processor", {"local_files_only": True}),
                                                    ("model", {"local_files_only": True})]


def test_align_model_falls_back_online_when_cache_is_incomplete(monkeypatch, tmp_path):
    from meet import align

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _snapshot(tmp_path, align.ALIGN_MODEL, files=("config.json",))
    calls = _fake_transformers(monkeypatch, fail_local=True)
    align._load_align_model("cpu")
    assert [kw for _, _, kw in calls][-2:] == [{}, {}]


def test_align_model_not_cached_loads_as_before(monkeypatch):
    from meet import align

    calls = _fake_transformers(monkeypatch)
    align._load_align_model("cpu")
    assert [kw for _, _, kw in calls] == [{}, {}]


# --- Whisper ---------------------------------------------------------------------------


class _Whisper:
    calls: list = []
    fail_local = False

    def __init__(self, name, **kw):
        type(self).calls.append((name, kw))
        if kw.get("local_files_only") and type(self).fail_local:
            raise FileNotFoundError("model.bin")


@pytest.fixture
def whisper(monkeypatch):
    _Whisper.calls = []
    _Whisper.fail_local = False
    return _Whisper


def test_cached_whisper_loads_without_asking_the_hub(monkeypatch, tmp_path, whisper):
    from meet import asr

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _snapshot(tmp_path, "bzikst/faster-whisper-large-v3-russian", files=asr.WHISPER_FILES)
    asr._whisper_model(whisper, "bzikst/faster-whisper-large-v3-russian", "cpu", "int8")
    (name, kw), = whisper.calls
    assert kw["local_files_only"] is True and kw["device"] == "cpu" and kw["compute_type"] == "int8"


def test_whisper_size_alias_is_found_in_the_cache(monkeypatch, tmp_path, whisper):
    pytest.importorskip("faster_whisper")
    from meet import asr

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _snapshot(tmp_path, "Systran/faster-whisper-medium", files=asr.WHISPER_FILES)
    asr._whisper_model(whisper, "medium", "cpu", "int8")
    assert whisper.calls[0][1]["local_files_only"] is True


def test_whisper_not_cached_or_broken_loads_as_before(monkeypatch, tmp_path, whisper):
    from meet import asr

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    asr._whisper_model(whisper, "a/b", "cpu", "int8")
    assert "local_files_only" not in whisper.calls[-1][1]
    _snapshot(tmp_path, "a/b", files=asr.WHISPER_FILES)
    whisper.fail_local = True
    whisper.calls = []
    asr._whisper_model(whisper, "a/b", "cpu", "int8")
    assert [kw.get("local_files_only") for _, kw in whisper.calls] == [True, None]


def test_whisper_weights_without_the_rest_are_not_a_cached_model(monkeypatch, tmp_path, whisper):
    """Снапшот только с model.bin (кэш старого huggingface_hub без списка
    файлов) — по сети, как раньше: докачает словарь, а не уронит ctranslate2."""
    from meet import asr

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _snapshot(tmp_path, "a/b", files=("model.bin",))
    asr._whisper_model(whisper, "a/b", "cpu", "int8")
    assert [kw.get("local_files_only") for _, kw in whisper.calls] == [None]


def test_whisper_from_a_local_folder_is_passed_as_is(tmp_path, whisper):
    from meet import asr

    asr._whisper_model(whisper, str(tmp_path), "cpu", "int8")
    assert "local_files_only" not in whisper.calls[0][1]


def test_transcription_and_live_use_the_cache_first_loader(monkeypatch, tmp_path):
    """Обе загрузки Whisper (расшифровка и живой режим) идут через _whisper_model."""
    import inspect

    from meet import asr

    source = inspect.getsource(asr)
    assert source.count("_whisper_model(WhisperModel") >= 2
    assert "WhisperModel(model_name, device=device, compute_type=compute_type" not in source
