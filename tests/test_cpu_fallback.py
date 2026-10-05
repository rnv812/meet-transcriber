"""Движок с CUDA, а расшифровка идёт на процессоре — никогда молча.

Живой случай 05.10.2026: видеокарта NVIDIA — внешняя, в док-станции
Thunderbolt; док отключили, и задачи пошли «(GigaAM, cpu)» с диаризацией
на процессоре (2263 с на часовую встречу) без единой строки о причине.
Здесь: причина «auto → процессор» (видеокарта не найдена, нет библиотек CUDA)
— строкой в журнал и пометкой в карточку записи; диаризация, голоса и
выравнивание (torch) — на видеокарте всегда, когда torch её видит, чем бы ни
распознавался текст; и ложное «библиотеки CUDA не грузятся», когда torch уже
загрузил свои cuBLAS/cuDNN раньше проверки (WinError 127)."""

import json
import sys
import types

import pytest

from meet import asr, events, gigaam_asr, jobs, library
from meet.asr import Segment, Word
from meet.diarize import Diarization


def _engine(monkeypatch, tmp_path, profile="cuda", *, libs=True, gpu=False, device="auto"):
    """Движок профиля `profile`; библиотеки CUDA есть/нет; карта видна/нет."""
    venv = tmp_path / "engine"
    venv.mkdir(parents=True, exist_ok=True)
    if profile is not None:
        (venv / asr.ENGINE_MARKER).write_text(json.dumps({"profile": profile}), encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(venv))
    folder = tmp_path / "nvidia"
    folder.mkdir(exist_ok=True)
    if libs:
        for name in ("cublas64_12.dll", "cudnn64_9.dll"):
            (folder / name).write_bytes(b"MZ")
    monkeypatch.setattr(asr, "_WINDOWS", True)
    monkeypatch.setattr(asr, "_library_dirs", lambda: [folder])
    monkeypatch.setattr(asr, "_libraries_load", lambda libraries: True)
    monkeypatch.setattr(asr, "cuda_available", lambda: gpu)
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "config.json").write_text(json.dumps({"asr": {
        "device": device, "backend": "faster-whisper", "cpu_backend": "gigaam"}}), encoding="utf-8")
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)


# --- причина «auto → процессор» ------------------------------------------------


def test_cuda_engine_without_card_says_why_it_recognises_on_cpu(monkeypatch, tmp_path, capsys):
    """Карта отключена (внешняя видеокарта без дока): процессор, и причина —
    одной строкой в вывод процесса, один раз, а не на каждый вызов."""
    _engine(monkeypatch, tmp_path, gpu=False)
    assert asr.resolve_device() == "cpu"
    assert asr.resolve_device() == "cpu"
    assert asr.cpu_reason() == asr.NO_GPU
    out = capsys.readouterr().out
    assert out.count("распознавание на процессоре: видеокарта NVIDIA не найдена") == 1


def test_cuda_engine_without_libraries_names_the_libraries(monkeypatch, tmp_path, capsys):
    _engine(monkeypatch, tmp_path, libs=False, gpu=True)
    assert asr.resolve_device() == "cpu"
    assert asr.cpu_reason() == asr.NO_CUDA_LIBS
    assert "распознавание на процессоре: не найдены или не загружаются библиотеки CUDA" in capsys.readouterr().out


def test_working_card_explicit_cpu_and_cpu_engine_are_not_fallbacks(monkeypatch, tmp_path, capsys):
    _engine(monkeypatch, tmp_path, gpu=True)
    assert asr.resolve_device() == "cuda" and asr.cpu_reason() is None
    _engine(monkeypatch, tmp_path, gpu=False, device="cpu")
    assert asr.resolve_device() == "cpu" and asr.cpu_reason() is None  # выбор человека
    asr._reset_cuda_state()
    _engine(monkeypatch, tmp_path / "cpu", profile="cpu", gpu=True)
    assert asr.resolve_device() == "cpu" and asr.cpu_reason() is None  # так задуман профиль CPU
    assert "распознавание на процессоре" not in capsys.readouterr().out


def test_dev_environment_without_cuda_packages_is_not_a_gpu_engine(monkeypatch, tmp_path):
    _engine(monkeypatch, tmp_path, profile=None, libs=False)
    assert not asr.gpu_engine()
    assert asr.resolve_device() == "cpu" and asr.cpu_reason() is None


def test_cuda_failure_in_this_process_is_the_reason(monkeypatch, tmp_path):
    _engine(monkeypatch, tmp_path, gpu=True)
    asr.cuda_failed(RuntimeError("Library cublas64_12.dll is not found or cannot be loaded"))
    assert asr.cpu_reason() == asr.CUDA_FAILED


def test_choose_carries_the_reason_with_gigaam_on_cpu(monkeypatch, tmp_path):
    _engine(monkeypatch, tmp_path, gpu=False)
    choice = asr.choose()
    assert (choice.backend, choice.device, choice.cpu_reason) == ("gigaam", "cpu", asr.NO_GPU)
    _engine(monkeypatch, tmp_path, gpu=True)
    assert asr.choose().cpu_reason is None


# --- torch загрузил свои cuBLAS/cuDNN раньше проверки ------------------------------


def test_library_already_loaded_by_torch_counts_as_loadable(monkeypatch, tmp_path):
    """`import torch` грузит torch/lib/cublas64_12.dll; вторая копия того же
    имени из пакета nvidia-cublas падает с WinError 127 (её cublasLt
    связывается с уже загруженным, старым). Это не «нет библиотек»: ctranslate2
    возьмёт уже загруженную по имени."""
    import ctypes

    def refuse(path):
        raise OSError("[WinError 127] Не найдена указанная процедура")

    monkeypatch.setattr(ctypes, "WinDLL", refuse, raising=False)
    libs = [tmp_path / "cublas64_12.dll", tmp_path / "cudnn64_9.dll"]
    monkeypatch.setattr(asr, "_module_loaded", lambda name: True)
    assert asr._libraries_load(libs)
    monkeypatch.setattr(asr, "_module_loaded", lambda name: False)
    assert not asr._libraries_load(libs)


# --- torch: видеокарта, когда torch её видит --------------------------------------


def _fake_torch(monkeypatch, cuda: bool):
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: cuda)
    torch.device = lambda name: types.SimpleNamespace(type=name)
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


def test_torch_device_on_auto_ignores_how_text_is_recognised(monkeypatch, tmp_path):
    """«Авто», текст — GigaAM на процессоре (у ctranslate2 нет библиотек), а
    torch видит карту: диаризация и голоса — на видеокарте."""
    _engine(monkeypatch, tmp_path, libs=False, gpu=False)
    _fake_torch(monkeypatch, cuda=True)
    assert asr.resolve_device() == "cpu"
    assert asr.torch_device() == "cuda"
    _fake_torch(monkeypatch, cuda=False)
    assert asr.torch_device() == "cpu"


def test_explicit_cpu_setting_keeps_torch_on_cpu(monkeypatch, tmp_path):
    """«Процессор» в настройках — всё на процессоре, хотя torch карту видит;
    «Видеокарта» — torch на ней."""
    _engine(monkeypatch, tmp_path, gpu=True, device="cpu")
    _fake_torch(monkeypatch, cuda=True)
    assert asr.torch_device() == "cpu"
    _engine(monkeypatch, tmp_path, gpu=True, device="cuda")
    assert asr.torch_device() == "cuda"


def test_torch_device_cpu_engine_failure_and_missing_torch(monkeypatch, tmp_path):
    _engine(monkeypatch, tmp_path, profile="cpu")
    _fake_torch(monkeypatch, cuda=True)
    assert asr.torch_device() == "cpu"  # профиль CPU: torch процессорный
    _engine(monkeypatch, tmp_path / "cuda", profile="cuda")
    assert asr.torch_device() == "cuda"
    asr.torch_cuda_failed(RuntimeError("Could not load library cudnn_ops64_9.dll"))
    assert asr.torch_device() == "cpu"
    assert asr._cuda_failure is None  # сбой torch не трогает ctranslate2
    asr._reset_cuda_state()
    monkeypatch.setitem(sys.modules, "torch", None)
    assert asr.torch_device() == "cpu"


@pytest.mark.parametrize("device, want", [("auto", "cuda"), ("cpu", "cpu")])
def test_diarization_follows_torch_on_auto_and_setting_on_cpu(monkeypatch, tmp_path, device, want):
    """«Авто»: текст на процессоре (нет библиотек ctranslate2), диаризация — на
    видеокарте. «Процессор» в настройках: диаризация тоже на процессоре."""
    from meet import credentials, diarize

    _engine(monkeypatch, tmp_path, libs=False, device=device)
    _fake_torch(monkeypatch, cuda=True)
    asked = []

    class Pipe:
        def to(self, device):
            self.device = device

        def __call__(self, *a, **k):
            return None

    def pick(torch, use_cuda):
        asked.append(use_cuda)
        return types.SimpleNamespace(type="cuda" if use_cuda else "cpu")

    monkeypatch.setattr(credentials, "get_hf_token", lambda: "hf_x")
    monkeypatch.setattr(diarize, "_load_pipeline", lambda token: Pipe())
    monkeypatch.setattr(diarize, "pick_device", pick)
    monkeypatch.setattr(diarize, "_load_wav", lambda path: (None, 16000))
    monkeypatch.setattr(diarize, "_to_diarization", lambda result, exclusive=False: Diarization(turns=[]))
    diar = diarize.diarize_wav(tmp_path / "x.wav")
    assert asked == [want == "cuda"] and diar.device == want


@pytest.mark.parametrize("device, want", [("auto", "cuda"), ("cpu", "cpu")])
def test_voice_embedder_and_alignment_follow_torch(monkeypatch, tmp_path, device, want):
    from meet import align, voice_id

    _engine(monkeypatch, tmp_path, libs=False, device=device)
    _fake_torch(monkeypatch, cuda=True)
    assert voice_id._embedder_device() == want
    assert align._align_device() == want


@pytest.mark.parametrize("device, want", [("auto", "cuda"), ("cpu", "cpu")])
def test_speaker_split_embedder_follows_torch(monkeypatch, tmp_path, device, want):
    from meet import segvoices

    _engine(monkeypatch, tmp_path, libs=False, device=device)
    _fake_torch(monkeypatch, cuda=True)
    devices = []
    for name in ("pyannote", "pyannote.audio", "pyannote.audio.pipelines"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sv = types.ModuleType("pyannote.audio.pipelines.speaker_verification")
    sv.PretrainedSpeakerEmbedding = lambda spec, device, token=None: devices.append(device.type)
    monkeypatch.setitem(sys.modules, "pyannote.audio.pipelines.speaker_verification", sv)
    monkeypatch.setattr("meet.credentials.get_hf_token", lambda: "t")
    segvoices.load_embedder()
    assert devices == [want]


# --- расшифровка: журнал, ход и пометка транскрипта --------------------------------


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    import meet.transcribe as tr

    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "transcribe_wav", lambda path, hotwords=None, **kw: [
        Segment(0.0, 1.0, "гигаам", words=[Word(0.0, 1.0, " гигаам")])])
    monkeypatch.setattr(tr, "_maybe_align", lambda segments, wav, enabled, **kw: segments)
    monkeypatch.setattr(tr, "diarize_wav", lambda p, num_speakers=None, exclusive=False, **kw: Diarization(
        turns=[(0.0, 5.0, "SPEAKER_00")], device="cuda"))
    monkeypatch.setattr(tr, "_match_names", lambda diar, threshold=None: {})
    monkeypatch.setattr(tr, "wav_seconds", lambda wav: 60.0)
    bus = events.EventBus()
    seen = []
    bus.subscribe(lambda event: seen.append(event.to_dict()))
    folder = tmp_path / "2026-10-05_13-33"
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    return tr, bus, seen, folder


def test_job_on_cpu_without_card_logs_warns_and_marks_transcript(monkeypatch, tmp_path, pipeline):
    tr, bus, seen, folder = pipeline
    _engine(monkeypatch, tmp_path, gpu=False)
    tr.transcribe(str(folder), align=True, bus=bus)
    logs = [e["text"] for e in seen if e["kind"] == "log" and e.get("source") == "device"]
    assert logs == ["распознавание на процессоре: видеокарта NVIDIA не найдена (не подключена или выключена)"]
    progress = [e for e in seen if e["kind"] == "progress"]
    first = next(i for i, e in enumerate(progress) if e.get("warning"))
    # С выбора движка (начало распознавания) — в каждом событии хода до конца.
    assert progress[first]["stage"] == "asr"
    assert {e.get("warning") for e in progress[first:]} == {
        "Распознаётся на процессоре: видеокарта NVIDIA не найдена (не подключена или выключена)"}
    data = library.read_transcript(folder)
    assert data["asr"]["device"] == "cpu" and data["asr_note"] == asr.NO_GPU
    assert library.describe(folder).to_raw()["asr_note"] == "no_gpu"


def test_job_on_gpu_has_no_warning_or_note(monkeypatch, tmp_path, pipeline):
    tr, bus, seen, folder = pipeline
    _engine(monkeypatch, tmp_path, gpu=True)
    tr.transcribe(str(folder), align=False, bus=bus)
    assert not [e for e in seen if e["kind"] == "log" and e.get("source") == "device"]
    assert not [e for e in seen if e.get("warning")]
    assert "asr_note" not in library.read_transcript(folder)


def test_explicit_cpu_estimates_torch_steps_on_cpu(monkeypatch, tmp_path, pipeline):
    """«Процессор» в настройках, карта видна: оценка шагов torch — процессор."""
    tr, bus, seen, folder = pipeline
    _engine(monkeypatch, tmp_path, gpu=True, device="cpu")
    run = tr._Run(bus=bus)
    run.choice = asr.Choice("gigaam", "cpu")
    assert run.device_of("diarize") == "cpu"
    _engine(monkeypatch, tmp_path, gpu=True, device="auto")
    assert run.device_of("diarize") == "cuda"


def test_stats_of_torch_steps_go_under_the_device_torch_used(monkeypatch, tmp_path, pipeline):
    """Текст на процессоре, диаризация на видеокарте: поправка времени
    диаризации — под «cuda», иначе она испортила бы ожидания процессора."""
    from meet.progress import StepStats

    tr, bus, seen, folder = pipeline
    _engine(monkeypatch, tmp_path, gpu=False)
    tr.transcribe(str(folder), align=False, bus=bus)
    keys = set(json.loads(StepStats().path.read_text(encoding="utf-8")))
    assert "cuda:any:diarize" in keys and "cpu:any:diarize" not in keys
    assert "cpu:gigaam:asr" in keys


# --- очередь задач: предупреждение и строка журнала ----------------------------------


def test_queue_keeps_the_warning_and_logs_device_lines(tmp_path, capsys):
    import time

    folder = tmp_path / "2026-10-05_13-33"
    text = "Распознаётся на процессоре: видеокарта NVIDIA не найдена"
    lines = [
        {"kind": "log", "text": "распознавание на процессоре: видеокарта NVIDIA не найдена", "source": "device"},
        {"kind": "progress", "stage": "asr", "label": "распознавание", "warning": text},
    ]
    captured = []

    def spawn(job, on_line):
        for line in lines:
            on_line(json.dumps(line))
        captured.append(job.to_raw()["warning"])
        job.result = "x"
        return 0

    queue = jobs.JobQueue(spawn=spawn)
    job = queue.submit(jobs.TRANSCRIBE, str(folder))
    try:
        deadline = time.time() + 5
        while queue.get(job.id).state != jobs.DONE and time.time() < deadline:
            time.sleep(0.01)
    finally:
        queue.stop()
    assert captured == [text]
    assert "задача transcribe (2026-10-05_13-33): распознавание на процессоре" in capsys.readouterr().out
