"""«Сначала текст, спикеры потом» (Р4, 0.3.3).

Распознавание кончается задолго до диаризации: текст встречи пишется в
transcript.json сразу (`phase: "text"`, без спикеров: микрофон — владелец,
собеседники — без подписи), окончательная расшифровка заменяет его целиком.
Всё, что работает на спикерах или отдаёт расшифровку наружу (анализ, итоги,
база знаний, «Улучшить расшифровку», правка спикеров), по черновику текста
не идёт. Модели не вызываются: распознавание и диаризация подменены."""

import json
import time
from pathlib import Path

import pytest

from meet import events, library
from meet.asr import Segment, Word
from meet.diarize import Diarization


# --- библиотека: пометка фазы --------------------------------------------------


def _text_phase(folder: Path, segments=None) -> None:
    library.write_transcript(folder, {
        "version": 1, "title": "Встреча", "phase": "text", "created_at": "2026-10-05T10:00:00",
        "segments": segments or [{"start": 0.0, "end": 1.0, "speaker": None, "text": "привет"}]})


def test_text_phase_is_recognised_and_is_not_final(tmp_path):
    _text_phase(tmp_path)
    data = library.read_transcript(tmp_path)
    assert library.is_text_phase(data)
    assert library.final_transcript(tmp_path) is None
    assert not library.is_text_phase({"segments": []})
    assert not library.is_text_phase(None)


def test_final_transcript_reads_ordinary_transcript(tmp_path):
    library.write_transcript(tmp_path, {"version": 1, "segments": []})
    assert library.final_transcript(tmp_path) == {"version": 1, "segments": []}
    assert library.final_transcript(tmp_path / "нет") is None


def test_card_says_transcript_is_text_only(tmp_path):
    folder = tmp_path / "2026-10-05_10-00"
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    _text_phase(folder)
    card = library.describe(folder)
    assert card.has_transcript and card.transcript_phase == "text"
    assert card.to_raw()["transcript_phase"] == "text"
    library.write_transcript(folder, {"version": 1, "segments": []})
    assert library.describe(folder).to_raw()["transcript_phase"] is None


# --- пайплайн: текст раньше спикеров -------------------------------------------


def _words(start: float, text: str) -> list[Word]:
    out, t = [], start
    for w in text.split():
        out.append(Word(t, t + 0.4, f" {w}"))
        t += 0.5
    return out


def _seg(start: float, text: str) -> Segment:
    words = _words(start, text)
    return Segment(start, words[-1].end, text, words=words)


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Двухдорожечная запись с заглушками: распознавание (sys — две реплики,
    mic — одна), диаризация — два спикера. `seen` — что лежало в папке, когда
    началась диаризация, и события шины."""
    import meet.transcribe as tr

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    folder = tmp_path / "recordings" / "2026-10-05_10-00"
    folder.mkdir(parents=True)
    for role in ("sys", "mic"):
        (folder / f"{role}.opus").write_bytes(b"x")
    seen: dict = {"order": []}

    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)

    def fake_asr(path, hotwords, **kw):
        role = "sys" if "sys" in Path(path).name else "mic" if "mic" in Path(path).name else "one"
        seen["order"].append(f"asr-{role}")
        if role == "mic":
            return [_seg(3.0, "да согласен")]
        return [_seg(0.0, "добрый день коллеги"), _seg(6.0, "тогда начнём")]

    def fake_diarize(path, num_speakers=None, exclusive=False, **kw):
        seen["order"].append("diarize")
        seen["at_diarize"] = library.read_transcript(folder)
        seen["words_at_diarize"] = (folder / library.WORDS_JSON).exists()
        seen["md_at_diarize"] = bool(list(folder.glob("*_transcript.md")))
        return Diarization(turns=[(0.0, 2.0, "SPEAKER_00"), (5.0, 8.0, "SPEAKER_01")])

    monkeypatch.setattr(tr, "transcribe_wav", fake_asr)
    monkeypatch.setattr(tr, "diarize_wav", fake_diarize)
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled, **kw: s)
    bus = events.EventBus()
    seen["events"] = []
    bus.subscribe(seen["events"].append)
    seen["folder"], seen["bus"], seen["tr"] = folder, bus, tr
    return seen


def test_two_track_writes_text_before_diarization(pipeline):
    tr, folder = pipeline["tr"], pipeline["folder"]
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    # Обе дорожки распознаны до диаризации: текст готов целиком.
    assert pipeline["order"] == ["asr-sys", "asr-mic", "diarize"]
    early = pipeline["at_diarize"]
    assert early["phase"] == "text"
    assert [s["text"] for s in early["segments"]] == ["добрый день коллеги", "да согласен", "тогда начнём"]
    # Микрофон — владелец, собеседники — без подписи (никаких «Спикер N»).
    assert [s["speaker"] for s in early["segments"]] == [None, "Вы", None]
    assert early["segments"][1]["track"] == "mic"
    assert early["track_marks"] == "pipeline"
    # Слова черновику не нужны: правка спикеров по нему не идёт.
    assert not pipeline["words_at_diarize"]
    assert not pipeline["md_at_diarize"]


def test_final_transcript_replaces_text_and_keeps_its_created_at(pipeline):
    tr, folder = pipeline["tr"], pipeline["folder"]
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    final = library.read_transcript(folder)
    assert "phase" not in final
    assert final["created_at"] == pipeline["at_diarize"]["created_at"]
    assert {s["speaker"] for s in final["segments"]} == {"Спикер 1", "Спикер 2", "Вы"}
    assert library.final_transcript(folder) == final
    assert (folder / library.WORDS_JSON).exists()
    assert list(folder.glob("*_transcript.md"))


def test_text_ready_event_goes_to_the_bus_once(pipeline):
    tr, folder = pipeline["tr"], pipeline["folder"]
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    ready = [e for e in pipeline["events"] if e.kind == events.TRANSCRIPT_TEXT]
    assert len(ready) == 1 and ready[0].data["path"] == str(folder)
    # Событие — после распознавания микрофона и до диаризации.
    kinds = [(e.kind, e.data.get("stage"), e.data.get("note")) for e in pipeline["events"]]
    at = kinds.index((events.TRANSCRIPT_TEXT, None, None))
    stages = [k[1] for k in kinds]
    assert "diarize" not in stages[:at]
    assert ("progress", "asr", "mic") in kinds[:at]


def test_retranscribe_keeps_the_old_final_until_the_new_one(pipeline):
    """Перерасшифровка готовой записи: прежняя расшифровка (с правками и
    историей) остаётся на экране до новой — отмена посередине её не теряет."""
    tr, folder = pipeline["tr"], pipeline["folder"]
    library.write_transcript(folder, {"version": 1, "created_at": "2026-01-01T00:00:00", "segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Анна", "text": "старое"}]})
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    assert pipeline["at_diarize"]["segments"][0]["text"] == "старое"
    assert not [e for e in pipeline["events"] if e.kind == events.TRANSCRIPT_TEXT]
    final = library.read_transcript(folder)
    assert final["created_at"] != "2026-01-01T00:00:00" and "phase" not in final


def test_interrupted_text_phase_is_rewritten_by_the_next_run(pipeline):
    tr, folder = pipeline["tr"], pipeline["folder"]
    _text_phase(folder, [{"start": 0.0, "end": 1.0, "speaker": None, "text": "прошлый черновик"}])
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    assert pipeline["at_diarize"]["segments"][0]["text"] == "добрый день коллеги"
    assert pipeline["at_diarize"]["created_at"] != "2026-10-05T10:00:00"


def test_replacement_rules_apply_to_the_text_phase_too(pipeline):
    state = Path(pipeline["folder"]).parents[1] / "state"
    state.mkdir(exist_ok=True)
    (state / "config.json").write_text(json.dumps(
        {"asr": {"replacements": [{"from": "коллеги", "to": "друзья"}]}}), encoding="utf-8")
    tr, folder = pipeline["tr"], pipeline["folder"]
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    assert pipeline["at_diarize"]["segments"][0]["text"] == "добрый день друзья"
    assert library.read_transcript(folder)["segments"][0]["text"] == "добрый день друзья"


def test_text_phase_write_failure_does_not_stop_transcription(pipeline, monkeypatch, capsys):
    tr, folder = pipeline["tr"], pipeline["folder"]
    real = library.write_transcript

    def flaky(path, data, words="keep"):
        if data.get("phase") == "text":
            raise OSError("диск занят")
        return real(path, data, words=words)

    monkeypatch.setattr(library, "write_transcript", flaky)
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    assert "phase" not in library.read_transcript(folder)
    assert "текст до спикеров не записан" in capsys.readouterr().out


def test_import_single_track_text_phase_has_no_speakers(monkeypatch, tmp_path):
    import meet.transcribe as tr

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    folder = tmp_path / "2026-10-05_11-00_import"
    folder.mkdir()
    (folder / "source.mp4").write_bytes(b"x")
    early = {}
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h, **kw: [_seg(0.0, "раз два"), _seg(4.0, "три")])

    def fake_diarize(path, num_speakers=None, exclusive=False, **kw):
        early.update(library.read_transcript(folder))
        return Diarization(turns=[(0.0, 9.0, "SPEAKER_00")])

    monkeypatch.setattr(tr, "diarize_wav", fake_diarize)
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled, **kw: s)
    tr.transcribe(str(folder), align=False)
    assert early["phase"] == "text"
    assert [s["speaker"] for s in early["segments"]] == [None, None]
    assert "track_marks" not in early
    assert {s["speaker"] for s in library.read_transcript(folder)["segments"]} == {"Спикер 1"}


def test_single_file_outside_a_folder_writes_no_text_phase(monkeypatch, tmp_path):
    import meet.transcribe as tr

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h, **kw: [_seg(0.0, "раз")])
    monkeypatch.setattr(tr, "diarize_wav", lambda p, **kw: Diarization(turns=[]))
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled, **kw: s)
    src = tmp_path / "a.wav"
    src.write_bytes(b"x")
    bus = events.EventBus()
    got = []
    bus.subscribe(got.append)
    tr.transcribe(str(src), align=False, bus=bus)
    assert not (tmp_path / "transcript.json").exists()
    assert not [e for e in got if e.kind == events.TRANSCRIPT_TEXT]


def test_merged_recording_text_phase_has_break_marks(pipeline):
    tr, folder = pipeline["tr"], pipeline["folder"]
    library.write_meta(folder, {"source": "merge", "parts": [
        {"id": "a", "start_offset_s": 0.0, "gap_s": 0.0},
        {"id": "b", "start_offset_s": 5.0, "gap_s": 600.0}]})
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    kinds = [s.get("kind") for s in pipeline["at_diarize"]["segments"]]
    assert kinds == [None, None, "break", None]
    assert pipeline["at_diarize"]["segments"][2]["speaker"] is None


# --- очередь задач: «текст готов» доходит до окна --------------------------------


def test_job_queue_relays_text_ready_as_progress(tmp_path):
    from meet import jobs

    bus = events.EventBus()
    got = []
    bus.subscribe(got.append)

    def spawn(job, on_line):
        on_line(json.dumps({"kind": "progress", "stage": "asr", "label": "распознавание", "done": 1, "total": 1}))
        on_line(json.dumps({"kind": events.TRANSCRIPT_TEXT, "path": str(tmp_path)}))
        on_line(json.dumps({"kind": "progress", "stage": "diarize", "label": "диаризация"}))
        on_line(json.dumps({"kind": "job.result", "path": str(tmp_path / "x.md")}))
        return 0

    queue = jobs.JobQueue(bus, spawn=spawn)
    try:
        job = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
        deadline = time.monotonic() + 5
        while job.state not in (jobs.DONE, jobs.FAILED) and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        queue.stop()
    progress = [e.data["job"] for e in got if e.kind == jobs.JOB_PROGRESS]
    assert [p["text_ready"] for p in progress] == [None, True, True]
    assert job.to_raw()["text_ready"] is True


def test_gigaam_failing_on_mic_keeps_sys_postprocessing_of_gigaam(pipeline, monkeypatch):
    """GigaAM распознала собеседников, а на микрофоне не вышла (дальше —
    Whisper): выравнивание и латиница у собеседников — по их движку."""
    from meet import asr

    tr, folder = pipeline["tr"], pipeline["folder"]
    seen = {"latin": [], "align": []}

    def recognize(wav, hotwords, run):
        if "sys" in Path(wav).name:
            run.choice = asr.Choice("gigaam", "cpu")
            return [_seg(0.0, "добрый день коллеги")]
        run.choice = asr.Choice("faster-whisper", "cpu", note=asr.GIGAAM_FAILED)
        return [_seg(3.0, "да")]

    monkeypatch.setattr(tr, "_recognize", recognize)
    monkeypatch.setattr(tr, "_restore_latin", lambda segs, run: seen["latin"].append(
        [s.text for s in segs]))
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled, **kw: seen["align"].append(enabled) or s)
    tr.transcribe(str(folder), align=True, bus=pipeline["bus"])
    # Латиница — у собеседников (копия для текста и основной проход), не у микрофона Whisper.
    assert seen["latin"] == [["добрый день коллеги"], ["добрый день коллеги"]]
    # После GigaAM выравнивания нет (align_after_gigaam выключено по умолчанию).
    assert seen["align"] == [False]


def test_text_phase_copy_does_not_repeat_log_lines(pipeline, capsys):
    state = Path(pipeline["folder"]).parents[1] / "state"
    state.mkdir(exist_ok=True)
    (state / "config.json").write_text(json.dumps(
        {"asr": {"replacements": [{"from": "коллеги", "to": "друзья"}]}}), encoding="utf-8")
    tr, folder = pipeline["tr"], pipeline["folder"]
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    assert capsys.readouterr().out.count("правила замены: исправлено") == 1


def test_translit_failure_is_reported_once(pipeline, monkeypatch, capsys):
    from meet import asr, translit

    tr, folder = pipeline["tr"], pipeline["folder"]

    def recognize(wav, hotwords, run):
        run.choice = asr.Choice("gigaam", "cpu")
        return [_seg(0.0 if "sys" in Path(wav).name else 3.0, "апи шлюз")]

    def broken(segments, terms):
        raise RuntimeError("словарь повреждён")

    monkeypatch.setattr(tr, "_recognize", recognize)
    monkeypatch.setattr(translit, "apply", broken)
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    out = capsys.readouterr().out
    # Собеседники — один раз (копия для текста молчит), микрофон — свой раз.
    assert out.count("термины латиницей пропущены") == 2
