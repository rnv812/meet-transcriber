"""«Переразделить на спикеров»: только диаризация (поддельная) по звуку
записи, реплики раздаются новым спикерам (со словами — с разрезом по слову,
без слов — целиком), предпросмотр, применение одним шагом и отмена.

Тексты, имена и «голоса» выдуманы."""

import json
from pathlib import Path

import numpy as np
import pytest

from meet import jobs, library, rediarize, speakers
from meet.diarize import Diarization


def _w(*items):
    return [[s, e, t] for s, e, t in items]


SEGMENTS = [
    # Два человека, слитые в «Спикер 1»: смена говорящего внутри сегмента (на 2.0 с).
    {"start": 0.0, "end": 4.0, "speaker": "Спикер 1", "text": "Склад готов. Да, отгрузка в четверг.",
     "uncertain": False, "words": _w((0.0, 0.6, " Склад"), (0.6, 1.4, " готов."), (2.1, 2.4, " Да,"),
                                     (2.4, 3.2, " отгрузка"), (3.2, 3.5, " в"), (3.5, 4.0, " четверг."))},
    {"start": 4.2, "end": 5.0, "speaker": "Вы", "text": "Отлично.", "uncertain": False, "track": "mic"},
    # Старый сегмент без слов — целиком тому, кто говорил дольше.
    {"start": 6.0, "end": 9.0, "speaker": "Спикер 1", "text": "Счёт оплатим до пятницы.", "uncertain": False},
    {"start": 9.0, "end": 9.0, "speaker": None, "text": "— перерыв 5 мин —", "uncertain": False, "kind": "break"},
    {"start": 10.0, "end": 12.0, "speaker": "Спикер 2", "text": "Договор продлим.", "uncertain": False},
]

TURNS = [(0.0, 2.0, "SPEAKER_00"), (2.0, 6.5, "SPEAKER_01"), (6.5, 9.5, "SPEAKER_00"), (9.5, 13.0, "SPEAKER_02")]
EMB = {"SPEAKER_00": np.array([1.0, 0.0, 0.0]), "SPEAKER_01": np.array([0.0, 1.0, 0.0]),
       "SPEAKER_02": np.array([0.0, 0.0, 1.0])}


@pytest.fixture
def meeting(tmp_path):
    folder = tmp_path / "recordings" / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "created_at": "2026-09-30T17:00:00", "track_marks": "pipeline",
                                      "names": {"Спикер 3": "Спикер 1"},
                                      "segments": json.loads(json.dumps(SEGMENTS))})
    (folder / "2026-09-30_speakers.json").write_text(json.dumps({
        "model": "m", "source": "C:/rec/x", "date": "2026-09-30", "speakers": [
            {"label": "SPEAKER_00", "display": "Спикер 1", "embedding": [0.7, 0.7, 0.0]},
            {"label": "SPEAKER_01", "display": "Спикер 2", "embedding": [0.0, 0.0, 1.0]}]},
        ensure_ascii=False), encoding="utf-8")
    return folder


@pytest.fixture
def base(tmp_path):
    folder = tmp_path / "voices"
    folder.mkdir()
    (folder / "Анна Смирнова.json").write_text(json.dumps({"samples": [
        {"embedding": [0.0, 0.98, 0.2], "source": "C:/rec/old", "date": "2026-09-01"}]}, ensure_ascii=False),
        encoding="utf-8")
    return folder


def _fake(calls):
    def diarize(wav, **kw):
        calls.append(kw)
        return Diarization(turns=TURNS, embeddings=EMB, overlaps=[])
    return diarize


def _to_wav(src, dst, normalize=False):
    Path(dst).write_bytes(b"wav")
    return Path(dst)


def _run(meeting, base, monkeypatch, **kw):
    from meet import voices

    monkeypatch.setattr(voices, "voices_dir", lambda: base)
    calls = []
    out = rediarize.run(meeting, diarize=_fake(calls), to_wav=_to_wav, **kw)
    return out, calls


def _segs(folder):
    return library.read_transcript(folder)["segments"]


def test_sensitivity_maps_to_pipeline_clustering_threshold():
    assert rediarize.clustering_threshold(None) is None
    assert rediarize.clustering_threshold(0.5) is None       # как у обычной расшифровки
    assert rediarize.clustering_threshold(1.0) == 0.4        # чувствительнее — порог ниже
    assert rediarize.clustering_threshold(0.0) == 0.8
    assert rediarize.clustering_threshold(0.75) == 0.5


def test_run_reassigns_text_without_asr_and_previews(meeting, base, monkeypatch):
    out, calls = _run(meeting, base, monkeypatch, num_speakers=3, sensitivity=1.0)
    assert out.name == rediarize.PREVIEW_NAME
    assert calls == [{"num_speakers": 3, "min_speakers": None, "max_speakers": None,
                      "exclusive": False, "clustering_threshold": 0.4}]
    assert _segs(meeting) == SEGMENTS  # транскрипт не тронут до применения
    got = rediarize.preview(meeting)
    assert got["stale"] is False and got["segments"] == 5 and got["before"] == 3
    rows = {r["label"]: r for r in got["speakers"]}
    # SPEAKER_01 узнан по базе (0.98), остальные — «Спикер N» по порядку появления.
    assert list(rows) == ["Спикер 1", "Анна Смирнова", "Вы", "Спикер 2"]
    assert got["cut"] == 1 and got["changed"] == 1
    assert rows["Анна Смирнова"]["samples"][0]["text"].startswith("Да, отгрузка")
    assert library.describe(meeting).rediarize_ready is True


def test_apply_cuts_at_words_relabels_and_undoes(meeting, base, monkeypatch):
    _run(meeting, base, monkeypatch)
    got = rediarize.apply(meeting, base)
    segs = _segs(meeting)
    assert [(s["speaker"], s["text"]) for s in segs] == [
        ("Спикер 1", "Склад готов."),
        ("Анна Смирнова", "Да, отгрузка в четверг."),
        ("Вы", "Отлично."),
        ("Спикер 1", "Счёт оплатим до пятницы."),
        (None, "— перерыв 5 мин —"),
        ("Спикер 2", "Договор продлим."),
    ]
    assert segs[1]["start"] == 2.1 and segs[1]["words"][0] == [2.1, 2.4, " Да,"]
    assert "names" not in library.read_transcript(meeting)
    side = json.loads((meeting / "2026-09-30_speakers.json").read_text(encoding="utf-8"))
    assert [(e["label"], e["display"]) for e in side["speakers"]] == [
        ("SPEAKER_00", "Спикер 1"), ("SPEAKER_01", "Анна Смирнова"), ("SPEAKER_02", "Спикер 2")]
    assert got["step"]["ops"][0]["type"] == "rediarize" and got["step"]["ops"][0]["speakers"] == 4
    assert not (meeting / rediarize.PREVIEW_NAME).exists()
    rows = {r["label"]: r for r in speakers.overview(meeting, base)["speakers"]}
    assert rows["Анна Смирнова"]["has_voice"]

    speakers.undo(meeting, base)
    data = library.read_transcript(meeting)
    assert data["segments"] == SEGMENTS and data["names"] == {"Спикер 3": "Спикер 1"}
    side = json.loads((meeting / "2026-09-30_speakers.json").read_text(encoding="utf-8"))
    assert [e["display"] for e in side["speakers"]] == ["Спикер 1", "Спикер 2"]
    speakers.redo(meeting, base)
    assert len(_segs(meeting)) == 6
    # После переразделения — обычные правки поверх и их отмена.
    speakers.relabel(meeting, [0], "Анна Смирнова", base)
    speakers.undo(meeting, base)
    speakers.undo(meeting, base)
    assert _segs(meeting) == SEGMENTS


def test_apply_refuses_if_transcript_changed_since_the_run(meeting, base, monkeypatch):
    _run(meeting, base, monkeypatch)
    speakers.relabel(meeting, [4], "Спикер 1", base)
    assert rediarize.preview(meeting)["stale"] is True
    with pytest.raises(speakers.Stale, match="запустите"):
        rediarize.apply(meeting, base)
    assert rediarize.discard(meeting) is True
    assert rediarize.preview(meeting) is None
    with pytest.raises(speakers.SpeakerError):
        rediarize.apply(meeting, base)


def test_rediarize_job_argv_and_worker(meeting, monkeypatch, capsys):
    from meet import job_worker

    job = jobs.Job(id="r", kind=jobs.REDIARIZE, folder=str(meeting),
                   options={"min_speakers": 2, "max_speakers": 6, "sensitivity": 0.7})
    assert jobs.worker_argv(job)[-5:] == ["rediarize", str(meeting), "--min-speakers=2", "--max-speakers=6",
                                          "--sensitivity=0.7"]
    seen = {}
    monkeypatch.setattr(rediarize, "run", lambda folder, **kw: seen.update(kw) or folder / "x.json")
    assert job_worker.main(["rediarize", str(meeting), "--num-speakers=4"]) == 0
    assert seen["num_speakers"] == 4 and seen["sensitivity"] is None
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert last == {"kind": "job.result", "path": str(meeting / "x.json")}
