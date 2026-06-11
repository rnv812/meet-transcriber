from meet.asr import Segment
from meet.diarize import assign_speakers


def test_assigns_speaker_with_max_overlap():
    segs = [Segment(0.0, 4.0, "текст"), Segment(5.0, 8.0, "ещё")]
    turns = [(0.0, 3.0, "SPEAKER_00"), (3.0, 10.0, "SPEAKER_01")]
    assign_speakers(segs, turns)
    assert segs[0].speaker == "SPEAKER_00"  # перекрытие 3 сек против 1 сек
    assert segs[1].speaker == "SPEAKER_01"


def test_no_overlap_leaves_none():
    segs = [Segment(20.0, 21.0, "хвост")]
    assign_speakers(segs, [(0.0, 3.0, "SPEAKER_00")])
    assert segs[0].speaker is None
