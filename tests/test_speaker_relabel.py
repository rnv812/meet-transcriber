"""Правка спикера по репликам: одна реплика, серия подряд, выбранные вручную —
одним шагом истории встречи, с отменой и повтором.

Все данные выдуманные."""

import json

import pytest

from meet import library, speakers


def _seg(start, end, speaker, text, **extra):
    return {"start": start, "end": end, "speaker": speaker, "text": text,
            "uncertain": False, **extra}


SEGMENTS = [
    _seg(0.0, 4.0, "Спикер 1", "Добрый день, начинаем."),
    _seg(4.5, 6.0, "Спикер 1", "Первый пункт — поставки."),
    _seg(9.0, 12.0, "Спикер 2", "Склад готов."),
    _seg(12.5, 15.0, "Спикер 1", "Хорошо, дальше."),
    {"start": 15.0, "end": 15.0, "speaker": None, "text": "— перерыв 5 мин —",
     "uncertain": False, "kind": "break"},
    _seg(20.0, 22.0, "Вы", "Я вернулся."),
]


@pytest.fixture
def meeting(tmp_path):
    folder = tmp_path / "recordings" / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    library.write_transcript(folder, {"version": 1, "created_at": "2026-09-30T17:00:00",
                                      "speakers": {}, "segments": [dict(s) for s in SEGMENTS]})
    (folder / "2026-09-30_16-04_speakers.json").write_text(json.dumps({
        "model": "m", "source": "C:/rec/x", "date": "2026-09-30",
        "speakers": [
            {"label": "SPEAKER_00", "display": "Спикер 1", "embedding": [1.0, 0.0]},
            {"label": "SPEAKER_01", "display": "Спикер 2", "embedding": [0.0, 1.0]},
        ]}, ensure_ascii=False), encoding="utf-8")
    return folder


@pytest.fixture
def base(tmp_path):
    folder = tmp_path / "voices"
    folder.mkdir()
    return folder


def _labels(folder):
    return [s["speaker"] for s in library.read_transcript(folder)["segments"]]


def test_relabel_one_turn_to_another_speaker_of_the_meeting(meeting, base):
    got = speakers.relabel(meeting, [0, 1], "Спикер 2", base, count=6,
                           labels=["Спикер 1", "Спикер 1"])
    assert _labels(meeting) == ["Спикер 2", "Спикер 2", "Спикер 2", "Спикер 1", None, "Вы"]
    assert got["changed"] == 2
    op = got["step"]["ops"][0]
    assert op == {"type": "relabel", "from": ["Спикер 1"], "to": "Спикер 2", "segments": 2, "turns": 1}
    # names не трогаются: кластер «Спикер 1» остался «Спикер 1».
    assert "names" not in library.read_transcript(meeting)


def test_relabel_to_a_new_person_and_to_a_new_unnamed_speaker(meeting, base):
    speakers.relabel(meeting, [3], "Анна Смирнова", base)
    assert _labels(meeting)[3] == "Анна Смирнова"
    # «Новый спикер без имени»: свободный номер — не занятый ни репликами, ни кластерами.
    speakers.relabel(meeting, [2], None, base)
    assert _labels(meeting)[2] == "Спикер 3"


def test_relabel_selected_turns_from_different_speakers_counts_turns(meeting, base):
    got = speakers.relabel(meeting, [3, 0, 2], "Вы", base)
    assert _labels(meeting) == ["Вы", "Спикер 1", "Вы", "Вы", None, "Вы"]
    op = got["step"]["ops"][0]
    assert op["from"] == ["Спикер 1", "Спикер 2"] and op["segments"] == 3 and op["turns"] == 3


def test_relabel_refuses_bad_input_and_stale_views_without_writing(meeting, base):
    before = (meeting / "transcript.json").read_bytes()
    with pytest.raises(speakers.SpeakerError):
        speakers.relabel(meeting, [], "Анна", base)
    with pytest.raises(speakers.SpeakerError):
        speakers.relabel(meeting, [0], "Спикер 9", base)      # служебная подпись, а не спикер встречи
    with pytest.raises(speakers.SpeakerError):
        speakers.relabel(meeting, [0], "a/b", base)
    with pytest.raises(speakers.SpeakerError):
        speakers.relabel(meeting, [4], "Анна", base)           # отметка перерыва
    with pytest.raises(speakers.SpeakerError):
        speakers.relabel(meeting, [2], "Спикер 2", base)       # уже у него
    with pytest.raises(speakers.Stale):
        speakers.relabel(meeting, [99], "Анна", base)
    with pytest.raises(speakers.Stale):
        speakers.relabel(meeting, [0], "Анна", base, count=5)  # окно видит другой транскрипт
    with pytest.raises(speakers.Stale):
        speakers.relabel(meeting, [0], "Анна", base, labels=["Спикер 2"])
    assert (meeting / "transcript.json").read_bytes() == before
    assert speakers.overview(meeting, base)["history"] == []


def test_relabel_is_undone_and_redone_with_the_rest_of_history(meeting, base):
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Борис"}], {}, base)
    speakers.relabel(meeting, [3], "Борис", base)
    assert _labels(meeting)[:4] == ["Спикер 1", "Спикер 1", "Борис", "Борис"]
    speakers.undo(meeting, base)
    assert _labels(meeting)[:4] == ["Спикер 1", "Спикер 1", "Борис", "Спикер 1"]
    speakers.undo(meeting, base)
    assert _labels(meeting)[:4] == ["Спикер 1", "Спикер 1", "Спикер 2", "Спикер 1"]
    speakers.revert(meeting, speakers.overview(meeting, base)["history"][1]["id"], base)
    assert _labels(meeting)[:4] == ["Спикер 1", "Спикер 1", "Борис", "Борис"]


def test_relabel_normalizes_raw_labels_first(meeting, base):
    data = library.read_transcript(meeting)
    for s in data["segments"]:
        s["speaker"] = {"Спикер 1": "SPEAKER_00", "Спикер 2": "SPEAKER_01"}.get(s["speaker"], s["speaker"])
    library.write_transcript(meeting, data)
    speakers.relabel(meeting, [2], "Спикер 1", base, labels=["Спикер 2"])
    assert _labels(meeting)[:4] == ["Спикер 1"] * 4
