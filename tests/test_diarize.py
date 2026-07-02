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


from types import SimpleNamespace

import numpy as np

from meet.diarize import Diarization, _to_diarization


class _FakeAnnotation:
    def __init__(self, turns, labels):
        self._turns = turns
        self._labels = labels

    def itertracks(self, yield_label=False):
        for start, end, label in self._turns:
            yield SimpleNamespace(start=start, end=end), None, label

    def labels(self):
        return self._labels


def test_to_diarization_extracts_turns_and_embeddings():
    turns = [(0.0, 1.0, "SPEAKER_00"), (1.0, 2.0, "SPEAKER_01")]
    result = SimpleNamespace(
        exclusive_speaker_diarization=_FakeAnnotation(turns, ["SPEAKER_00", "SPEAKER_01"]),
        speaker_diarization=_FakeAnnotation(turns, ["SPEAKER_00", "SPEAKER_01"]),
        speaker_embeddings=np.asarray([[1.0, 0.0], [0.0, 1.0]]),
    )
    diar = _to_diarization(result)
    assert isinstance(diar, Diarization)
    assert diar.turns == turns
    assert np.allclose(diar.embeddings["SPEAKER_01"], [0.0, 1.0])


def test_to_diarization_without_embeddings():
    turns = [(0.0, 1.0, "SPEAKER_00")]
    result = SimpleNamespace(
        exclusive_speaker_diarization=_FakeAnnotation(turns, ["SPEAKER_00"]),
        speaker_diarization=_FakeAnnotation(turns, ["SPEAKER_00"]),
        speaker_embeddings=None,
    )
    assert _to_diarization(result).embeddings is None


def test_to_diarization_legacy_bare_annotation():
    # старый путь pyannote: результат — голая Annotation без эмбеддингов
    turns = [(0.0, 1.0, "SPEAKER_00")]
    diar = _to_diarization(_FakeAnnotation(turns, ["SPEAKER_00"]))
    assert diar.turns == turns
    assert diar.embeddings is None


def test_to_diarization_label_count_mismatch_drops_embeddings():
    turns = [(0.0, 1.0, "SPEAKER_00")]
    result = SimpleNamespace(
        exclusive_speaker_diarization=_FakeAnnotation(turns, ["SPEAKER_00"]),
        speaker_diarization=_FakeAnnotation(turns, ["SPEAKER_00"]),
        speaker_embeddings=np.zeros((3, 2)),
    )
    assert _to_diarization(result).embeddings is None
