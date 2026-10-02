"""Ход долгих задач одной шкалой (meet.progress) и его источники: распознавание,
диаризация, загрузка моделей, очередь задач."""

import json
import time
from types import SimpleNamespace

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
