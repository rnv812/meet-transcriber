import numpy as np

from meet.voice_id import SAMPLE_RATE, VoiceMatcher


def _base():
    return {
        "Демьян": [np.array([1.0, 0.0], dtype=np.float32)],
        "Пётр": [np.array([0.0, 1.0], dtype=np.float32)],
    }


def _audio(seconds=2.0):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def _embed(a):
    return np.array([1.0, 0.05], dtype=np.float32)


def test_enabled_with_base_or_owner_sample():
    from meet.owner_voice import OwnerSample

    owner = [OwnerSample(id="a", embedding=np.array([0.0, 1.0]), source="enroll", date="d", seconds=20.0)]
    assert VoiceMatcher(base=_base(), embed_fn=_embed).enabled
    assert VoiceMatcher(base={}, owner=owner, embed_fn=_embed).enabled
    assert not VoiceMatcher(base={}, owner=[], embed_fn=_embed).enabled
    assert not VoiceMatcher(base=_base()).enabled  # эмбеддер не загружен


def test_load_with_empty_base_and_no_owner_does_not_build_embedder(monkeypatch):
    import meet.voice_id as vid

    built = []
    monkeypatch.setattr(vid, "load_voices", lambda: {})
    monkeypatch.setattr("meet.owner_voice.load", lambda voices=None: [])
    monkeypatch.setattr(vid, "_load_embedder", lambda: built.append(1))
    lines = []
    m = VoiceMatcher(log=lines.append)
    m.load()
    assert not m.enabled and built == []
    assert any("live-имена выключены" in line for line in lines)


def test_load_builds_embedder_for_owner_sample_alone(monkeypatch):
    import meet.voice_id as vid
    from meet.owner_voice import OwnerSample

    owner = [OwnerSample(id="a", embedding=np.array([0.0, 1.0]), source="enroll", date="d", seconds=20.0)]
    monkeypatch.setattr(vid, "load_voices", lambda: {})
    monkeypatch.setattr("meet.owner_voice.load", lambda voices=None: owner)
    monkeypatch.setattr(vid, "_load_embedder", lambda: _embed)
    m = VoiceMatcher(log=lambda line: None)
    m.load()
    assert m.enabled and m.owner == owner


def test_threshold_and_mic_flag_come_from_settings(monkeypatch):
    import dataclasses

    from meet import settings

    cfg = settings.load()
    asr = dataclasses.replace(cfg.asr, voice_threshold=0.82, mic_speakers=False)
    monkeypatch.setattr(settings, "load", lambda: dataclasses.replace(cfg, asr=asr))
    m = VoiceMatcher(base=_base(), embed_fn=_embed, log=lambda line: None)
    m.load()
    assert m.threshold == pytest.approx(0.70)  # min(0.82 − 0.05, 0.70)
    assert m.mic is False
    asr = dataclasses.replace(asr, voice_threshold=0.70)
    m2 = VoiceMatcher(base=_base(), embed_fn=_embed, log=lambda line: None)
    m2.load()
    assert m2.threshold == pytest.approx(0.65)


def test_live_voices_uses_matcher_state():
    m = VoiceMatcher(base=_base(), embed_fn=_embed, threshold=0.6, mic=True, owner=[])
    voices = m.live_voices({"sys": "Собеседник", "mic": "Вы"})
    assert voices is not None and voices.threshold == 0.6
    assert VoiceMatcher(base=_base(), threshold=0.6, mic=True).live_voices() is None


def test_embedding_capped_at_four_torch_threads(monkeypatch):
    import types

    import meet.voice_id as vid

    state = {"n": 8, "seen": []}
    torch = types.SimpleNamespace(get_num_threads=lambda: state["n"],
                                  set_num_threads=lambda n: state.update(n=n))
    monkeypatch.setitem(sys.modules, "torch", torch)
    assert vid._capped(lambda: state["seen"].append(state["n"]) or 1) == 1
    assert state["seen"] == [4] and state["n"] == 8


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


def test_embedder_on_cuda_on_auto_even_when_text_is_recognised_on_cpu(fake_stack, monkeypatch):
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
    assert m.live_voices() is None


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
        engine.start()
    assert not matcher.enabled
