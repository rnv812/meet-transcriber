"""Видеокарта видна, а считать на ней нечем: движок профиля CPU на машине с
NVIDIA (ctranslate2 карту видит, cuBLAS/cuDNN не установлены). «auto» должен
выбрать процессор (GigaAM) — в расшифровке, состоянии движка и живом режиме;
а если CUDA всё же упала без библиотек — откат на процессор, а не сбой задачи.
Живая проверка 0.3.0: «Library cublas64_12.dll is not found or cannot be loaded»."""

import json
import sys
import types

import pytest

from meet import asr, engine, gigaam_asr, library, live_asr, settings
from meet.asr import Segment, Word
from meet.diarize import Diarization

CUBLAS_ERROR = "Library cublas64_12.dll is not found or cannot be loaded"


def _marker(folder, profile):
    (folder / asr.ENGINE_MARKER).write_text(
        json.dumps({"version": "0.3.0", "profile": profile}), encoding="utf-8")


def _engine_venv(monkeypatch, tmp_path, profile):
    """sys.prefix — venv движка с маркером установщика заданного профиля."""
    venv = tmp_path / "engine"
    venv.mkdir()
    if profile is not None:
        _marker(venv, profile)
    monkeypatch.setattr(sys, "prefix", str(venv))
    return venv


def _cuda_dlls(tmp_path, *names):
    folder = tmp_path / "nvidia" / "bin"
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_bytes(b"MZ")
    return folder


def _windows_probe(monkeypatch, dirs, loads=True):
    monkeypatch.setattr(asr, "_WINDOWS", True)
    monkeypatch.setattr(asr, "_library_dirs", lambda: list(dirs))
    loaded = []

    def load(libraries):
        loaded.append([p.name for p in libraries])
        return loads

    monkeypatch.setattr(asr, "_libraries_load", load)
    return loaded


def _gpu_visible(monkeypatch):
    monkeypatch.setattr(asr, "cuda_available", lambda: True)


# --- профиль движка и проверка библиотек --------------------------------------


def test_engine_profile_reads_installer_marker(tmp_path):
    assert asr.engine_profile(tmp_path) is None  # dev-окружение: маркера нет
    _marker(tmp_path, "cpu")
    assert asr.engine_profile(tmp_path) == "cpu"
    (tmp_path / asr.ENGINE_MARKER).write_text("мусор", encoding="utf-8")
    assert asr.engine_profile(tmp_path) is None


def test_find_cuda_libraries_needs_cublas_and_cudnn(tmp_path):
    only_cublas = _cuda_dlls(tmp_path / "a", "cublas64_12.dll", "cublasLt64_12.dll")
    assert asr.find_cuda_libraries([only_cublas]) is None
    both = _cuda_dlls(tmp_path / "b", "cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")
    found = asr.find_cuda_libraries([tmp_path / "нет", only_cublas, both])
    assert [p.name for p in found] == ["cublas64_12.dll", "cudnn64_9.dll"]
    assert found[0].parent == only_cublas  # первая папка с библиотекой


def test_gpu_visible_but_cpu_profile_resolves_to_cpu_and_gigaam(monkeypatch, tmp_path):
    _engine_venv(monkeypatch, tmp_path, "cpu")
    _gpu_visible(monkeypatch)
    # даже если CUDA Toolkit где-то в PATH — профиль CPU на карту не идёт
    loaded = _windows_probe(monkeypatch, [_cuda_dlls(tmp_path, "cublas64_12.dll", "cudnn64_9.dll")])
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    assert asr.resolve_device("auto") == "cpu"
    assert asr.resolve_device("cuda") == "cuda"  # явный выбор уважается
    choice = asr.choose()
    assert (choice.backend, choice.device) == ("gigaam", "cpu")
    assert loaded == []  # DLL даже не трогали


def test_cuda_profile_with_loadable_libraries_resolves_to_cuda(monkeypatch, tmp_path):
    _engine_venv(monkeypatch, tmp_path, "cuda")
    _gpu_visible(monkeypatch)
    loaded = _windows_probe(monkeypatch, [_cuda_dlls(tmp_path, "cublas64_12.dll", "cudnn64_9.dll")])
    assert asr.resolve_device("auto") == "cuda"
    assert asr.resolve_device("auto") == "cuda"
    assert loaded == [["cublas64_12.dll", "cudnn64_9.dll"]]  # проверка — один раз на процесс


def test_cuda_profile_without_libraries_resolves_to_cpu(monkeypatch, tmp_path):
    _engine_venv(monkeypatch, tmp_path, "cuda")
    _gpu_visible(monkeypatch)
    _windows_probe(monkeypatch, [_cuda_dlls(tmp_path, "cublasLt64_12.dll")])
    assert asr.resolve_device("auto") == "cpu"


def test_cuda_libraries_that_do_not_load_mean_cpu(monkeypatch, tmp_path):
    _engine_venv(monkeypatch, tmp_path, None)  # dev-окружение
    _gpu_visible(monkeypatch)
    _windows_probe(monkeypatch, [_cuda_dlls(tmp_path, "cublas64_12.dll", "cudnn64_9.dll")], loads=False)
    assert asr.resolve_device("auto") == "cpu"


def test_missing_cuda_library_recognises_the_real_errors():
    assert asr.missing_cuda_library(RuntimeError(CUBLAS_ERROR))
    assert asr.missing_cuda_library(RuntimeError(
        "Could not locate cudnn_ops64_9.dll. Please make sure it is in your library path!"))
    assert asr.missing_cuda_library(OSError("cudart64_12.dll: could not load"))
    assert not asr.missing_cuda_library(RuntimeError("CUDA failed with error out of memory"))
    assert not asr.missing_cuda_library(ValueError("cublas handle: bad argument"))


# --- состояние движка ------------------------------------------------------------


def _engine_setting(monkeypatch, tmp_path, device="auto"):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "config.json").write_text(json.dumps({"asr": {"device": device}}), encoding="utf-8")
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    monkeypatch.setattr(engine, "gpu", lambda: {"available": True, "name": "NVIDIA GeForce RTX 5070 Ti"})


def test_engine_status_shows_cpu_and_gigaam_for_cpu_profile_on_nvidia(monkeypatch, tmp_path):
    _engine_venv(monkeypatch, tmp_path, "cpu")
    _engine_setting(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "ctranslate2", None)  # состояние не трогает ctranslate2
    state = engine.state()
    assert (state["device"], state["backend"]) == ("cpu", "gigaam")
    assert state["speed_factor"] == engine.SPEED_FACTOR["cpu"]["gigaam"]


def test_engine_status_matches_recognition_for_cuda_profile(monkeypatch, tmp_path):
    _engine_venv(monkeypatch, tmp_path, "cuda")
    _engine_setting(monkeypatch, tmp_path)
    loaded = _windows_probe(monkeypatch, [_cuda_dlls(tmp_path, "cublas64_12.dll", "cudnn64_9.dll")])
    state = engine.state()
    assert (state["device"], state["backend"]) == ("cuda", "faster-whisper")
    assert state["speed_factor"] == 0.22
    assert loaded == []  # резидент DLL не грузит: pip должен суметь их обновить


def test_engine_status_cuda_profile_without_libraries_is_cpu(monkeypatch, tmp_path):
    _engine_venv(monkeypatch, tmp_path, "cuda")
    _engine_setting(monkeypatch, tmp_path)
    _windows_probe(monkeypatch, [])
    _gpu_visible(monkeypatch)
    state = engine.state()
    assert state["device"] == "cpu" == asr.resolve_device("auto")


# --- откат при сбое CUDA: расшифровка ----------------------------------------


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Пайплайн с настоящим asr.choose: карта видна, библиотеки «на месте»,
    но Whisper на CUDA падает без cuBLAS (как на живой проверке)."""
    import meet.transcribe as tr

    calls = []
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MEET_DATA_DIR", str(state))
    _gpu_visible(monkeypatch)
    monkeypatch.setattr(asr, "cuda_runtime_ok", lambda **kw: True)
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    failing = {"gigaam": False}

    def fake_asr(path, hotwords=None, *, choice=None, model_name=None, **kw):
        device = choice.device if choice is not None else asr.resolve_device()
        backend = choice.backend if choice is not None else "faster-whisper"
        calls.append((backend, device))
        if backend == "gigaam" and failing["gigaam"]:
            raise gigaam_asr.Unavailable("GigaAM: не удалось скачать v3_e2e_rnnt.ckpt (нет связи)")
        if device == "cuda":
            raise RuntimeError(CUBLAS_ERROR)
        if backend == "gigaam":
            return [Segment(0.0, 1.0, "гигаам", words=[Word(0.0, 1.0, " гигаам")])]
        return [Segment(0.0, 1.0, "whisper")]

    monkeypatch.setattr(tr, "transcribe_wav", fake_asr)
    monkeypatch.setattr(tr, "_maybe_align", lambda segments, wav, enabled: segments)
    monkeypatch.setattr(tr, "diarize_wav", lambda p, num_speakers=None, exclusive=False, **kw: Diarization(
        turns=[(0.0, 5.0, "SPEAKER_00")]))
    monkeypatch.setattr(tr, "_match_names", lambda diar, threshold=None: {})
    return tr, calls, failing


def _two_tracks(tmp_path):
    folder = tmp_path / "2026-10-03_10-00"
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    return folder


def test_cublas_error_falls_back_to_gigaam_on_cpu(pipeline, tmp_path, capsys):
    tr, calls, _ = pipeline
    folder = _two_tracks(tmp_path)
    tr.transcribe(str(folder), align=True)
    out = capsys.readouterr().out
    assert "видеокарта недоступна" in out and "cublas64_12.dll" in out
    # sys: Whisper на CUDA упал → GigaAM на CPU; mic — сразу GigaAM на CPU
    assert calls == [("faster-whisper", "cuda"), ("gigaam", "cpu"), ("gigaam", "cpu")]
    data = library.read_transcript(folder)
    assert data["asr"]["backend"] == "gigaam" and data["asr"]["device"] == "cpu"
    assert data["asr_note"] == asr.CUDA_FAILED
    assert library.describe(folder).to_raw()["asr_note"] == "cuda_failed"
    assert asr.resolve_device("cuda") == "cpu"  # дальше в процессе — только процессор


def test_cublas_error_after_gigaam_failure_ends_on_cpu_whisper(pipeline, monkeypatch, tmp_path):
    """GigaAM выбрана на CUDA и не загрузилась, запасной Whisper на той же
    карте упал без cuBLAS — дальше процессор (GigaAM снова недоступна —
    Whisper на CPU), а не ошибка задачи."""
    tr, calls, failing = pipeline
    failing["gigaam"] = True
    monkeypatch.setattr(asr, "local_whisper_model", lambda device: f"whisper-{device}")
    data = tmp_path / "state"
    (data / "config.json").write_text(json.dumps({"asr": {"backend": "gigaam"}}), encoding="utf-8")
    folder = tmp_path / "2026-10-03_11-00_import"
    folder.mkdir()
    (folder / "source.mp4").write_bytes(b"x")
    tr.transcribe(str(folder), align=False)
    assert calls == [("gigaam", "cuda"), ("faster-whisper", "cuda"),
                     ("gigaam", "cpu"), ("faster-whisper", "cpu")]
    saved = library.read_transcript(folder)
    assert saved["asr"] == {"backend": "faster-whisper", "device": "cpu"}
    assert saved["asr_note"] == asr.GIGAAM_FAILED  # своя пометка выбора важнее


def test_other_cuda_errors_are_not_swallowed(pipeline, monkeypatch, tmp_path):
    import meet.transcribe as tr

    def boom(path, hotwords=None, **kw):
        raise RuntimeError("CUDA error: an illegal memory access was encountered")

    monkeypatch.setattr(tr, "transcribe_wav", boom)
    folder = tmp_path / "2026-10-03_12-00_import"
    folder.mkdir()
    (folder / "source.mp4").write_bytes(b"x")
    with pytest.raises(RuntimeError, match="illegal memory access"):
        tr.transcribe(str(folder), align=False)


# --- живой режим -------------------------------------------------------------------


def test_live_pick_on_cpu_profile_with_nvidia_uses_gigaam_on_cpu(monkeypatch, tmp_path):
    _engine_venv(monkeypatch, tmp_path, "cpu")
    _gpu_visible(monkeypatch)
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)
    monkeypatch.setattr(gigaam_asr, "downloaded", lambda name: True)
    cfg = settings.Settings.from_raw({"asr": {"language": "ru", "device": "auto"}})
    chosen = live_asr.pick(cfg, log=lambda _l: None)
    assert isinstance(chosen, live_asr.GigaamLive) and chosen.device == "cpu"


def _fake_whisper_models(monkeypatch, *, fail_on_load=False):
    made = []

    class FakeModel:
        def __init__(self, name, device, compute_type, **kw):
            if device == "cuda" and fail_on_load:
                raise RuntimeError(CUBLAS_ERROR)
            self.device = device
            made.append((name, device, compute_type))

        def transcribe(self, audio, **kw):
            if self.device == "cuda":
                raise RuntimeError(CUBLAS_ERROR)  # cuBLAS грузится лениво — на первом окне
            return iter([types.SimpleNamespace(start=0.0, end=1.0, text=" окно", words=None)]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(asr, "_add_nvidia_dll_dirs", lambda: None)
    monkeypatch.setattr(asr, "_apply_hf_token", lambda: None)
    monkeypatch.setattr(asr, "_asr_settings", lambda: ("big-gpu-model", "ru"))
    monkeypatch.setattr(asr, "_cpu_model_setting", lambda: "cpu-model")
    _gpu_visible(monkeypatch)
    monkeypatch.setattr(asr, "cuda_runtime_ok", lambda **kw: True)
    monkeypatch.setattr(asr, "resolve_device",
                        lambda setting=None: "cpu" if asr._cuda_failure else "cuda")
    return made


def test_live_whisper_window_falls_back_to_cpu_on_cublas_error(monkeypatch, capsys):
    made = _fake_whisper_models(monkeypatch)
    t = asr.Transcriber()
    t.load()
    assert t.device == "cuda"
    segments = t.transcribe_window([0.0] * 16000, offset_s=10.0)
    assert [s.text for s in segments] == ["окно"] and segments[0].start == 10.0
    assert (t.device, t.model_name, t.compute_type) == ("cpu", "cpu-model", "int8")
    assert made[-1] == ("cpu-model", "cpu", "int8")
    assert "видеокарта недоступна" in capsys.readouterr().out


def test_live_whisper_load_falls_back_to_cpu_on_cublas_error(monkeypatch):
    made = _fake_whisper_models(monkeypatch, fail_on_load=True)
    t = asr.Transcriber()
    t.load()
    assert t.device == "cpu" and made == [("cpu-model", "cpu", "int8")]
