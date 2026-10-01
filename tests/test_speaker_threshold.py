"""Порог узнавания голоса встречи: пересчёт имён автоматически названных
спикеров по голосам кластеров — одним шагом истории, ручные имена не
трогаются. Голоса — синтетические векторы, имена выдуманы."""

import json

import numpy as np
import pytest

from meet import library, speakers, transcribe


def _seg(start, end, speaker):
    return {"start": start, "end": end, "speaker": speaker, "text": "фраза", "uncertain": False}


@pytest.fixture
def meeting(tmp_path):
    folder = tmp_path / "recordings" / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    library.write_transcript(folder, {"version": 1, "created_at": "2026-09-30T17:00:00", "segments": [
        _seg(0, 3, "Спикер 1"), _seg(3, 6, "Анна Смирнова"), _seg(6, 9, "Спикер 3"), _seg(9, 12, "Вы")]})
    (folder / "2026-09-30_speakers.json").write_text(json.dumps({
        "model": "m", "source": "C:/rec/x", "date": "2026-09-30", "speakers": [
            {"label": "SPEAKER_00", "display": "Спикер 1", "embedding": [0.8, 0.6, 0.0]},
            {"label": "SPEAKER_01", "display": "Анна Смирнова", "embedding": [0.0, 1.0, 0.0]},
            {"label": "SPEAKER_02", "display": "Спикер 3", "embedding": [0.0, 0.0, 1.0]}]},
        ensure_ascii=False), encoding="utf-8")
    return folder


@pytest.fixture
def base(tmp_path):
    folder = tmp_path / "voices"
    folder.mkdir()
    for name, vec in (("Борис Козлов", [1.0, 0.0, 0.0]), ("Анна Смирнова", [0.0, 0.92, 0.39])):
        (folder / f"{name}.json").write_text(json.dumps(
            {"samples": [{"embedding": vec, "source": "C:/rec/old", "date": "2026-09-01"}]},
            ensure_ascii=False), encoding="utf-8")
    return folder


def _labels(folder):
    return [s["speaker"] for s in library.read_transcript(folder)["segments"]]


def test_lower_threshold_names_a_similar_voice_and_higher_unnames_it(meeting, base):
    plan = speakers.threshold_plan(meeting, 0.75, base)
    rows = {r["label"]: r for r in plan["rows"]}
    assert rows["Спикер 1"]["best"] == "Борис Козлов" and rows["Спикер 1"]["score"] == pytest.approx(0.8)
    assert rows["Спикер 1"]["to"] == "Борис Козлов"   # 0.80 ≥ 0.75
    assert rows["Анна Смирнова"]["to"] == "Анна Смирнова"
    assert rows["Спикер 3"]["to"] is None
    assert [r["label"] for r in plan["changes"]] == ["Спикер 1"]

    got = speakers.threshold_apply(meeting, 0.75, base)
    assert _labels(meeting) == ["Борис Козлов", "Анна Смирнова", "Спикер 3", "Вы"]
    assert got["step"]["ops"][0] == {"type": "threshold", "value": 0.75}
    assert library.read_meta(meeting)["voice_threshold"] == 0.75

    # Строже: Анна (0.92) остаётся, Борис (0.80) снова безымянный.
    speakers.threshold_apply(meeting, 0.85, base)
    assert _labels(meeting) == ["Спикер 1", "Анна Смирнова", "Спикер 3", "Вы"]
    speakers.undo(meeting, base)
    assert _labels(meeting)[0] == "Борис Козлов"
    assert library.read_meta(meeting)["voice_threshold"] == 0.75
    speakers.undo(meeting, base)
    assert _labels(meeting)[0] == "Спикер 1"
    assert "voice_threshold" not in library.read_meta(meeting)
    assert speakers.overview(meeting, base)["voice_threshold"] is None


def test_threshold_leaves_names_given_by_hand(meeting, base):
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 1", "to": "Глеб Демьянов"}], {}, base)
    plan = speakers.threshold_plan(meeting, 0.6, base)
    rows = {r["label"]: r for r in plan["rows"]}
    assert rows["Глеб Демьянов"]["auto"] is False
    assert plan["changes"] == []
    got = speakers.threshold_apply(meeting, 0.6, base)   # имён не меняет — только запоминает порог
    assert got["changed"] == 0 and library.read_meta(meeting)["voice_threshold"] == 0.6
    assert _labels(meeting)[0] == "Глеб Демьянов"


def test_transcription_uses_the_meeting_threshold_then_settings(meeting, monkeypatch, tmp_path):
    from meet import settings

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    settings.patch({"asr": {"voice_threshold": 0.7}})
    assert transcribe.voice_threshold(meeting) == 0.7
    library.write_meta(meeting, {"voice_threshold": 0.9})
    assert transcribe.voice_threshold(meeting) == 0.9
    library.write_meta(meeting, {"voice_threshold": 3})
    assert transcribe.voice_threshold(meeting) == 0.95
    assert transcribe.voice_threshold(None) == 0.7
