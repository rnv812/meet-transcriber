"""Выбор движка распознавания (Whisper или GigaAM): настройки, миграция
существующих конфигов, язык записи, отсутствие пакета."""

import json
import sys
import types

import pytest

from meet import asr, gigaam_asr, settings
from meet.asr import CPU_MODEL_NAME


def _config(tmp_path, monkeypatch, asr_section=None, extra=None):
    """Конфиг приложения в своей папке данных."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    raw = {"version": settings.SCHEMA_VERSION, **(extra or {})}
    if asr_section is not None:
        raw["asr"] = asr_section
    (tmp_path / "config.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


# --- настройки и миграция ---------------------------------------------------


def test_defaults_gigaam_on_cpu_whisper_on_gpu(tmp_path):
    cfg = settings.load(tmp_path / "нет.json").asr
    assert cfg.backend == "faster-whisper"
    assert cfg.cpu_backend == "gigaam"
    assert cfg.gigaam_model == "v3_e2e_rnnt"
    assert cfg.align_after_gigaam is False
    assert cfg.backend_for("cpu") == "gigaam" and cfg.backend_for("cuda") == "faster-whisper"


@pytest.mark.parametrize("cpu_model,expected", [
    (None, "gigaam"),                       # модель не выбирали — дефолт
    (CPU_MODEL_NAME, "gigaam"),             # записан поставляемый дефолт
    ("Systran/faster-whisper-small", "faster-whisper"),  # свой выбор не трогаем
    ("deepdml/faster-whisper-large-v3-turbo-ct2", "faster-whisper"),
])
def test_existing_config_switches_only_from_the_shipped_default(tmp_path, cpu_model, expected):
    section = {"backend": "faster-whisper", "model": "bzikst/faster-whisper-large-v3-russian"}
    if cpu_model is not None:
        section["cpu_model"] = cpu_model
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"version": settings.SCHEMA_VERSION, "asr": section}), encoding="utf-8")
    assert settings.load(f).asr.cpu_backend == expected


def test_explicit_cpu_backend_wins_over_migration(tmp_path):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"asr": {"cpu_model": CPU_MODEL_NAME, "cpu_backend": "faster-whisper"}}),
                 encoding="utf-8")
    assert settings.load(f).asr.cpu_backend == "faster-whisper"


def test_migration_result_is_persisted_by_the_next_save(tmp_path):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"asr": {"cpu_model": "Systran/faster-whisper-small"}}), encoding="utf-8")
    settings.patch({"asr": {"language": "ru"}}, f)
    raw = json.loads(f.read_text(encoding="utf-8"))["asr"]
    assert raw["cpu_backend"] == "faster-whisper"
    # и при следующей смене модели выбор движка уже не пересчитывается
    settings.patch({"asr": {"cpu_model": CPU_MODEL_NAME}}, f)
    assert settings.load(f).asr.cpu_backend == "faster-whisper"


@pytest.mark.parametrize("value,expected", [
    ("whisper", "faster-whisper"), ("gigaam", "gigaam"), ("whisper.cpp", "whisper.cpp"),
    ("опечатка", "faster-whisper"), (None, "faster-whisper"),
])
def test_gpu_backend_values(tmp_path, value, expected):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"asr": {"backend": value}}), encoding="utf-8")
    assert settings.load(f).asr.backend == expected


def test_gigaam_model_and_align_switch_roundtrip(tmp_path):
    f = tmp_path / "config.json"
    settings.patch({"asr": {"gigaam_model": "v3_e2e_ctc", "align_after_gigaam": True,
                            "backend": "gigaam"}}, f)
    cfg = settings.load(f).asr
    assert (cfg.gigaam_model, cfg.align_after_gigaam, cfg.backend) == ("v3_e2e_ctc", True, "gigaam")
    settings.patch({"asr": {"gigaam_model": "v9_нет"}}, f)
    assert settings.load(f).asr.gigaam_model == "v3_e2e_rnnt"


# --- выбор движка для встречи ----------------------------------------------


@pytest.fixture
def gigaam_present(monkeypatch):
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)


def _device(monkeypatch, device):
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: device)


def test_cpu_profile_chooses_gigaam(tmp_path, monkeypatch, gigaam_present):
    _config(tmp_path, monkeypatch)
    _device(monkeypatch, "cpu")
    choice = asr.choose()
    assert (choice.backend, choice.device, choice.gigaam_model, choice.note) == (
        "gigaam", "cpu", "v3_e2e_rnnt", None)


def test_gpu_profile_keeps_whisper_unless_switched(tmp_path, monkeypatch, gigaam_present):
    _config(tmp_path, monkeypatch)
    _device(monkeypatch, "cuda")
    assert asr.choose().backend == "faster-whisper"
    _config(tmp_path, monkeypatch, {"backend": "gigaam", "gigaam_model": "v3_e2e_ctc"})
    choice = asr.choose()
    assert (choice.backend, choice.device, choice.gigaam_model) == ("gigaam", "cuda", "v3_e2e_ctc")


def test_cpu_whisper_choice_is_respected(tmp_path, monkeypatch, gigaam_present):
    _config(tmp_path, monkeypatch, {"cpu_backend": "faster-whisper"})
    _device(monkeypatch, "cpu")
    assert asr.choose().backend == "faster-whisper"


def test_non_russian_language_falls_back_to_whisper(tmp_path, monkeypatch, gigaam_present, capsys):
    _config(tmp_path, monkeypatch, {"language": "en"})
    _device(monkeypatch, "cpu")
    choice = asr.choose()
    assert (choice.backend, choice.note) == ("faster-whisper", asr.NOT_RUSSIAN)
    assert "только русский" in capsys.readouterr().out


def test_auto_language_detects_on_the_recording(tmp_path, monkeypatch, gigaam_present):
    _config(tmp_path, monkeypatch, {"language": "auto"})
    _device(monkeypatch, "cpu")
    wav = tmp_path / "a.wav"
    assert asr.choose(wav, detect=lambda p: "ru").backend == "gigaam"
    choice = asr.choose(wav, detect=lambda p: "en")
    assert (choice.backend, choice.note) == ("faster-whisper", asr.NOT_RUSSIAN)
    # детектор не смог — считаем русским (язык по умолчанию)
    assert asr.choose(wav, detect=lambda p: None).backend == "gigaam"


def test_missing_gigaam_package_falls_back_quietly(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch)
    _device(monkeypatch, "cpu")
    monkeypatch.setattr(gigaam_asr, "installed", lambda: False)
    choice = asr.choose()
    assert (choice.backend, choice.note) == ("faster-whisper", None)
    assert "GigaAM не установлен" in capsys.readouterr().out


def test_transcribe_wav_routes_gigaam_choice(tmp_path, monkeypatch):
    seen = {}

    def fake(path, model_name, device):
        seen.update(path=path, model=model_name, device=device)
        return ["сегменты"]

    monkeypatch.setattr(gigaam_asr, "transcribe", fake)
    monkeypatch.setattr(asr, "_add_nvidia_dll_dirs", lambda: None)
    out = asr.transcribe_wav(tmp_path / "a.wav", "API, Jira",
                             choice=asr.Choice("gigaam", "cuda", "v3_e2e_ctc"))
    assert out == ["сегменты"]
    assert seen == {"path": tmp_path / "a.wav", "model": "v3_e2e_ctc", "device": "cuda"}


def test_whisper_auto_language_means_detect(tmp_path, monkeypatch):
    calls = {}

    class FakeModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, *a, **kw):
            calls.update(kw)
            return iter(()), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(asr, "_add_nvidia_dll_dirs", lambda: None)
    monkeypatch.setattr(asr, "_apply_hf_token", lambda: None)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    asr.transcribe_wav(tmp_path / "a.wav", language="auto")
    assert calls["language"] is None
