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
    assert asr.choose(wav, detect=lambda p: ("ru", 0.99)).backend == "gigaam"
    choice = asr.choose(wav, detect=lambda p: ("en", 0.93))
    assert (choice.backend, choice.note) == ("faster-whisper", asr.NOT_RUSSIAN)
    # детектор не смог — считаем русским (язык по умолчанию)
    assert asr.choose(wav, detect=lambda p: None).backend == "gigaam"


def test_unsure_detection_keeps_gigaam(tmp_path, monkeypatch, gigaam_present, capsys):
    """Тишина и гудки в начале звонка дают «en» с низкой уверенностью — это
    не повод уходить с GigaAM."""
    _config(tmp_path, monkeypatch, {"language": "auto"})
    _device(monkeypatch, "cpu")
    choice = asr.choose(tmp_path / "a.wav", detect=lambda p: ("en", 0.55))
    assert (choice.backend, choice.note) == ("gigaam", None)
    assert "не уверен" in capsys.readouterr().out


def _wav(path, seconds=1.0):
    import wave

    import numpy as np

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(np.zeros(int(seconds * 16000), dtype=np.int16).tobytes())
    return path


def test_detection_never_downloads_whisper(tmp_path, monkeypatch, capsys):
    """Модели Whisper на диске нет — язык не определяется (None), фабрика
    модели не вызывается, в журнале — почему."""
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    monkeypatch.setattr(asr, "local_whisper_model", lambda device: None)

    def factory(*a, **kw):
        raise AssertionError("качать модель ради определения языка нельзя")

    assert asr.detect_language(_wav(tmp_path / "a.wav"), model_factory=factory) is None
    assert "не скачана" in capsys.readouterr().out


def test_detection_uses_speech_sample_from_several_places(tmp_path, monkeypatch):
    import numpy as np

    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    monkeypatch.setattr(asr, "local_whisper_model", lambda device: "Systran/faster-whisper-medium")
    seen = {}

    class Model:
        def detect_language(self, audio, language_detection_segments=1):
            seen.update(n=len(audio), segments=language_detection_segments)
            return "en", 0.91, []

    def factory(name, device):
        seen.update(name=name, device=device)
        return Model()

    wav = _wav(tmp_path / "a.wav", seconds=300.0)
    regions = [(40.0, 100.0), (150.0, 290.0)]  # тишина в начале не берётся
    found = asr.detect_language(wav, model_factory=factory, regions=regions)
    assert found == ("en", 0.91)
    assert seen["name"] == "Systran/faster-whisper-medium" and seen["device"] == "cpu"
    assert seen["n"] == 60 * 16000 and seen["segments"] == 2


def test_speech_sample_takes_pieces_only_from_speech():
    import numpy as np

    sr = 100
    audio = np.zeros(1000 * sr, dtype=np.float32)
    audio[200 * sr:300 * sr] = 1.0  # речь 200–300 с
    audio[600 * sr:700 * sr] = 2.0  # речь 600–700 с
    sample = asr.speech_sample(audio, sr, seconds=60, pieces=4,
                               regions=[(200.0, 300.0), (600.0, 700.0)])
    assert len(sample) == 60 * sr
    assert set(np.unique(sample)) == {1.0, 2.0}  # из обоих мест, без тишины
    assert asr.speech_sample(audio, sr, regions=[]) is None


def test_local_whisper_model_on_cpu_goes_light_to_heavy_and_never_large(monkeypatch):
    from meet import models

    have = {"bzikst/faster-whisper-large-v3-russian", "Systran/faster-whisper-large-v3"}
    monkeypatch.setattr(models, "downloaded", lambda repo: repo in have)
    monkeypatch.setattr(asr, "_model_for", lambda device, name: "bzikst/faster-whisper-large-v3-russian")
    assert asr.local_whisper_model("cpu") is None  # large-v3 на процессоре — никогда
    assert asr.local_whisper_model("cuda") == "bzikst/faster-whisper-large-v3-russian"
    have.add("deepdml/faster-whisper-large-v3-turbo-ct2")
    assert asr.local_whisper_model("cpu") == "deepdml/faster-whisper-large-v3-turbo-ct2"
    have.add("Systran/faster-whisper-medium")
    assert asr.local_whisper_model("cpu") == "Systran/faster-whisper-medium"
    have.add("Systran/faster-whisper-small")
    assert asr.local_whisper_model("cpu") == "Systran/faster-whisper-small"


def test_fallback_download_model_is_never_large_on_cpu(monkeypatch):
    monkeypatch.setattr(asr, "_model_for", lambda device, name: "bzikst/faster-whisper-large-v3-russian")
    assert asr.fallback_whisper_model("cpu") == "Systran/faster-whisper-medium"
    assert asr.fallback_whisper_model("cuda") == "bzikst/faster-whisper-large-v3-russian"
    assert asr.model_size_text("Systran/faster-whisper-medium") == "около 1,5 ГБ"
    assert asr.model_size_text("неизвестная") == ""


def test_live_transcriber_maps_auto_to_detection(tmp_path, monkeypatch):
    """Живой режим с языком `auto`: Whisper получает None (определит сам),
    а не строку «auto», которую faster-whisper отвергает."""
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
    _config(tmp_path, monkeypatch, {"language": "auto"})
    t = asr.Transcriber()
    assert t.language is None
    t.load()
    t.transcribe_window([0.0])
    assert calls["language"] is None
    assert asr.Transcriber(language="ru").language == "ru"
    assert asr.whisper_language("Auto") is None and asr.whisper_language("") is None


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


def test_whisper_prompt_leaves_half_the_context_for_the_text(tmp_path, monkeypatch):
    """faster-whisper режет hotwords и предыдущий текст до 223 токенов
    КАЖДЫЙ: вместе ~390 из 448, и на 30 с речи декодеру оставалось ~60
    токенов — окно обрывалось, текст терялся, на тишине декодер срывался в
    повтор. Подсказка целиком — не больше половины контекста, как в Whisper:
    термины целиком, предыдущий текст — сколько влезет (последние токены)."""
    seen = {}

    class Tokenizer:
        sot_prev, sot_sequence, no_timestamps, timestamp_begin = -1, [-2, -3, -4], -5, -6

        def encode(self, text):  # токен на символ
            return list(range(len(text)))

    class FakeModel:  # get_prompt — как в faster-whisper 1.2
        max_length = 448

        def __init__(self, *a, **kw):
            pass

        def get_prompt(self, tokenizer, previous_tokens, without_timestamps=False, prefix=None, hotwords=None):
            prompt = []
            if previous_tokens or (hotwords and not prefix):
                prompt.append(tokenizer.sot_prev)
                if hotwords and not prefix:
                    prompt.extend(tokenizer.encode(" " + hotwords.strip())[: self.max_length // 2 - 1])
                if previous_tokens:
                    prompt.extend(previous_tokens[-(self.max_length // 2 - 1):])
            prompt.extend(tokenizer.sot_sequence)
            return prompt

        def transcribe(self, *a, **kw):
            tok, previous = Tokenizer(), list(range(1000, 1300))
            seen["both"] = self.get_prompt(tok, previous, hotwords=kw["hotwords"])
            seen["prev_only"] = self.get_prompt(tok, previous, hotwords=None)
            seen["huge_hot"] = self.get_prompt(tok, previous, hotwords="x" * 500)
            return iter(()), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(asr, "_add_nvidia_dll_dirs", lambda: None)
    monkeypatch.setattr(asr, "_apply_hf_token", lambda: None)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    asr.transcribe_wav(tmp_path / "a.wav", "Jira, " * 30, language="ru")
    half = FakeModel.max_length // 2
    both = seen["both"]
    assert len(both) <= half + len(Tokenizer.sot_sequence)
    assert both[1:1 + len(" " + ("Jira, " * 30).strip())] == list(range(len(" " + ("Jira, " * 30).strip())))
    assert both[-4] == 1299                       # хвост предыдущего текста — свежий
    assert len(seen["prev_only"]) == 1 + half - 1 + 3   # без терминов — как было
    assert len(seen["huge_hot"]) <= half + len(Tokenizer.sot_sequence)
