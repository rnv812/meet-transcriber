import numpy as np

from meet.voice_id import SAMPLE_RATE, VoiceMatcher


def _base():
    return {
        "Демьян": [np.array([1.0, 0.0], dtype=np.float32)],
        "Пётр": [np.array([0.0, 1.0], dtype=np.float32)],
    }


def _audio(seconds=2.0):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def test_matches_confident_embedding():
    m = VoiceMatcher(base=_base(), embed_fn=lambda a: np.array([1.0, 0.05], dtype=np.float32))
    assert m.name_for(_audio()) == "Демьян"


def test_margin_too_small_returns_none():
    # Два человека с почти одинаковыми образцами: у обоих cos ~1.0 к запросу,
    # так что лучший проходит порог, но отрыв от второго меньше MARGIN -> None.
    base = {
        "Демьян": [np.array([1.0, 0.0], dtype=np.float32)],
        "Пётр": [np.array([1.0, 0.02], dtype=np.float32)],
    }
    m = VoiceMatcher(base=base, embed_fn=lambda a: np.array([1.0, 0.0], dtype=np.float32))
    assert m.name_for(_audio()) is None


def test_below_threshold_returns_none():
    m = VoiceMatcher(base=_base(), embed_fn=lambda a: np.array([1.0, 0.9], dtype=np.float32))
    assert m.name_for(_audio()) is None


def test_short_segment_skipped_without_embedding():
    calls = []

    def embed(a):
        calls.append(a)
        return np.array([1.0, 0.0], dtype=np.float32)

    m = VoiceMatcher(base=_base(), embed_fn=embed)
    assert m.name_for(_audio(seconds=0.5)) is None
    assert calls == []


def test_nan_embedding_returns_none():
    m = VoiceMatcher(base=_base(), embed_fn=lambda a: np.array([np.nan, 0.0], dtype=np.float32))
    assert m.name_for(_audio()) is None


def test_empty_base_disabled():
    m = VoiceMatcher(base={}, embed_fn=lambda a: np.array([1.0, 0.0], dtype=np.float32))
    assert not m.enabled
    assert m.name_for(_audio()) is None


def test_load_with_empty_base_does_not_build_embedder(monkeypatch, capsys):
    import meet.voice_id as vid

    monkeypatch.setattr(vid, "load_voices", lambda: {})
    m = VoiceMatcher()
    m.load()
    assert not m.enabled
    assert "live-имена выключены" in capsys.readouterr().out


def test_match_announced_once(capsys):
    m = VoiceMatcher(base=_base(), embed_fn=lambda a: np.array([1.0, 0.0], dtype=np.float32))
    m.name_for(_audio())
    m.name_for(_audio())
    out = capsys.readouterr().out
    assert out.count("Демьян") == 1


# --- выбор устройства и устойчивость загрузки эмбеддера -------------------------

import sys
import types

import pytest

from meet import asr, voice_id


@pytest.fixture
def fake_stack(monkeypatch):
    """Поддельные torch и pyannote: запоминают, на каком устройстве просили модель."""
    asr._reset_cuda_state()
    state = {"devices": [], "cuda_ok": True, "fail": {}, "call_fail": {}, "calls": []}

    torch = types.ModuleType("torch")
    torch.device = lambda name: name
    torch.cuda = types.SimpleNamespace(is_available=lambda: state["cuda_ok"])
    torch.from_numpy = lambda a: types.SimpleNamespace(float=lambda: a.astype(np.float32))
    monkeypatch.setitem(sys.modules, "torch", torch)

    def pretrained(spec, device, token=None):
        state["devices"].append(device)
        if device in state["fail"]:
            raise state["fail"][device]

        def model(wav):
            state["calls"].append(device)
            errors = state["call_fail"].get(device)
            if errors:
                raise errors.pop(0)
            return [np.zeros(3, dtype=np.float32)]

        return model

    for name in ("pyannote", "pyannote.audio", "pyannote.audio.pipelines"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sv = types.ModuleType("pyannote.audio.pipelines.speaker_verification")
    sv.PretrainedSpeakerEmbedding = pretrained
    monkeypatch.setitem(sys.modules, "pyannote.audio.pipelines.speaker_verification", sv)
    monkeypatch.setattr("meet.credentials.get_hf_token", lambda: "t")
    yield state
    asr._reset_cuda_state()


def test_cpu_profile_loads_embedder_on_cpu(fake_stack, monkeypatch):
    monkeypatch.setattr(asr, "engine_profile", lambda prefix=None: "cpu")
    voice_id._load_embedder()
    assert fake_stack["devices"] == ["cpu"]


def test_usable_cuda_loads_embedder_on_cuda(fake_stack, monkeypatch):
    voice_id._load_embedder()
    assert fake_stack["devices"] == ["cuda"]


def test_embedder_on_cuda_even_when_text_is_recognised_on_cpu(fake_stack, monkeypatch):
    """Текст — на процессоре (GigaAM, выбрано или нет библиотек ctranslate2),
    а torch видит карту: голоса — на видеокарте."""
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    voice_id._load_embedder()
    assert fake_stack["devices"] == ["cuda"]


def test_cuda_without_torch_support_falls_to_cpu(fake_stack, monkeypatch):
    fake_stack["cuda_ok"] = False
    voice_id._load_embedder()
    assert fake_stack["devices"] == ["cpu"]


def test_missing_cuda_library_retries_on_cpu(fake_stack, monkeypatch):
    fake_stack["fail"]["cuda"] = RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
    voice_id._load_embedder()
    assert fake_stack["devices"] == ["cuda", "cpu"]
    assert asr._torch_failure is not None  # сбой запомнен на процесс
    assert asr._cuda_failure is None  # распознаванию (ctranslate2) он не помеха


CUDNN_MISSING = RuntimeError("Could not load library cudnn_ops64_9.dll. Error code 126")


def test_cuda_library_failing_on_first_embedding_falls_to_cpu_at_load(fake_stack, monkeypatch):
    """cuDNN грузится лениво: модель на видеокарте собралась, а первая свёртка
    падает — пробный эмбеддинг при загрузке это ловит."""
    fake_stack["call_fail"]["cuda"] = [CUDNN_MISSING]
    embed = voice_id._load_embedder()
    assert fake_stack["devices"] == ["cuda", "cpu"]
    assert embed(_audio()) is not None and fake_stack["calls"][-1] == "cpu"


def test_cuda_library_failing_later_moves_matcher_to_cpu_once(fake_stack, monkeypatch):
    embed = voice_id._load_embedder()  # пробный эмбеддинг прошёл
    fake_stack["call_fail"]["cuda"] = [CUDNN_MISSING]
    assert embed(_audio()) is not None
    assert embed(_audio()) is not None
    assert fake_stack["devices"] == ["cuda", "cpu"]  # на процессор — один раз
    assert fake_stack["calls"] == ["cuda", "cuda", "cpu", "cpu"]


def test_other_embedding_error_is_not_swallowed(fake_stack, monkeypatch):
    embed = voice_id._load_embedder()
    fake_stack["call_fail"]["cuda"] = [ValueError("bad shape")]
    with pytest.raises(ValueError):
        embed(_audio())
    assert fake_stack["devices"] == ["cuda"]


def test_other_load_error_disables_matcher_without_raising(fake_stack, monkeypatch, capsys):
    fake_stack["cuda_ok"] = False
    fake_stack["fail"]["cpu"] = OSError("model not cached")
    m = VoiceMatcher(base=_base())
    m.load()
    assert not m.enabled
    out = capsys.readouterr().out
    assert "опознание в живом режиме недоступно" in out and "model not cached" in out
    assert "имена появятся после расшифровки" in out
    assert m.name_for(_audio()) is None


def test_live_engine_starts_with_failing_embedder(tmp_path, monkeypatch):
    from meet.live import LiveEngine

    monkeypatch.setattr(voice_id, "_load_embedder", lambda: (_ for _ in ()).throw(OSError("no token")))
    matcher = VoiceMatcher(base=_base())

    class Stop(Exception):
        pass

    engine = LiveEngine(tmp_path, types.SimpleNamespace(load=lambda: None), voice_matcher=matcher)

    def connect():
        raise Stop

    monkeypatch.setattr(engine, "_tap_connect", connect)
    with pytest.raises(Stop):  # дошли до подключения — загрузка матчера не упала
        engine._start_from_tap()
    assert not matcher.enabled
