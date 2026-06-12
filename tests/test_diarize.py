from meet.asr import Segment, Word
from meet.diarize import split_by_speaker


def _seg(start: float, end: float, words: list[tuple[float, float, str]]) -> Segment:
    return Segment(
        start,
        end,
        "".join(w[2] for w in words).strip(),
        words=[Word(*w) for w in words],
    )


def test_splits_segment_at_speaker_change():
    seg = _seg(0.0, 4.0, [(0.0, 1.0, " Привет"), (1.0, 2.0, " всем"), (3.0, 4.0, " Ага")])
    turns = [(0.0, 2.5, "SPEAKER_00"), (2.5, 4.0, "SPEAKER_01")]
    result = split_by_speaker([seg], turns)
    assert [(s.text, s.speaker) for s in result] == [
        ("Привет всем", "SPEAKER_00"),
        ("Ага", "SPEAKER_01"),
    ]
    assert result[1].start == 3.0


def test_word_without_overlap_goes_to_nearest_turn():
    seg = _seg(0.0, 5.5, [(5.0, 5.5, " Понял")])
    turns = [(0.0, 1.0, "SPEAKER_00"), (4.0, 4.8, "SPEAKER_01")]
    result = split_by_speaker([seg], turns)
    assert result[0].speaker == "SPEAKER_01"


def test_empty_turns_keeps_segments():
    seg = _seg(0.0, 1.0, [(0.0, 1.0, " Привет")])
    assert split_by_speaker([seg], []) == [seg]


def test_segment_without_words_assigned_by_overlap():
    seg = Segment(0.0, 2.0, "Привет")
    turns = [(0.0, 1.5, "SPEAKER_00"), (1.5, 5.0, "SPEAKER_01")]
    result = split_by_speaker([seg], turns)
    assert result[0].speaker == "SPEAKER_00"
    assert result[0].text == "Привет"
