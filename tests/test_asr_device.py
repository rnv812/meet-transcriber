"""Выбор устройства распознавания: CUDA, если есть карта, иначе CPU с
квантованной и более лёгкой моделью."""

import sys
import types

import pytest

from meet import asr


@pytest.mark.parametrize("setting,cuda,expected", [
    ("cuda", False, "cuda"),   # явный выбор уважается
    ("cpu", True, "cpu"),
    ("auto", True, "cuda"),
    ("auto", False, "cpu"),
    ("мусор", False, "cpu"),   # опечатка в конфиге → как auto
])
def test_resolve_device(monkeypatch, setting, cuda, expected):
    monkeypatch.setattr(asr, "cuda_available", lambda: cuda)
    monkeypatch.setattr(asr, "cuda_runtime_ok", lambda **kw: True)
    assert asr.resolve_device(setting) == expected


def _fake_whisper(monkeypatch, created):
    class FakeModel:
        def __init__(self, name, device, compute_type, **kw):
            created.append({"name": name, "device": device,
                            "compute_type": compute_type, **kw})

        def transcribe(self, *a, **kw):
            return iter(()), None

    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(asr, "_add_nvidia_dll_dirs", lambda: None)
    monkeypatch.setattr(asr, "_apply_hf_token", lambda: None)


def test_cpu_profile_uses_int8_and_cpu_model(monkeypatch, tmp_path):
    created = []
    _fake_whisper(monkeypatch, created)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    monkeypatch.setattr(asr, "_cpu_model_setting", lambda: "tiny-cpu-model")
    asr.transcribe_wav(tmp_path / "a.wav")
    assert created[0]["device"] == "cpu"
    assert created[0]["compute_type"] == "int8"
    assert created[0]["name"] == "tiny-cpu-model"
    assert created[0]["cpu_threads"] >= 1


def test_cuda_profile_keeps_float16_and_main_model(monkeypatch, tmp_path):
    created = []
    _fake_whisper(monkeypatch, created)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cuda")
    monkeypatch.setattr(asr, "_asr_settings", lambda: ("main-model", "ru"))
    asr.transcribe_wav(tmp_path / "a.wav")
    assert created[0] == {"name": "main-model", "device": "cuda",
                          "compute_type": "float16"}


def test_explicit_model_name_wins_on_cpu(monkeypatch, tmp_path):
    created = []
    _fake_whisper(monkeypatch, created)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    asr.transcribe_wav(tmp_path / "a.wav", model_name="chosen")
    assert created[0]["name"] == "chosen"


def test_file_transcription_decodes_greedily(monkeypatch, tmp_path):
    """0.5.1: луч 1 вместо 5 — на 20–45% быстрее; по сверке с GigaAM не хуже
    (луч 5 дважды на одном куске расходится сам с собой на 10% слов: повторы
    декодирования с температурой при неуверенности)."""
    calls = []

    class FakeModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, *a, **kw):
            calls.append(kw)
            return iter(()), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(asr, "_add_nvidia_dll_dirs", lambda: None)
    monkeypatch.setattr(asr, "_apply_hf_token", lambda: None)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cuda")
    asr.transcribe_wav(tmp_path / "a.wav")
    assert calls[0]["beam_size"] == asr.BEAM_SIZE == 1
