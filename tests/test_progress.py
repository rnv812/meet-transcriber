"""Ход долгих задач одной шкалой (meet.progress) и его источники: распознавание,
диаризация, загрузка моделей, очередь задач."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from meet import events, jobs
from meet.progress import Stages, Step


def _bus():
    bus = events.EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(e.data) if e.kind == "progress" else None)
    return bus, seen


def _plan():
    return [Step("convert", "convert", 10), Step("asr", "asr", 60, measured=True),
            Step("diarize", "diarize", 30)]


def test_steps_carry_number_count_and_weighted_fraction():
    bus, seen = _bus()
    st = Stages(bus, _plan(), clock=lambda: 0.0)
    st.begin("convert")
    st.begin("asr")
    assert seen[-1]["step"] == 2 and seen[-1]["steps"] == 3
    assert seen[-1]["fraction"] == 0.1  # конвертация (вес 10 из 100) пройдена
    assert seen[-1]["done"] == 0 and seen[-1]["total"] == 1  # у распознавания своя шкала
    st.begin("diarize")
    assert seen[-1]["done"] is None and seen[-1]["total"] is None  # своей шкалы нет — блик в окне
    st.finish()
    assert seen[-1]["fraction"] == 1.0


def test_updates_are_thinned_and_never_go_back():
    bus, seen = _bus()
    now = [0.0]
    st = Stages(bus, _plan(), clock=lambda: now[0])
    st.begin("asr")
    n = len(seen)
    st.update(0.001)  # мельче MIN_PART — не событие
    now[0] = 0.1
    st.update(0.3)  # раньше MIN_GAP_S — не событие
    assert len(seen) == n
    now[0] = 1.0
    st.update(0.4)
    assert seen[-1]["done"] == 0.4
    now[0] = 2.0
    st.update(0.2)  # запоздалое меньшее значение не откатывает шаг
    assert st.part == 0.4
    st.update(1.0)  # конец шага — всегда событие
    assert seen[-1]["done"] == 1.0


def test_dropping_a_future_step_moves_the_fraction_forward_only():
    bus, seen = _bus()
    st = Stages(bus, _plan(), clock=lambda: 0.0)
    st.begin("asr")
    st.update(1.0)
    before = st.fraction()
    st.drop("diarize")
    assert st.fraction() > before
    st.drop("asr")  # начатый шаг не убирается
    assert st.has("asr")


def test_estimate_goes_into_events():
    bus, seen = _bus()
    st = Stages(bus, _plan(), clock=lambda: 0.0)
    st.estimate(812.34)
    st.begin("convert")
    assert seen[-1]["estimate_s"] == 812.3
    st.estimate(0)  # пустая оценка не затирает прежнюю
    assert st.estimate_s == 812.34


def test_whisper_progress_follows_segment_ends():
    from meet import asr

    got = []
    raw = [SimpleNamespace(end=e) for e in (10.0, 30.0, 60.0)]
    assert list(asr._tracked(raw, 60.0, got.append)) == raw
    assert got == [10 / 60, 0.5, 1.0]
    assert list(asr._tracked(raw, 0, got.append)) == raw  # длительность неизвестна — молчим
    assert len(got) == 3


def test_gigaam_choice_reports_chunks(monkeypatch, tmp_path):
    from meet import asr, gigaam_asr

    got = []

    def fake(path, *, model_name, device, on_chunk=None):
        on_chunk(1, 4)
        on_chunk(4, 4)
        return []

    monkeypatch.setattr(gigaam_asr, "transcribe", fake)
    asr.transcribe_wav(tmp_path / "a.wav", choice=asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"), on_progress=got.append)
    assert got == [0.25, 1.0]


def test_pyannote_hook_maps_steps_to_one_scale():
    from meet.diarize import progress_hook

    got = []
    hook = progress_hook(got.append)
    hook("segmentation", None, total=10, completed=5)
    hook("speaker_counting", None)
    hook("embeddings", None, total=4, completed=4)
    hook("discrete_diarization", None)
    hook("unknown_step", None, total=1, completed=1)  # неизвестный шаг — без отметки
    assert got == [0.15, 0.3, 0.95, 0.97]
    assert got == sorted(got)


def test_hook_is_passed_only_to_pipelines_that_take_it():
    from meet.diarize import _takes_hook

    class New:
        def apply(self, file, num_speakers=None, hook=None):
            pass

    class Old:
        def apply(self, file, num_speakers=None):
            pass

    assert _takes_hook(New()) and not _takes_hook(Old())


def test_byte_watch_reports_growth_and_caps_below_full():
    from meet.job_worker import ByteWatch

    sizes = iter([0, 0, 500, 1500])
    got = []
    watch = ByteWatch(lambda: next(sizes), 1000, lambda done, total: got.append((done, total)), interval=60,
                      cap=990)
    watch.tick()
    watch.tick()  # не изменилось — не событие
    watch.tick()
    watch.tick()
    assert got == [(0, 1000), (500, 1000), (990, 1000)]  # полная — только после загрузчика


def test_download_model_job_emits_byte_progress(monkeypatch, capsys):
    from meet import job_worker, models

    sizes = iter([0, 0, 400, 1000, 1000, 1000, 1000])
    monkeypatch.setattr(models, "size_on_disk", lambda repo: next(sizes, 1000))
    monkeypatch.setattr(models, "download_total", lambda repo: 1000)

    def fake_download(repo, on_line=None):
        time.sleep(0.05)
        return 0

    monkeypatch.setattr(models, "download", fake_download)
    monkeypatch.setattr(job_worker.ByteWatch, "__init__",
                        lambda self, m, t, r, interval=0.5, cap=None, _orig=job_worker.ByteWatch.__init__: _orig(self, m, t, r, 0.01, cap))
    assert job_worker._download_model("Systran/faster-whisper-small") == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    progress = [x for x in lines if x["kind"] == "progress"]
    assert progress[0]["step"] == 1 and progress[0]["steps"] == 1
    measured = [x for x in progress if x.get("total") == 1000]
    assert measured[0]["done"] == 0 and measured[-1]["done"] == 1000
    assert measured[-1]["fraction"] == 1.0
    assert all(x["done"] <= 990 for x in measured[:-1])  # 100 % — только когда загрузка завершилась
    assert [x["done"] for x in measured] == sorted(x["done"] for x in measured)


def test_update_of_a_downloaded_model_counts_growth_not_what_is_on_disk(monkeypatch, capsys):
    """«Обновить» скачанную модель: на диске уже всё — шкала не стоит полной,
    а показывает «неизвестно» (докачивать по оценке нечего)."""
    from meet import job_worker, models

    monkeypatch.setattr(models, "size_on_disk", lambda repo: 1000)
    monkeypatch.setattr(models, "download_total", lambda repo: 1000)
    monkeypatch.setattr(models, "download", lambda repo, on_line=None: 0)
    assert job_worker._download_model("Systran/faster-whisper-small") == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    progress = [x for x in lines if x["kind"] == "progress"]
    assert all(x.get("total") is None and x.get("fraction") is None for x in progress)


def test_emit_writes_whole_lines_from_several_threads(capsys):
    import threading

    from meet import job_worker

    def burst(k):
        for i in range(200):
            job_worker._emit({"kind": "log", "text": f"{k}-{i}-" + "ж" * 50})

    threads = [threading.Thread(target=burst, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 800
    assert all(json.loads(x)["kind"] == "log" for x in out)


def test_download_total_gigaam_and_catalogue_fallback(monkeypatch):
    from meet import gigaam_asr, models

    name = next(iter(gigaam_asr.FILES))
    assert models.download_total(models.GIGAAM_PREFIX + name) == sum(s for _, s, _, _ in gigaam_asr.FILES[name])
    # Без huggingface_hub (или без связи) — размер из каталога.
    entry = next(m for m in models.CATALOGUE if not m["id"].startswith(models.GIGAAM_PREFIX))
    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub", None)
    assert models.download_total(entry["id"]) == int(entry["size_gb"] * 1e9)


def test_queue_keeps_step_fields_of_the_job(tmp_path):
    bus = events.EventBus()

    def spawn(job, on_line):
        on_line(json.dumps({"kind": "progress", "stage": "diarize", "label": "диаризация", "done": None,
                            "total": None, "note": "sys", "step": 4, "steps": 6, "fraction": 0.62,
                            "estimate_s": 300.0}))
        on_line(json.dumps({"kind": "job.result", "path": str(tmp_path / "т.md")}))
        return 0

    queue = jobs.JobQueue(bus, spawn=spawn)
    job = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    try:
        deadline = time.monotonic() + 5
        while queue.get(job.id).state != jobs.DONE and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        queue.stop()
    raw = queue.get(job.id).to_raw()
    assert (raw["step"], raw["steps"], raw["fraction"], raw["estimate_s"]) == (4, 6, 0.62, 300.0)


def test_cli_prints_step_starts_and_the_final_line_only(capsys):
    from meet.cli_library import _progress_printer

    emit = _progress_printer([])
    bus, _ = _bus()
    bus.subscribe(lambda e: emit(e.to_dict()))
    st = Stages(bus, [Step("asr", "asr", 1, measured=True), Step("render", "render", 1)], clock=lambda: 0.0)
    st.begin("asr")
    st.update(0.5)
    st.begin("render")
    st.finish(note="C:/rec/x_transcript.md")
    err = capsys.readouterr().err.splitlines()
    assert err == ["распознавание", "сборка транскрипта", "сборка транскрипта · C:/rec/x_transcript.md"]


# --- 0.3.1: ровный ход внутри этапов ---------------------------------------------


def test_soft_estimate_is_monotonic_and_never_claims_completion():
    from meet.progress import soft

    xs = [i / 10 for i in range(0, 80)]
    ys = [soft(x) for x in xs]
    assert ys == sorted(ys)
    assert soft(0) == 0 and soft(0.5) == pytest.approx(0.45) and soft(1.0) == pytest.approx(0.9)
    assert max(ys) < 0.95 and soft(1000) <= 0.95


def test_updates_are_throttled_to_four_per_second():
    bus, seen = _bus()
    now = [0.0]
    st = Stages(bus, _plan(), clock=lambda: now[0])
    st.begin("asr")
    n = len(seen)
    for i in range(1, 101):  # сотня обновлений за секунду (кусок за куском)
        now[0] = i / 100
        st.update(i / 200)
    assert len(seen) - n <= 4
    assert [e["fraction"] for e in seen] == sorted(e["fraction"] for e in seen)


def test_cap_is_the_end_of_the_current_step():
    bus, seen = _bus()
    st = Stages(bus, _plan(), clock=lambda: 0.0)
    st.begin("convert")
    assert seen[-1]["cap"] == 0.1
    st.begin("asr")
    assert seen[-1]["cap"] == 0.7 and seen[-1]["fraction"] == 0.1
    st.finish()
    assert "cap" not in seen[-1]


def test_step_without_own_scale_goes_by_time_and_stops_short_of_full():
    bus, seen = _bus()
    now = [0.0]
    st = Stages(bus, _plan(), clock=lambda: now[0])
    st.estimate(100.0)  # диаризация весит 30 из 100 — ожидается 30 с
    st.begin("convert")
    st.begin("asr")
    st.update(1.0)
    st.begin("diarize")
    st.update(None)  # pyannote без hook: своей шкалы не будет
    parts = []
    for t in range(1, 200):
        now[0] = float(t)
        st.tick()
        parts.append(st.part)
    assert parts == sorted(parts)
    assert parts[14] == pytest.approx(0.45, abs=0.01)  # половина ожидаемого — 45 % шага
    assert max(parts) < 0.95  # дольше ожидаемого — медленно, но не «готово»
    assert seen[-1]["unit"] == "time"


def test_measured_step_is_not_ticked_unless_it_says_it_has_no_scale():
    bus, seen = _bus()
    now = [0.0]
    st = Stages(bus, _plan(), clock=lambda: now[0])
    st.estimate(100.0)
    st.begin("asr")
    now[0] = 10.0
    st.tick()
    assert st.part == 0.0  # у распознавания своя шкала — по времени не продлеваем
    st.update(None)  # «шкалы не будет»
    st.tick()
    assert st.part > 0 and seen[-1]["unit"] == "time"
    st.update(0.8)  # своя шкала всё же пришла — дальше по ней, без отката
    assert st.part >= 0.8
    now[0] = 50.0
    before = st.part
    st.tick()
    assert st.part == before


def test_unmeasured_step_is_ticked_from_its_start():
    bus, seen = _bus()
    now = [0.0]
    st = Stages(bus, _plan(), clock=lambda: now[0])
    st.estimate(100.0)  # конвертация: 10 из 100 — ожидается 10 с
    st.begin("convert")
    now[0] = 5.0
    st.tick()
    assert st.part == pytest.approx(0.45)
    assert seen[-1]["fraction"] == pytest.approx(0.045) and seen[-1]["unit"] == "time"


def test_ticking_thread_emits_while_work_runs():
    bus, seen = _bus()
    st = Stages(bus, [Step("convert", "convert", 1), Step("voices", "voices", 1)])
    st.estimate(0.4)
    with st.ticking(interval=0.02):
        st.begin("convert")
        time.sleep(0.25)
    timed = [e for e in seen if e.get("unit") == "time"]
    assert len(timed) >= 2
    assert [e["fraction"] for e in seen] == sorted(e["fraction"] for e in seen)


def test_reweight_moves_weight_between_pending_steps_without_moving_the_fraction():
    bus, seen = _bus()
    st = Stages(bus, [Step("convert", "convert", 10), Step("asr-sys", "asr", 33), Step("diarize", "diarize", 24),
                      Step("asr-mic", "asr", 33)], clock=lambda: 0.0)
    st.begin("convert")
    st.update(1.0)
    before = st.fraction()
    st.reweight({"asr-sys": 3.0, "asr-mic": 1.0})
    assert st.fraction() == before
    weights = {s.key: s.weight for s in st.steps}
    assert weights["asr-sys"] == pytest.approx(49.5) and weights["asr-mic"] == pytest.approx(16.5)


def test_two_tracks_share_recognition_by_their_length(tmp_path):
    from meet.transcribe import _two_track_plan, _weigh_tracks

    sys_wav, mic_wav = tmp_path / "s.wav", tmp_path / "m.wav"
    sys_wav.write_bytes(b"\0" * (44 + 32000 * 60))  # минута собеседников, полминуты микрофона
    mic_wav.write_bytes(b"\0" * (44 + 32000 * 30))
    bus, _ = _bus()
    st = Stages(bus, _two_track_plan(False))
    _weigh_tracks(st, sys_wav, mic_wav)
    w = {s.key: s.weight for s in st.steps}
    assert w["asr-sys"] / w["asr-mic"] == pytest.approx((0.6 * 60) / (0.4 * 30))
    assert w["asr-sys"] + w["asr-mic"] == pytest.approx(55.0)


def test_gigaam_reports_progress_in_chunk_seconds(tmp_path):
    import types
    import wave

    import numpy as np

    from meet import gigaam_asr

    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes((np.zeros(16000 * 40)).astype(np.int16).tobytes())

    class Model:
        def transcribe(self, path, word_timestamps=False):
            return types.SimpleNamespace(text="", words=[])

    got = []
    # Два куска: 20 с и 4 с — первый весит впятеро больше.
    gigaam_asr.transcribe(wav, regions=[(0.0, 20.0), (30.0, 34.0)], model=Model(),
                          on_chunk=lambda done, total: got.append(round(done / total, 3)))
    assert got == [0.0, round(20 / 24, 3), 1.0]


def test_alignment_reports_progress_per_segment(monkeypatch):
    import sys
    import types

    from meet import align
    from meet.asr import Segment

    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(empty_cache=lambda: None))
    functional = types.ModuleType("torchaudio.functional")
    torchaudio = types.ModuleType("torchaudio")
    torchaudio.functional = functional
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "torchaudio", torchaudio)
    monkeypatch.setitem(sys.modules, "torchaudio.functional", functional)
    tok = types.SimpleNamespace(get_vocab=lambda: {}, pad_token_id=0)
    monkeypatch.setattr(align, "_load_align_model", lambda device: (types.SimpleNamespace(tokenizer=tok), object()))

    class Wav:
        def __enter__(self):
            return types.SimpleNamespace(getframerate=lambda: 16000, readframes=lambda n: b"\0\0" * 16000,
                                         getnframes=lambda: 16000)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("wave.open", lambda *a, **k: Wav())
    segs = [Segment(0.0, 1.0, "а"), Segment(1.0, 4.0, "б"), Segment(4.0, 5.0, "в")]  # без слов — как есть
    got = []
    assert align.align_segments(segs, "x.wav", device="cpu", on_progress=got.append) == segs
    assert got == [0.0, 0.2, 0.8, 1.0]


def test_diarization_without_hook_says_it_has_no_scale(monkeypatch, tmp_path):
    import sys
    import types

    from meet import credentials, diarize

    class Pipe:
        def to(self, device):
            pass

        def apply(self, file, num_speakers=None):  # без hook
            pass

        def __call__(self, file, **kw):
            assert "hook" not in kw
            return object()

    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(credentials, "get_hf_token", lambda: "hf_x")
    monkeypatch.setattr(diarize, "_load_pipeline", lambda token: Pipe())
    monkeypatch.setattr(diarize, "pick_device", lambda torch, use_cuda: types.SimpleNamespace(type="cpu"))
    monkeypatch.setattr(diarize, "_load_wav", lambda path: (None, 16000))
    monkeypatch.setattr(diarize, "_to_diarization", lambda result, exclusive=False: diarize.Diarization(turns=[]))
    got = []
    diarize.diarize_wav(tmp_path / "x.wav", on_progress=got.append)
    assert got == [None]


def test_two_track_transcription_progress_is_monotonic_with_constant_step_count(monkeypatch, tmp_path):
    import meet.transcribe as tr
    from meet.asr import Segment
    from meet.diarize import Diarization

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)

    def fake_asr(path, hotwords=None, *, on_progress=None, **kw):
        for i in range(1, 11):
            on_progress(i / 10)
        return [Segment(0.0, 1.0, "а")]

    def fake_diarize(p, num_speakers=None, exclusive=False, on_progress=None, **kw):
        for x in (0.1, 0.3, 0.6, 0.97):
            on_progress(x)
        return Diarization(turns=[(0.0, 1.0, "SPEAKER_00")])

    monkeypatch.setattr(tr, "transcribe_wav", fake_asr)
    monkeypatch.setattr(tr, "diarize_wav", fake_diarize)
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled, **kw: s)
    monkeypatch.setattr(tr, "_match_names", lambda diar, threshold=None: {})
    (tmp_path / "sys.opus").write_bytes(b"x")
    (tmp_path / "mic.opus").write_bytes(b"x")
    bus, seen = _bus()
    tr.transcribe(str(tmp_path), align=False, bus=bus)
    fractions = [e["fraction"] for e in seen]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert len({e["steps"] for e in seen}) == 1
    assert all(e["fraction"] <= e["cap"] + 1e-9 for e in seen if "cap" in e)
    assert any(e.get("unit") == "audio_s" for e in seen)


def test_import_copy_reports_bytes(tmp_path, monkeypatch, capsys):
    from meet import job_worker, library

    src = tmp_path / "in" / "встреча.mp3"
    src.parent.mkdir()
    src.write_bytes(b"m" * (3 * 1024 + 5))
    folder = library.create_import(tmp_path / "rec", src)
    monkeypatch.setattr(job_worker, "COPY_CHUNK", 1024)
    monkeypatch.setattr(job_worker, "COPY_GAP_S", 0.0)
    assert job_worker._copy_import(str(folder)) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    progress = [x for x in lines if x["kind"] == "progress"]
    done = [x["done"] for x in progress]
    assert done == sorted(done) and done[0] == 0 and done[-1] == 3 * 1024 + 5
    assert len(done) >= 4 and all(x["unit"] == "bytes" for x in progress)
    assert (folder / "source.mp3").read_bytes() == src.read_bytes()


def test_merge_reports_a_step_per_track(tmp_path, monkeypatch):
    from meet import merge

    class Done:
        returncode = 0
        stderr = ""

    def run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"opus")
        return Done()

    monkeypatch.setattr(merge.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(merge, "_write_events", lambda *a, **k: None)
    monkeypatch.setattr(merge, "describe_part",
                        lambda source, probe=None, end=None: SimpleNamespace(start=0.0, duration_s=60.0))
    monkeypatch.setattr(merge, "output_roles", lambda parts: ["sys", "mic"])
    monkeypatch.setattr(merge, "pieces", lambda parts, role: [])
    monkeypatch.setattr(merge, "concat_command", lambda items, out: ["ffmpeg", str(out)])
    monkeypatch.setattr(merge, "parts_meta", lambda parts: [])
    monkeypatch.setattr(merge.library, "read_meta", lambda f: {"merged_from": ["a", "b"]})
    monkeypatch.setattr(merge.library, "update_meta", lambda f, fn: None)
    folder = tmp_path / "m"
    folder.mkdir()
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    bus, seen = _bus()
    merge.run(folder, run=run, bus=bus)
    assert [(e["step"], e["steps"]) for e in seen if not e.get("final")][:2] == [(1, 2), (2, 2)]
    assert seen[-1]["fraction"] == 1.0
    assert [e["fraction"] for e in seen] == sorted(e["fraction"] for e in seen)


# --- 0.3.1: веса шагов — по их ожидаемому времени ------------------------------------


def test_step_time_depends_on_engine_and_length():
    from meet.progress import step_time

    load, work = step_time("cpu", "gigaam", "diarize", 360)
    assert load > 0 and work == pytest.approx(0.12 * 360)
    # GigaAM на CPU распознаёт много быстрее Whisper — и вес у распознавания меньше.
    assert sum(step_time("cpu", "gigaam", "asr", 600)) < sum(step_time("cpu", "faster-whisper", "asr", 600))
    assert step_time("mps", "что-то", "asr", 10) == step_time("cpu", "faster-whisper", "asr", 10)


def test_cpu_diarization_after_the_0_3_3_speed_up(monkeypatch):
    """Модель с диска и голоса за один проход на окно: ~10 с загрузки и
    0,12 с на секунду звука вместо 50 и 0,27."""
    from meet import plat
    from meet.progress import step_time

    monkeypatch.setattr(plat, "is_macos", lambda: False)
    for backend in ("gigaam", "faster-whisper"):
        assert step_time("cpu", backend, "diarize", 600) == pytest.approx((10.0, 0.12 * 600))


def test_mps_is_its_own_profile(monkeypatch, tmp_path):
    """Mac (MPS) — свой профиль времени и свои поправки: его замеры больше не
    сдвигают ожидания процессора на Windows-подобном ключе `cpu`."""
    from meet import plat
    from meet.progress import StepStats, step_time

    monkeypatch.setattr(plat, "is_macos", lambda: False)
    mps, cpu = step_time("mps", "gigaam", "diarize", 600), step_time("cpu", "gigaam", "diarize", 600)
    assert mps[1] < cpu[1]
    assert step_time("mps", "gigaam", "asr", 600) == step_time("cpu", "gigaam", "asr", 600)  # текст — процессор
    stats = StepStats(tmp_path / "p.json")
    stats.record("mps", "gigaam", "diarize", (10.0, 30.0), (5.0, 15.0))
    assert stats.factors("cpu", "gigaam", "diarize") == (1.0, 1.0)
    assert stats.factors("mps", "gigaam", "diarize") == pytest.approx((0.5, 0.5))
    assert "mps:any:diarize" in stats._load()


def test_cpu_guess_for_diarization_on_a_mac_means_mps(monkeypatch):
    """До диаризации расшифровка знает только cuda/cpu, а на Mac pyannote идёт
    на MPS (diarize.pick_device): оценка и поправки — по профилю mps."""
    from meet import plat
    from meet.progress import step_time

    monkeypatch.setattr(plat, "is_macos", lambda: True)
    assert step_time("cpu", "gigaam", "diarize", 600) == step_time("mps", "gigaam", "diarize", 600)
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    assert step_time("cpu", "gigaam", "asr", 600) == step_time("mps", "gigaam", "asr", 600)


def test_plan_times_weighs_steps_by_time_without_moving_the_fraction():
    bus, seen = _bus()
    now = [0.0]
    st = Stages(bus, [Step("convert", "convert", 5), Step("asr", "asr", 55, measured=True),
                      Step("diarize", "diarize", 25, measured=True), Step("render", "render", 15)],
                clock=lambda: now[0])
    st.begin("convert")
    now[0] = 2.0
    st.update(1.0)
    st.begin("asr")
    before = st.fraction()
    st.plan_times({"asr": (10.0, 10.0), "diarize": (50.0, 20.0), "render": (0.5, 0.0)})
    assert st.fraction() == before
    w = {s.key: s.weight for s in st.steps}
    assert w["diarize"] / w["asr"] == pytest.approx(70 / 20)
    assert w["asr"] + w["diarize"] + w["render"] == pytest.approx(95.0)
    assert st.estimate_s == pytest.approx(2.0 + 90.5)


def test_measured_step_creeps_through_loading_then_follows_its_scale():
    bus, seen = _bus()
    now = [0.0]
    st = Stages(bus, [Step("diarize", "diarize", 1, measured=True), Step("render", "render", 0.01)],
                clock=lambda: now[0])
    st.begin("diarize")
    st.plan_times({"diarize": (30.0, 10.0), "render": (0.1, 0.0)})  # загрузка — 75 % шага
    parts = []
    for t in range(1, 61):  # pyannote молчит минуту (дольше ожидаемой загрузки)
        now[0] = float(t)
        st.tick()
        parts.append(st.part)
    assert parts == sorted(parts) and parts[14] == pytest.approx(0.75 * 0.45, abs=0.01)
    assert max(parts) < 0.75  # загрузка не заходит на работу
    assert seen[-1]["unit"] == "time"
    st.update(0.0)  # первый отчёт pyannote — конец загрузки
    assert st.part == pytest.approx(0.75)
    st.update(0.5)
    assert st.part == pytest.approx(0.875)
    now[0] = 100.0
    st.tick()
    assert st.part == pytest.approx(0.875)  # дальше — только своя шкала


def test_step_times_are_learned_from_finished_runs(tmp_path):
    from meet.progress import StepStats, step_time

    stats = StepStats(tmp_path / "p.json")
    assert stats.factors("cpu", "gigaam", "diarize") == (1.0, 1.0)
    for _ in range(3):  # загрузка вдвое быстрее таблицы, работа — в полтора раза дольше
        stats.record("cpu", "gigaam", "diarize", (50.0, 16.0), (25.0, 24.0))
    stats.record("cpu", "gigaam", "diarize", (50.0, 16.0), (None, 40.0))  # без своей шкалы — только работа
    stats.save()
    again = StepStats(tmp_path / "p.json")
    k_load, k_work = again.factors("cpu", "faster-whisper", "diarize")  # диаризации движок не важен
    assert k_load == pytest.approx(0.5) and k_work == pytest.approx(1.5)
    base = step_time("cpu", "gigaam", "diarize", 60)
    assert step_time("cpu", "gigaam", "diarize", 60, again) == pytest.approx((base[0] * 0.5, base[1] * 1.5))
    assert again.factors("cpu", "gigaam", "asr") == (1.0, 1.0)


def test_transcription_learns_step_times(monkeypatch, tmp_path):
    import meet.transcribe as tr
    from meet import asr
    from meet.asr import Segment
    from meet.diarize import Diarization
    from meet.progress import StepStats

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))

    def to_wav(src, dst, **k):
        Path(dst).write_bytes(b"\0" * (44 + 32000 * 60))
        return dst

    def fake_asr(path, hotwords=None, *, on_progress=None, **kw):
        on_progress(0.0)
        on_progress(1.0)
        return [Segment(0.0, 1.0, "а")]

    def fake_diarize(p, num_speakers=None, exclusive=False, on_progress=None, **kw):
        on_progress(0.5)
        return Diarization(turns=[(0.0, 1.0, "SPEAKER_00")])

    monkeypatch.setattr(tr, "to_wav16k", to_wav)
    monkeypatch.setattr(asr, "choose", lambda path=None, **kw: asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"))
    monkeypatch.setattr(tr, "transcribe_wav", fake_asr)
    monkeypatch.setattr(tr, "diarize_wav", fake_diarize)
    monkeypatch.setattr(tr, "_restore_latin", lambda segments, run: None)
    monkeypatch.setattr(tr, "_match_names", lambda diar, threshold=None: {})
    (tmp_path / "sys.opus").write_bytes(b"x")
    (tmp_path / "mic.opus").write_bytes(b"x")
    bus, seen = _bus()
    tr.transcribe(str(tmp_path), align=False, bus=bus)
    # Веса по времени: на CPU с GigaAM диаризация тяжелее распознавания.
    diarize_at = next(e for e in seen if e["stage"] == "diarize")
    asr_at = next(e for e in seen if e["stage"] == "asr")
    assert diarize_at["cap"] - diarize_at["fraction"] > asr_at["cap"] - asr_at["fraction"]
    assert diarize_at["estimate_s"] > 0  # оценка всей работы — по времени шагов
    data = json.loads((tmp_path / "state" / StepStats.NAME).read_text(encoding="utf-8"))
    assert {"cpu:gigaam:asr", "cpu:any:diarize"} <= set(data)
