"""Выбор и работа распознавания живого режима (GigaAM / Whisper).
Модели поддельные; звук синтетический."""

import sys
import types
import wave

import numpy as np
import pytest

from meet import asr, gigaam_asr, live_asr, settings
from meet.live_asr import GIGAAM_POLICY, GigaamLive, pick

SR = 16000


class FakeWhisper:
    name = "Whisper"
    latin_pass = False

    def __init__(self):
        self.loaded = False
        self.calls = []

    def load(self):
        self.loaded = True

    def unload(self):
        self.loaded = False

    def transcribe_window(self, audio, *, offset_s=0.0, hotwords=None, initial_prompt=None):
        self.calls.append(offset_s)
        return [asr.Segment(offset_s, offset_s + 1.0, "whisper")]


def _cfg(**assist):
    raw = {"assist": assist, "asr": {"language": assist.pop("language", "ru")}}
    return settings.Settings.from_raw(raw)


@pytest.fixture
def whisper(monkeypatch):
    made = []

    def factory():
        made.append(FakeWhisper())
        return made[-1]

    monkeypatch.setattr(asr, "Transcriber", factory)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    return made


def test_pick_gigaam_for_russian_when_installed_and_downloaded(whisper, monkeypatch):
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)
    monkeypatch.setattr(gigaam_asr, "downloaded", lambda name: True)
    chosen = pick(_cfg(), log=lambda _l: None)
    assert isinstance(chosen, GigaamLive) and chosen.policy == GIGAAM_POLICY
    assert chosen.model_name == gigaam_asr.MODEL_NAME and chosen.device == "cpu"


@pytest.mark.parametrize("why,patch,cfg", [
    ("язык", {}, {"language": "en"}),
    ("язык auto", {}, {"language": "auto"}),
    ("нет пакета", {"installed": False}, {}),
    ("не скачана", {"downloaded": False}, {}),
    ("так в настройках", {}, {"live_asr": "whisper"}),
])
def test_pick_whisper_otherwise(whisper, monkeypatch, why, patch, cfg):
    monkeypatch.setattr(gigaam_asr, "installed", lambda: patch.get("installed", True))
    monkeypatch.setattr(gigaam_asr, "downloaded", lambda name: patch.get("downloaded", True))
    logs = []
    chosen = pick(_cfg(**cfg), log=logs.append)
    assert isinstance(chosen, FakeWhisper), why
    assert logs and "Whisper" in logs[0]


def test_live_asr_setting_round_trip():
    a = settings.Settings.from_raw({"assist": {"live_asr": "whisper"}}).assist
    assert a.live_asr == "whisper" and a.to_raw()["live_asr"] == "whisper"
    assert settings.Settings.from_raw({"assist": {"live_asr": "чепуха"}}).assist.live_asr == "auto"


class FakeModel:
    """GigaAM: слова относительно начала файла; читает сам wav."""

    def __init__(self, fail=0):
        self.paths = []
        self.fail = fail

    def transcribe(self, path, word_timestamps=False):
        assert word_timestamps is True
        if path.endswith("warm.wav"):  # прогрев при загрузке
            self.warmed = True
            return types.SimpleNamespace(text="", words=[])
        if self.fail:
            self.fail -= 1
            raise RuntimeError("модель упала")
        with wave.open(path, "rb") as w:
            self.paths.append((path, w.getframerate(), w.getnframes()))
        words = [types.SimpleNamespace(text="Добрый", start=0.2, end=0.6),
                 types.SimpleNamespace(text="день.", start=0.7, end=1.1),
                 types.SimpleNamespace(text="Начнём", start=2.0, end=2.5)]
        return types.SimpleNamespace(text="Добрый день. Начнём", words=words)


def _live(model=None, vad=None, **kw):
    made = []

    def fallback():
        made.append(FakeWhisper())
        return made[-1]

    g = GigaamLive("v3_e2e_rnnt", "cpu", fallback=fallback, model=model or FakeModel(),
                   vad=vad or (lambda audio, sr: [(0.0, 1.0)]), log=lambda _l: None, **kw)
    g.made = made
    return g


def test_window_goes_through_a_wav_file_and_gets_meeting_timecodes():
    g = _live()
    g.load()
    try:
        audio = (np.sin(np.arange(3 * SR) / 10) * 0.3).astype(np.float32)
        segs = g.transcribe_window(audio, offset_s=100.0)
    finally:
        g.unload()
    assert [s.text for s in segs] == ["Добрый день.", "Начнём"]
    assert segs[0].start == 100.2 and segs[1].start == 102.0


def test_wav_written_at_16k_and_temp_dir_removed():
    model = FakeModel()
    g = _live(model=model)
    g.load()
    tmp = g._tmp
    g.transcribe_window(np.full(SR, 0.1, dtype=np.float32), offset_s=0.0)
    assert model.paths[0][1] == SR and model.paths[0][2] == SR
    g.unload()
    assert not tmp.exists()


def test_no_speech_or_silence_never_reaches_the_model():
    model = FakeModel()
    g = _live(model=model, vad=lambda audio, sr: [])
    g.load()
    assert g.transcribe_window(np.full(SR, 0.1, dtype=np.float32)) == []
    assert g.transcribe_window(np.zeros(SR, dtype=np.float32)) == []
    assert model.paths == [] and model.warmed  # прогрев — при загрузке, не на окне
    g.unload()


def test_load_failure_falls_back_to_whisper(monkeypatch):
    def broken(name, device, on_line=None):
        raise gigaam_asr.Unavailable("GigaAM не загрузилась (тест)")

    monkeypatch.setattr(gigaam_asr, "load", broken)
    g = GigaamLive("v3_e2e_rnnt", "cpu", fallback=FakeWhisper, log=lambda _l: None)
    g.load()
    assert g.name == "Whisper" and g.policy is None and g.latin_pass is False
    assert g.transcribe_window(np.full(SR, 0.1, dtype=np.float32), offset_s=5.0)[0].text == "whisper"


def test_three_failed_windows_switch_to_whisper():
    g = _live(model=FakeModel(fail=3))
    g.load()
    audio = np.full(SR, 0.1, dtype=np.float32)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            g.transcribe_window(audio)
    assert g.name == "Whisper" and g.made and g.made[0].loaded
    assert g.transcribe_window(audio, offset_s=7.0)[0].text == "whisper"


def test_one_failure_does_not_switch():
    g = _live(model=FakeModel(fail=1))
    g.load()
    audio = np.full(SR, 0.1, dtype=np.float32)
    with pytest.raises(RuntimeError):
        g.transcribe_window(audio)
    assert g.transcribe_window(audio)[0].text == "Добрый день."
    assert g.name == "GigaAM"
    g.unload()


def test_cuda_without_torch_cuda_runs_on_cpu(monkeypatch):
    seen = {}

    def load(name, device, on_line=None):
        seen["device"] = device
        return FakeModel()

    monkeypatch.setattr(gigaam_asr, "load", load)
    monkeypatch.setattr(live_asr, "_torch_cuda", lambda: False)
    g = GigaamLive("v3_e2e_rnnt", "cuda", fallback=FakeWhisper, log=lambda _l: None)
    g.load()
    assert seen["device"] == "cpu" and g.device == "cpu"
    g.unload()


def test_quiet_cut_prefers_the_latest_pause_in_range():
    rng = np.random.default_rng(0)
    speech = lambda s: rng.normal(0, 0.3, int(s * SR)).astype(np.float32)  # noqa: E731
    pause = lambda s: rng.normal(0, 0.001, int(s * SR)).astype(np.float32)  # noqa: E731
    audio = np.concatenate([speech(4.5), pause(0.3), speech(1.2), pause(0.3), speech(3.0)])
    cut = gigaam_asr.quiet_cut(audio, SR, 4.0, 7.0)
    assert 6.0 <= cut <= 6.3                     # вторая (последняя) пауза
    flat = speech(9.0)
    assert 4.0 <= gigaam_asr.quiet_cut(flat, SR, 4.0, 7.0) <= 7.0   # нет паузы — самый тихий кадр


def test_text_fixes_never_break_a_window(monkeypatch):
    from meet import textfix

    def broken(*a, **kw):
        raise ValueError("сломанное правило")

    monkeypatch.setattr(textfix, "apply_rules", broken)
    seg = asr.Segment(0.0, 1.0, "текст")
    fixes = live_asr.TextFixes([{"from": "а", "to": "б"}], ["API"], latin=True)
    assert fixes([seg], latin=True) == [seg] and seg.text == "текст"


def test_find_pause_only_reports_real_pauses():
    rng = np.random.default_rng(0)
    speech = lambda s: rng.normal(0, 0.3, int(s * SR)).astype(np.float32)  # noqa: E731
    pause = lambda s: rng.normal(0, 0.001, int(s * SR)).astype(np.float32)  # noqa: E731
    assert gigaam_asr.find_pause(speech(8.0), SR, 4.0, 7.0) is None
    audio = np.concatenate([speech(4.6), pause(0.4), speech(1.0)])
    assert 4.6 <= gigaam_asr.find_pause(audio, SR, 4.0, len(audio) / SR) <= 5.0


def test_in_process_wav_reads_our_wav_without_ffmpeg(monkeypatch, tmp_path):
    """Окно живого режима GigaAM читает без процесса ffmpeg: подмена
    `gigaam.model.load_audio` читает свой wav (16 кГц, моно, PCM16) модулем
    wave, остальное — прежним путём."""
    calls = []
    fake_model = types.ModuleType("gigaam.model")
    fake_model.load_audio = lambda path, sample_rate=16000: calls.append(path) or "ffmpeg"
    fake_pkg = types.ModuleType("gigaam")
    fake_pkg.model = fake_model
    monkeypatch.setitem(sys.modules, "gigaam", fake_pkg)
    monkeypatch.setitem(sys.modules, "gigaam.model", fake_model)
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(from_numpy=lambda a: a))
    assert gigaam_asr.in_process_wav() is True
    assert gigaam_asr.in_process_wav() is True            # повторно — без второй обёртки
    ours = tmp_path / "w.wav"
    gigaam_asr._write_wav(ours, (np.ones(SR) * 1000).astype(np.int16), SR)
    got = fake_model.load_audio(str(ours))
    assert len(got) == SR and abs(float(got[0]) - 1000 / 32768) < 1e-6 and calls == []
    other = tmp_path / "x.wav"
    with wave.open(str(other), "wb") as w:                # 44,1 кГц — не наш формат
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100); w.writeframes(b"\0\0" * 10)
    assert fake_model.load_audio(str(other)) == "ffmpeg" and calls == [str(other)]
