"""Текст до спикеров (`phase: "text"`) не принимают за окончательную
расшифровку: ни резидент (анализ, итоги, база знаний, «Улучшить расшифровку»,
правка спикеров, экспорт, агент), ни сами задачи, если до них всё же дошло.
Прерванная между фазами расшифровка восстанавливается после перезапуска.
Модели не вызываются, данные выдуманные."""

import json
import threading
from pathlib import Path

import pytest

from meet import analysis, assistant, control, improve, jobs, kb_export, library, people, rediarize, tray
from meet import tray_control

RID = "2026-10-05_10-00"
SEGMENTS = [
    {"start": 0.0, "end": 2.0, "speaker": None, "text": "Добрый день, коллеги."},
    {"start": 2.5, "end": 4.0, "speaker": "Вы", "text": "Да, начнём.", "track": "mic"},
]


def _write_config(root: Path, **extra) -> None:
    path = root / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(root / "recordings"), "voices_dir": str(root / "voices")},
        "assistant": {"knowledge_dir": None, "notes_dir": None},
        **extra,
    }, ensure_ascii=False), encoding="utf-8")


def _text_phase(folder: Path) -> None:
    library.write_transcript(folder, {"version": 1, "title": "Встреча", "phase": "text",
                                      "created_at": "2026-10-05T10:05:00", "segments": SEGMENTS})


@pytest.fixture
def folder(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    _write_config(tmp_path, export={"meetings_dir": str(tmp_path / "vault"), "auto_export": True},
                  analysis={"auto": True, "consent": "granted", "improve_auto": True})
    (tmp_path / "vault").mkdir()
    folder = tmp_path / "recordings" / RID
    folder.mkdir(parents=True)
    for role in ("sys", "mic"):
        (folder / f"{role}.opus").write_bytes(b"x")
    _text_phase(folder)
    return folder


def _blocked(app):
    release = threading.Event()

    def spawn(job, on_line):
        release.wait(timeout=5)
        return 0

    return jobs.JobQueue(app.bus, spawn=spawn), release


@pytest.fixture
def state(folder, monkeypatch):
    app = tray.TrayApp()
    queue, release = _blocked(app)
    llm_queue, llm_release = _blocked(app)
    st = tray_control.TrayControl(app, queue=queue, llm_queue=llm_queue)
    st._background = lambda fn, name="": fn()
    monkeypatch.setattr(tray_control, "_provider_installed", lambda cfg: True)
    try:
        yield st
    finally:
        release.set()
        llm_release.set()
        queue.stop()
        llm_queue.stop()


# --- резидент: действия по тексту до спикеров -----------------------------------


@pytest.mark.parametrize("call", [
    lambda st: st.make_summary(RID),
    lambda st: st.ask(RID, {"question": "что решили?"}),
    lambda st: st.make_analysis(RID),
    lambda st: st.make_improve(RID),
    lambda st: st.suggest_title(RID),
    lambda st: st.export(RID, "md"),
    lambda st: st.kb_export(RID),
    lambda st: st.speakers(RID),
    lambda st: st.speakers_apply(RID, {"ops": [{"type": "rename", "label": "Вы", "to": "Анна"}]}),
    lambda st: st.speakers_relabel(RID, {"idx": [0], "to": "Анна"}),
    lambda st: st.name_speakers(RID, {"Вы": "Анна"}),
    lambda st: st.text_apply(RID, {"find": "коллеги", "replace": "друзья", "scope": "all"}),
    lambda st: st.text_preview(RID, {"find": "коллеги"}),
    lambda st: st.speakers_rediarize(RID, {}),
    lambda st: st.speakers_split_prepare(RID, {"label": "Вы"}),
    lambda st: st.speakers_undo(RID),
    lambda st: st.save_transcript(RID, {"version": 1, "segments": SEGMENTS}),
], ids=["summary", "ask", "analysis", "improve", "title", "export", "kb", "speakers", "apply", "relabel",
        "name", "text_apply", "text_preview", "rediarize", "split", "undo", "save"])
def test_text_phase_refuses_final_only_actions(state, folder, call):
    before = (folder / library.TRANSCRIPT_JSON).read_bytes()
    with pytest.raises(control.Conflict, match="Спикеры ещё не определены"):
        call(state)
    assert (folder / library.TRANSCRIPT_JSON).read_bytes() == before
    assert state.llm_queue.listing() == [] and state.queue.listing() == []


def test_text_phase_reason_while_transcription_runs(state, folder):
    state.queue.submit(jobs.TRANSCRIBE, str(folder))
    with pytest.raises(control.Conflict, match="дождитесь конца расшифровки"):
        state.speakers_apply(RID, {"ops": []})


def test_text_phase_reason_after_interrupted_transcription(state):
    with pytest.raises(control.Conflict, match="расшифруйте запись заново"):
        state.make_summary(RID)


def test_window_reads_text_phase_as_is(state):
    card = state.recording(RID)
    assert card["transcript_phase"] == "text" and card["has_transcript"]
    assert card["transcript"]["phase"] == "text"
    assert [s["speaker"] for s in card["transcript"]["segments"]] == [None, "Вы"]
    assert state.transcript(RID)["phase"] == "text"


def test_agent_gets_no_text_phase_without_live_feed(state, folder):
    assert state.agent_files(RID)["files"] == []
    with pytest.raises(control.Conflict):
        state.agent_context(RID, {})
    (folder / "live_transcript.md").write_text("лента живого режима", encoding="utf-8")
    assert state.agent_files(RID) == {"files": ["transcript.md"], "live": True}
    out = state.agent_context(RID, {})
    assert out["files"] == ["transcript.md"]
    assert "лента живого режима" in (folder / "transcript.md").read_text(encoding="utf-8")


def test_automatic_steps_skip_text_phase(state, folder, monkeypatch):
    exported = []
    monkeypatch.setattr(kb_export, "export_recording", lambda f, cfg, **kw: exported.append(f) or {"path": ""})
    assert state._kb_wanted(jobs.TRANSCRIBE, folder, False) is False
    state._auto_kb_export(folder)
    state._auto_analyze(folder)
    state._auto_improve(folder, transcribed=True)
    assert exported == []
    assert state.llm_queue.listing() == []
    assert state._kb_failed is None


def test_category_change_does_not_export_text_phase(state, folder, monkeypatch):
    exported = []
    monkeypatch.setattr(kb_export, "export_recording", lambda f, cfg, **kw: exported.append(f) or {"path": ""})
    monkeypatch.setattr(kb_export, "previously_exported", lambda f: True)
    state._speakers_reexport(folder)
    assert exported == []


# --- задачи: последняя линия обороны --------------------------------------------


def test_model_tasks_refuse_text_phase(folder):
    with pytest.raises(RuntimeError, match="Спикеры ещё не определены"):
        assistant.summarize(folder, runner=None, knowledge_dir=None)
    with pytest.raises(RuntimeError, match="Спикеры ещё не определены"):
        assistant.ask(folder, "что решили?", runner=None, knowledge_dir=None)
    with pytest.raises(analysis.AnalysisError, match="Спикеры ещё не определены"):
        analysis.run(folder, runner=None, cfg=None)
    with pytest.raises(improve.ImproveError, match="Спикеры ещё не определены"):
        improve.run(folder, runner=None, cfg=None)


def test_kb_export_refuses_text_phase(folder, tmp_path):
    from meet import settings

    with pytest.raises(ValueError, match="Спикеры ещё не определены"):
        kb_export.export_recording(folder, settings.load())
    assert not any((tmp_path / "vault").iterdir())


def test_rediarize_refuses_text_phase(folder):
    with pytest.raises(ValueError, match="Спикеры ещё не определены"):
        rediarize.run(folder, diarize=lambda *a, **k: None, to_wav=lambda *a, **k: None)


def test_people_stats_ignore_text_phase(folder):
    assert people._speech_of(folder) == {}
    library.write_transcript(folder, {"version": 1, "segments": SEGMENTS})
    assert people._speech_of(folder) == {"Вы": 1.5}


# --- прерванная расшифровка -----------------------------------------------------


class FakeQueue:
    def __init__(self):
        self.submitted: list[jobs.Job] = []
        self.stopping = False

    def submit(self, kind, folder, options=None, **kw):
        job = jobs.Job(id=f"j{len(self.submitted)}", kind=kind, folder=str(folder))
        self.submitted.append(job)
        return job

    def get(self, job_id):
        return None

    def active_for(self, folder, kinds):
        return None

    def listing(self):
        return []

    def cancel(self, job_id):
        return False

    def stop(self):
        pass


def test_recover_requeues_transcription_interrupted_after_the_text(folder, monkeypatch):
    import time

    library.write_meta(folder, {"pending_transcribe": time.time() - 60})
    _text_phase(folder)  # написан позже отметки — но это ещё не конец расшифровки
    queue = FakeQueue()
    st = tray_control.TrayControl(tray.TrayApp(), queue=queue, llm_queue=FakeQueue())
    st._background = lambda fn, name="": fn()
    done = st.recover()
    assert done["queued"] == [RID]
    assert [j.kind for j in queue.submitted] == [jobs.TRANSCRIBE]


def test_recover_does_not_finish_merge_on_text_phase(folder, monkeypatch):
    library.write_meta(folder, {"source": "merge", "merge": {"state": "merged"}, "merged_from": ["a", "b"]})
    finished = []
    st = tray_control.TrayControl(tray.TrayApp(), queue=FakeQueue(), llm_queue=FakeQueue())
    st._background = lambda fn, name="": fn()
    monkeypatch.setattr(st, "_finish_merge", lambda f: finished.append(f))
    st.recover()
    assert finished == []


def test_console_enroll_does_not_rename_in_text_phase(folder):
    from meet import voices

    before = (folder / library.TRANSCRIPT_JSON).read_bytes()
    assert voices._name_in_transcript(folder, {"Вы": "Анна"}) == 0
    assert (folder / library.TRANSCRIPT_JSON).read_bytes() == before


def test_speaker_and_text_edit_modules_refuse_text_phase(folder, tmp_path):
    from meet import speakers, textfix

    with pytest.raises(speakers.SpeakerError, match="Спикеры ещё не определены"):
        speakers.apply(folder, [{"type": "rename", "label": "Вы", "to": "Анна"}], {}, tmp_path / "voices")
    with pytest.raises(speakers.SpeakerError, match="Спикеры ещё не определены"):
        textfix.preview(folder, "коллеги")
    with pytest.raises(speakers.SpeakerError, match="Спикеры ещё не определены"):
        textfix.apply(folder, "коллеги", "друзья", "all", tmp_path / "voices")
    assert "speaker_history" not in library.read_meta(folder)


def test_people_cards_and_samples_ignore_text_phase(folder, tmp_path):
    voices = tmp_path / "voices"
    voices.mkdir(exist_ok=True)
    (voices / "Вы.json").write_text(json.dumps({"samples": [{"embedding": [0.1]}]}), encoding="utf-8")
    assert people.person("Вы", voices, folder.parent)["meetings"] == []
    assert people.sample("Вы", voices, folder.parent) is None


def test_search_hits_in_text_phase_have_no_speaker_label(folder):
    from meet import search

    search.clear_cache()
    found = search.search_library(folder.parent, "коллеги")
    hits = found[0]["hits"]
    assert hits and hits[0]["speaker"] == ""
    assert search.search_library(folder.parent, 'спикер:"Неизвестный"') == []


def test_resume_marks_are_dropped_on_text_phase(state, folder):
    import time

    mark = {"at": time.time(), "manual": True}
    library.write_meta(folder, {"pending_analysis": mark, "pending_improve": mark})
    assert state._resume_analysis(folder, mark, 0.0) is False
    assert state._resume_improve(folder, mark, 0.0) is False
    meta = library.read_meta(folder)
    assert "pending_analysis" not in meta and "pending_improve" not in meta
    assert state.llm_queue.listing() == []


def test_merge_failing_after_text_keeps_parts_until_the_final(state, folder, monkeypatch):
    """Объединённая встреча, расшифровка которой упала после текста: исходные
    части не трогаются (`state: merged`); их обработает конец следующей."""
    from meet import events as ev

    library.write_meta(folder, {"source": "merge", "merge": {"state": "merged"}, "merged_from": ["a", "b"]})
    finished = []
    monkeypatch.setattr(state, "_finish_merge", lambda f: finished.append(f))
    job = jobs.Job(id="x1", kind=jobs.TRANSCRIBE, folder=str(folder), state=jobs.FAILED)
    state._on_job_event(ev.Event(jobs.JOB_FAILED, {"job": job.to_raw()}))
    assert finished == [] and library.read_meta(folder)["merge"]["state"] == "merged"
    library.write_transcript(folder, {"version": 1, "segments": SEGMENTS})
    job.state = jobs.DONE
    state._on_job_event(ev.Event(jobs.JOB_DONE, {"job": job.to_raw()}))
    assert finished == [folder]


def test_phase_check_uses_the_cached_head(state, folder, monkeypatch):
    """Отказ по фазе не разбирает весь transcript.json на каждый запрос окна."""
    library.final_transcript(folder)  # заголовок — в кэш через describe ниже
    library.describe(folder)
    calls = []
    real = library.read_transcript
    monkeypatch.setattr(library, "read_transcript", lambda f: calls.append(f) or real(f))
    assert state._text_only(folder)
    assert calls == []
