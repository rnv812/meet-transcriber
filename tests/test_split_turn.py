"""«Разделить реплику здесь»: по границе слова (у новых расшифровок) или по
краю сегмента (у старых), вторая часть — другому спикеру; один шаг истории,
отмена собирает сегмент обратно. Тексты выдуманные."""

import json

import pytest

from meet import library, speakers


def _w(*items):
    return [[s, e, t] for s, e, t in items]


SEGMENTS = [
    {"start": 0.0, "end": 4.0, "speaker": "Спикер 1", "text": "Склад готов. Да, подтверждаю отгрузку.",
     "uncertain": False, "words": _w((0.0, 0.5, " Склад"), (0.5, 1.0, " готов."), (1.6, 1.8, " Да,"),
                                     (1.8, 2.6, " подтверждаю"), (2.6, 4.0, " отгрузку."))},
    {"start": 4.5, "end": 6.0, "speaker": "Спикер 1", "text": "В четверг.", "uncertain": False,
     "words": _w((4.5, 5.0, " В"), (5.0, 6.0, " четверг."))},
    {"start": 6.5, "end": 8.0, "speaker": "Спикер 1", "text": "Старый сегмент без слов.", "uncertain": False},
    {"start": 9.0, "end": 10.0, "speaker": "Спикер 2", "text": "Хорошо.", "uncertain": False},
]


@pytest.fixture
def meeting(tmp_path):
    folder = tmp_path / "recordings" / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    library.write_transcript(folder, {"version": 1, "created_at": "2026-09-30T17:00:00",
                                      "segments": json.loads(json.dumps(SEGMENTS))})
    return folder


@pytest.fixture
def base(tmp_path):
    return tmp_path / "voices"


def _segs(folder):
    return library.read_transcript(folder)["segments"]


def test_split_inside_a_segment_at_the_nearest_word(meeting, base):
    # «Склад готов. |Да, подтверждаю…» — место чуть правее границы слова.
    got = speakers.split_turn(meeting, [0, 1, 2], 0, 14, "Спикер 2", base, count=4,
                              labels=["Спикер 1"] * 3)
    segs = _segs(meeting)
    assert [(s["text"], s["speaker"]) for s in segs[:2]] == [
        ("Склад готов.", "Спикер 1"), ("Да, подтверждаю отгрузку.", "Спикер 2")]
    assert (segs[0]["start"], segs[0]["end"], segs[1]["start"], segs[1]["end"]) == (0.0, 1.0, 1.6, 4.0)
    assert segs[1]["words"][0] == [1.6, 1.8, " Да,"]
    # Остаток реплики — тоже второму спикеру; следующая реплика не тронута.
    assert [s["speaker"] for s in segs[2:]] == ["Спикер 2", "Спикер 2", "Спикер 2"]
    assert got["step"]["ops"][0] == {"type": "split_turn", "label": "Спикер 1", "to": "Спикер 2",
                                     "at": 1.6, "cut": "word"}

    speakers.undo(meeting, base)
    assert _segs(meeting) == SEGMENTS
    speakers.redo(meeting, base)
    assert len(_segs(meeting)) == 5 and _segs(meeting)[1]["speaker"] == "Спикер 2"
    speakers.undo(meeting, base)
    assert _segs(meeting) == SEGMENTS


def test_split_at_segment_edges_and_without_words(meeting, base):
    # Начало второго сегмента реплики — без разреза, со второго сегмента.
    speakers.split_turn(meeting, [0, 1, 2], 1, 0, "Анна Смирнова", base)
    assert [s["speaker"] for s in _segs(meeting)] == ["Спикер 1", "Анна Смирнова", "Анна Смирнова", "Спикер 2"]
    speakers.undo(meeting, base)
    # Сегмент без слов — к ближайшему краю: место в конце → со следующего (его нет — отказ).
    with pytest.raises(speakers.SpeakerError, match="ничего нет"):
        speakers.split_turn(meeting, [0, 1, 2], 2, 20, "Анна Смирнова", base)
    got = speakers.split_turn(meeting, [0, 1, 2], 2, 3, None, base)
    assert got["step"]["ops"][0]["cut"] == "segment"
    assert [s["speaker"] for s in _segs(meeting)] == ["Спикер 1", "Спикер 1", "Спикер 3", "Спикер 2"]


def test_split_turn_refusals_write_nothing(meeting, base):
    before = (meeting / "transcript.json").read_bytes()
    with pytest.raises(speakers.SpeakerError):
        speakers.split_turn(meeting, [0, 1, 2], 0, 14, "Спикер 1", base)        # тот же спикер
    with pytest.raises(speakers.SpeakerError):
        speakers.split_turn(meeting, [0, 1], 3, 2, "Анна", base)                 # место вне реплики
    with pytest.raises(speakers.Stale):
        speakers.split_turn(meeting, [0, 1, 2], 0, 14, "Анна", base, count=9)
    with pytest.raises(speakers.Stale):
        speakers.split_turn(meeting, [2, 3], 2, 3, "Анна", base)                 # разные спикеры
    assert (meeting / "transcript.json").read_bytes() == before


def test_undo_refuses_when_the_cut_part_was_edited_since(meeting, base):
    speakers.split_turn(meeting, [0, 1, 2], 0, 14, "Спикер 2", base)
    data = library.read_transcript(meeting)
    data["segments"][1]["text"] = "Правка вручную."
    library.write_transcript(meeting, data)
    with pytest.raises(speakers.Stale):
        speakers.undo(meeting, base)
