"""MLX Whisper на Mac: перевод ответа mlx_whisper в сегменты и слова Meet,
VAD перед вызовом и пересчёт времени к исходной записи, выбор движка,
откат на faster-whisper, каталог моделей и живой режим.

Настоящего `mlx` здесь нет: модуль `mlx_whisper` подставляется в
sys.modules, macOS и её версия — подменой."""

import importlib.util
import json
import sys
import types

import numpy as np
import pytest

from meet import asr, gigaam_asr, live_asr, mlx_asr, models, plat, settings
from meet.asr import Segment, Word

SR = 16000
# VAD и декодер MLX-пути — из faster-whisper (extra `engine-*`); в CI без
# движка эти тесты пропускаются.
needs_faster_whisper = pytest.mark.skipif(importlib.util.find_spec("faster_whisper") is None,
                                          reason="faster-whisper не установлен")


class FakeMlx(types.ModuleType):
    """Подставной mlx_whisper: отдаёт заданный ответ и запоминает вызов."""

    def __init__(self, result=None, error=None):
        super().__init__("mlx_whisper")
        self.result = result if result is not None else {"segments": []}
        self.error = error
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append({"audio": np.asarray(audio), **kw})
        if self.error is not None:
            raise self.error
        return self.result


def _mac(monkeypatch, version="14.4.1"):
    monkeypatch.setattr(plat, "is_macos", lambda: True)
    monkeypatch.setattr(mlx_asr.platform, "mac_ver", lambda: (version, ("", "", ""), "arm64"))


def _fake(monkeypatch, **kw) -> FakeMlx:
    fake = FakeMlx(**kw)
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
    return fake


def _model_on_disk(monkeypatch, tmp_path, weights="weights.safetensors"):
    """Снапшот модели MLX в кэше Hugging Face (своём для теста)."""
    monkeypatch.delenv(models.USER_HF_HUB_CACHE, raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    folder = tmp_path / "hub" / ("models--" + mlx_asr.MODEL.replace("/", "--"))
    snapshot = folder / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (folder / "refs").mkdir()
    (folder / "refs" / "main").write_text("abc", encoding="utf-8")
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    if weights:
        (snapshot / weights).write_bytes(b"x")
    return snapshot


def _speech(monkeypatch, chunks):
    """VAD отдаёт заданные участки речи (в отсчётах, как faster_whisper.vad)."""
    seen = []

    def vad(audio, sr=SR):
        seen.append(len(audio))
        return [{"start": int(s * SR), "end": int(e * SR)} for s, e in chunks]

    monkeypatch.setattr(mlx_asr, "speech_chunks", vad)
    return seen


def _seg(start, end, text, words=None, **extra):
    seg = {"start": start, "end": end, "text": text, "avg_logprob": -0.2, "no_speech_prob": 0.01, **extra}
    if words is not None:
        seg["words"] = [{"word": w, "start": s, "end": e, "probability": 0.9} for w, s, e in words]
    return seg


# --- перевод ответа ------------------------------------------------------------


@needs_faster_whisper
def test_segments_and_words_keep_their_times(monkeypatch, tmp_path):
    _mac(monkeypatch)
    snapshot = _model_on_disk(monkeypatch, tmp_path)
    fake = _fake(monkeypatch, result={"segments": [
        _seg(0.0, 1.5, " Hello there.", [(" Hello", 0.0, 0.6), (" there.", 0.7, 1.5)]),
        _seg(2.0, 3.0, " Bye.", [(" Bye.", 2.0, 3.0)]),
    ]})
    _speech(monkeypatch, [(0.0, 4.0)])
    progress = []
    out = mlx_asr.transcribe_audio(np.zeros(4 * SR, dtype=np.float32), mlx_asr.MODEL, "en", "Jira, API",
                                   on_progress=progress.append)
    assert out == [
        Segment(0.0, 1.5, "Hello there.", words=[Word(0.0, 0.6, " Hello"), Word(0.7, 1.5, " there.")],
                no_speech_prob=0.01, avg_logprob=-0.2),
        Segment(2.0, 3.0, "Bye.", words=[Word(2.0, 3.0, " Bye.")], no_speech_prob=0.01, avg_logprob=-0.2),
    ]
    call = fake.calls[0]
    assert call["path_or_hf_repo"] == str(snapshot)  # с диска, без запроса к Hugging Face
    assert call["word_timestamps"] is True and call["language"] == "en"
    assert call["initial_prompt"] == "Jira, API"
    assert call["verbose"] is None  # mlx_whisper не печатает текст встречи
    assert progress == [1.5 / 4, 3.0 / 4]  # ход — по концу сегмента


@needs_faster_whisper
def test_vad_cut_silence_and_word_times_return_to_the_recording(monkeypatch, tmp_path):
    _mac(monkeypatch)
    _model_on_disk(monkeypatch, tmp_path)
    # Речь 2–4 с и 10–12 с: модель слышит 4 с подряд, слово на 2,5 с склейки —
    # это 10,5 с записи.
    fake = _fake(monkeypatch, result={"segments": [
        _seg(0.0, 1.0, " One.", [(" One.", 0.2, 1.0)]),
        _seg(2.0, 3.5, " Two.", [(" Two.", 2.5, 3.5)]),
    ]})
    _speech(monkeypatch, [(2.0, 4.0), (10.0, 12.0)])
    out = mlx_asr.transcribe_audio(np.ones(14 * SR, dtype=np.float32), mlx_asr.MODEL, None, None)
    assert len(fake.calls[0]["audio"]) == 4 * SR  # тишина вырезана до вызова
    assert [(w.start, w.end) for s in out for w in s.words] == [(2.2, 3.0), (10.5, 11.5)]
    assert [(s.start, s.end) for s in out] == [(2.2, 3.0), (10.5, 11.5)]


@needs_faster_whisper
def test_no_speech_never_reaches_the_model(monkeypatch, tmp_path):
    _mac(monkeypatch)
    _model_on_disk(monkeypatch, tmp_path)
    fake = _fake(monkeypatch)
    _speech(monkeypatch, [])
    assert mlx_asr.transcribe_audio(np.zeros(SR, dtype=np.float32), mlx_asr.MODEL, None, None) == []
    assert fake.calls == []


@needs_faster_whisper
def test_transcribe_reads_the_file(monkeypatch, tmp_path):
    _mac(monkeypatch)
    _model_on_disk(monkeypatch, tmp_path)
    fake = _fake(monkeypatch, result={"segments": [_seg(0.0, 1.0, " Hi.", [(" Hi.", 0.0, 1.0)])]})
    _speech(monkeypatch, [(0.0, 1.0)])
    monkeypatch.setattr(mlx_asr, "_read", lambda path: np.zeros(2 * SR, dtype=np.float32))
    out = mlx_asr.transcribe(tmp_path / "a.wav", mlx_asr.MODEL, "en", None, None)
    assert [s.text for s in out] == ["Hi."] and len(fake.calls) == 1


@needs_faster_whisper
def test_off_mac_mlx_is_never_imported(monkeypatch, tmp_path):
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    fake = _fake(monkeypatch)
    with pytest.raises(RuntimeError):
        mlx_asr.transcribe_audio(np.zeros(SR, dtype=np.float32), mlx_asr.MODEL, None, None)
    assert fake.calls == []
    assert mlx_asr.usable() is False


@needs_faster_whisper
def test_live_window_is_short_and_without_word_stamps(monkeypatch, tmp_path):
    _mac(monkeypatch)
    _model_on_disk(monkeypatch, tmp_path)
    fake = _fake(monkeypatch, result={"segments": [_seg(0.5, 2.0, " Hello.")]})
    _speech(monkeypatch, [(0.0, 3.0)])
    out = mlx_asr.transcribe_audio(np.zeros(3 * SR, dtype=np.float32), mlx_asr.MODEL, "en", None,
                                   words=False, offset_s=100.0, temperature=0.0)
    assert fake.calls[0]["word_timestamps"] is False and fake.calls[0]["temperature"] == 0.0
    assert [(s.start, s.end, s.text, s.words) for s in out] == [(100.5, 102.0, "Hello.", [])]


# --- выбор движка ------------------------------------------------------------------


def _config(tmp_path, monkeypatch, section=None):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    raw = {"version": settings.SCHEMA_VERSION}
    if section is not None:
        raw["asr"] = section
    (data / "config.json").write_text(json.dumps(raw), encoding="utf-8")


@pytest.fixture
def mac_ready(monkeypatch, tmp_path):
    """Mac 14, MLX ставится, модель скачана, процессор (как на любом Mac)."""
    _mac(monkeypatch)
    _model_on_disk(monkeypatch, tmp_path)
    _fake(monkeypatch)
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    monkeypatch.setattr(asr, "cpu_reason", lambda setting=None: None)


# Mac 0.6.2: выбранная модель на все встречи (подробнее — test_mac_asr.py).
MAC_TURBO = {"cpu_backend": "faster-whisper", "cpu_model": mlx_asr.MODEL}


@pytest.mark.parametrize("language", ["ru", "en", "auto"])
def test_mac_14_turbo_chosen_goes_to_mlx_for_any_language(mac_ready, monkeypatch, tmp_path, language):
    _config(tmp_path, monkeypatch, {**MAC_TURBO, "language": language})
    choice = asr.choose()
    assert (choice.backend, choice.device, choice.model, choice.note) == ("mlx-whisper", "mps", mlx_asr.MODEL, None)


def test_mac_russian_stays_on_gigaam(mac_ready, monkeypatch, tmp_path):
    _config(tmp_path, monkeypatch, {"language": "ru"})
    assert asr.choose().backend == "gigaam"


def test_macos_13_keeps_faster_whisper(mac_ready, monkeypatch, tmp_path):
    _mac(monkeypatch, "13.6")
    _config(tmp_path, monkeypatch, {**MAC_TURBO, "language": "en"})
    assert asr.choose().backend == "faster-whisper"


def test_windows_keeps_faster_whisper(mac_ready, monkeypatch, tmp_path):
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    _config(tmp_path, monkeypatch, {**MAC_TURBO, "language": "en"})
    assert asr.choose().backend == "faster-whisper"


def test_model_missing_keeps_faster_whisper(monkeypatch, tmp_path, capsys):
    _mac(monkeypatch)
    _model_on_disk(monkeypatch, tmp_path, weights=None)  # загрузка оборвалась: весов нет
    _fake(monkeypatch)
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    _config(tmp_path, monkeypatch, {**MAC_TURBO, "language": "en"})
    assert asr.choose().backend == "faster-whisper"
    assert "модель MLX Whisper не скачана" in capsys.readouterr().out


def test_mlx_not_installed_keeps_faster_whisper(mac_ready, monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "mlx_whisper", None)  # import падает
    _config(tmp_path, monkeypatch, {**MAC_TURBO, "language": "en"})
    assert asr.choose().backend == "faster-whisper"
    assert "MLX Whisper не установлен" in capsys.readouterr().out


def test_transcribe_wav_routes_mlx_choice(monkeypatch, tmp_path):
    seen = {}

    def fake(path, model, language, prompt, on_progress):
        seen.update(path=path, model=model, language=language, prompt=prompt)
        return ["сегменты"]

    monkeypatch.setattr(mlx_asr, "transcribe", fake)
    out = asr.transcribe_wav(tmp_path / "a.wav", "Jira", language="en",
                             choice=asr.Choice("mlx-whisper", "mps"))
    assert out == ["сегменты"]
    assert seen == {"path": tmp_path / "a.wav", "model": mlx_asr.MODEL, "language": "en", "prompt": "Jira"}


# --- откат на faster-whisper ----------------------------------------------------------


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Пайплайн: выбран MLX, подставной mlx_whisper падает; faster-whisper —
    подделка, MLX — настоящий путь asr.transcribe_wav → mlx_asr."""
    import meet.transcribe as tr
    from meet.diarize import Diarization

    _mac(monkeypatch)
    _model_on_disk(monkeypatch, tmp_path)
    _fake(monkeypatch, error=RuntimeError("[metal::Device] Unable to load kernel"))
    _speech(monkeypatch, [(0.0, 1.0)])
    monkeypatch.setattr(mlx_asr, "_read", lambda path: np.zeros(2 * SR, dtype=np.float32))
    monkeypatch.setattr(asr, "choose", lambda path=None, **kw: asr.Choice("mlx-whisper", "mps",
                                                                           note=asr.NOT_RUSSIAN))
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    real = asr.transcribe_wav
    calls = []

    def fake_asr(path, hotwords=None, *, choice=None, model_name=None, **kw):
        backend = choice.backend if choice is not None else "faster-whisper"
        calls.append(backend)
        if backend == "mlx-whisper":
            return real(path, hotwords, choice=choice, **kw)
        return [Segment(0.0, 1.0, "whisper", words=[Word(0.0, 1.0, " whisper")])]

    monkeypatch.setattr(tr, "transcribe_wav", fake_asr)
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "_maybe_align", lambda segments, wav, enabled, **kw: segments)
    monkeypatch.setattr(tr, "diarize_wav", lambda p, num_speakers=None, exclusive=False, **kw: Diarization(
        turns=[(0.0, 5.0, "SPEAKER_00")]))
    monkeypatch.setattr(tr, "_match_names", lambda diar, threshold=None: {})
    return tr, calls


@needs_faster_whisper
def test_mlx_failure_falls_back_to_faster_whisper_on_cpu(pipeline, tmp_path, capsys):
    from meet import library

    tr, calls = pipeline
    folder = tmp_path / "2026-11-18_10-00_import"
    folder.mkdir()
    (folder / "source.mp4").write_bytes(b"x")
    tr.transcribe(str(folder), align=False)
    out = capsys.readouterr().out
    assert calls == ["mlx-whisper", "faster-whisper"]
    assert "MLX Whisper не сработал" in out and "RuntimeError" in out
    saved = library.read_transcript(folder)
    assert saved["asr"] == {"backend": "faster-whisper", "device": "cpu"}
    assert saved["asr_note"] == asr.NOT_RUSSIAN


# --- каталог моделей ---------------------------------------------------------------


def _ids(state):
    return [m["id"] for m in state["items"]]


def test_catalogue_shows_mlx_model_only_on_mac(monkeypatch, tmp_path):
    _model_on_disk(monkeypatch, tmp_path)
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    assert mlx_asr.MODEL not in _ids(models.state())
    _mac(monkeypatch)
    item = next(m for m in models.state()["items"] if m["id"] == mlx_asr.MODEL)
    assert item["backend"] == "mlx-whisper" and item["kind"] == models.ASR
    assert item["downloaded"] is True and item["selected"] is False


def test_mlx_model_downloads_only_on_mac(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    got = []
    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = lambda repo, **kw: got.append(repo) or str(tmp_path / repo)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setattr(models, "token", lambda: None)
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    lines = []
    assert models.download(mlx_asr.MODEL, lines.append) == 2 and got == []
    _mac(monkeypatch)
    assert models.download(mlx_asr.MODEL, lines.append) == 0 and got == [mlx_asr.MODEL]


def test_mlx_model_is_not_a_faster_whisper_fallback(monkeypatch):
    monkeypatch.setattr(models, "downloaded", lambda name: name == mlx_asr.MODEL)
    monkeypatch.setattr(asr, "_asr_settings", lambda: ("nope/model", "ru"))
    assert asr.local_whisper_model("cuda") is None


# --- живой режим ---------------------------------------------------------------------


class FakeWhisper:
    name = "Whisper"
    latin_pass = False

    def __init__(self):
        self.loaded = False

    def load(self):
        self.loaded = True

    def unload(self):
        self.loaded = False

    def transcribe_window(self, audio, *, offset_s=0.0, hotwords=None, initial_prompt=None):
        return [Segment(offset_s, offset_s + 1.0, "whisper")]


def _live_cfg(language, **asr_raw):
    return settings.Settings.from_raw({"asr": {"language": language, **asr_raw}})


def test_live_pick_mlx_on_mac_for_english(mac_ready, monkeypatch):
    monkeypatch.setattr(asr, "Transcriber", FakeWhisper)
    chosen = live_asr.pick(_live_cfg("en", **MAC_TURBO), log=lambda _l: None)
    assert isinstance(chosen, live_asr.MlxLive) and chosen.policy == live_asr.MLX_POLICY
    assert chosen.policy.max_s < live_asr.whisper_policy().max_s  # окна короче, чем у Whisper
    assert chosen.latin_pass is False
    # русский — как раньше: GigaAM
    monkeypatch.setattr(gigaam_asr, "downloaded", lambda name: True)
    assert isinstance(live_asr.pick(_live_cfg("ru"), log=lambda _l: None), live_asr.GigaamLive)


def test_live_pick_off_mac_keeps_whisper(monkeypatch):
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    monkeypatch.setattr(asr, "Transcriber", FakeWhisper)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    assert isinstance(live_asr.pick(_live_cfg("en"), log=lambda _l: None), FakeWhisper)


@needs_faster_whisper
def test_live_mlx_window(mac_ready, monkeypatch):
    fake = _fake(monkeypatch, result={"segments": [_seg(0.2, 1.0, " Hi.")]})
    _speech(monkeypatch, [(0.0, 2.0)])
    live = live_asr.MlxLive("en", fallback=FakeWhisper, log=lambda _l: None)
    live.load()
    out = live.transcribe_window(np.zeros(2 * SR, dtype=np.float32), offset_s=30.0,
                                 hotwords="Jira", initial_prompt="Earlier text.")
    assert [(s.start, s.end, s.text) for s in out] == [(30.2, 31.0, "Hi.")]
    call = fake.calls[-1]
    assert call["word_timestamps"] is False and call["language"] == "en"
    assert call["initial_prompt"] == "Jira Earlier text."
    live.unload()


def test_live_mlx_load_failure_falls_back_to_whisper(mac_ready, monkeypatch):
    _fake(monkeypatch, error=RuntimeError("Metal недоступен"))
    logs = []
    live = live_asr.MlxLive("en", fallback=FakeWhisper, log=logs.append)
    live.load()
    assert live.name == "Whisper" and live.policy is None
    assert live.transcribe_window(np.zeros(SR, dtype=np.float32), offset_s=5.0)[0].text == "whisper"
    assert any("MLX Whisper" in line and "RuntimeError" in line for line in logs)


@needs_faster_whisper
def test_live_mlx_three_failed_windows_switch_to_whisper(mac_ready, monkeypatch):
    fake = _fake(monkeypatch, result={"segments": []})
    _speech(monkeypatch, [(0.0, 1.0)])
    live = live_asr.MlxLive("en", fallback=FakeWhisper, log=lambda _l: None)
    live.load()
    fake.error = RuntimeError("сбой окна")
    audio = np.zeros(SR, dtype=np.float32)
    for _ in range(live_asr.GIGAAM_MAX_ERRORS):
        with pytest.raises(RuntimeError):
            live.transcribe_window(audio)
    assert live.name == "Whisper"
    assert live.transcribe_window(audio, offset_s=7.0)[0].text == "whisper"
