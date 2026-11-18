from types import SimpleNamespace

import numpy as np
import pytest

from meet.asr import Segment, Word
from meet.diarize import MIN_OVERLAP, Diarization, _to_diarization, _word_uncertain, split_by_speaker


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


def _result(full_turns, exclusive_turns, labels):
    return SimpleNamespace(
        exclusive_speaker_diarization=_FakeAnnotation(exclusive_turns, labels),
        speaker_diarization=_FakeAnnotation(full_turns, labels),
        speaker_embeddings=None,
    )


_FULL = [(0.0, 10.0, "SPEAKER_00"), (4.0, 5.0, "SPEAKER_01")]
_EXCLUSIVE = [
    (0.0, 4.0, "SPEAKER_00"),
    (4.0, 5.0, "SPEAKER_01"),
    (5.0, 10.0, "SPEAKER_00"),
]


def test_to_diarization_default_takes_overlap_aware_turns():
    diar = _to_diarization(_result(_FULL, _EXCLUSIVE, ["SPEAKER_00", "SPEAKER_01"]))
    assert diar.turns == _FULL


def test_to_diarization_exclusive_flag_keeps_old_behavior():
    diar = _to_diarization(
        _result(_FULL, _EXCLUSIVE, ["SPEAKER_00", "SPEAKER_01"]), exclusive=True
    )
    assert diar.turns == _EXCLUSIVE
    assert diar.overlaps is None


def test_to_diarization_overlaps_filtered_by_min_overlap():
    # pyannote — из extras движка; CI гоняет тесты на лёгком ядре без него.
    pytest.importorskip("pyannote.core")
    from pyannote.core import Annotation
    from pyannote.core import Segment as PySegment

    # 1.0 — по приёмке 03.07.2026: при 0.3 пометки ловили суб-секундные
    # поддакивания («ага» на фоне) — 204 блока «(нахлёст)» на встречу против
    # 75 при 1.0, а реальная переатрибуция почти не менялась (124 -> 114 слов).
    assert MIN_OVERLAP == 1.0
    ann = Annotation()
    ann[PySegment(0.0, 10.0), 0] = "SPEAKER_00"
    ann[PySegment(4.0, 5.0), 1] = "SPEAKER_01"  # нахлёст 1.0 с >= MIN_OVERLAP
    ann[PySegment(8.0, 8.4), 2] = "SPEAKER_01"  # 0.4 с < MIN_OVERLAP — отсев
    result = SimpleNamespace(
        exclusive_speaker_diarization=None,
        speaker_diarization=ann,
        speaker_embeddings=None,
    )
    diar = _to_diarization(result)
    assert diar.overlaps == [(4.0, 5.0)]


def test_to_diarization_overlaps_none_without_get_overlap():
    # легаси/синтетическая annotation без get_overlap(): нахлёсты неизвестны
    turns = [(0.0, 1.0, "SPEAKER_00")]
    diar = _to_diarization(_result(turns, turns, ["SPEAKER_00"]))
    assert diar.overlaps is None


def test_word_uncertain_requires_strict_intersection():
    overlaps = [(4.0, 5.0)]
    assert _word_uncertain(Word(4.2, 4.8, "ага"), overlaps) is True
    assert _word_uncertain(Word(3.0, 4.0, "до"), overlaps) is False  # касание границы
    assert _word_uncertain(Word(5.0, 6.0, "после"), overlaps) is False
    assert _word_uncertain(Word(0.0, 1.0, "вне"), overlaps) is False


def test_word_uncertain_none_overlaps_is_false():
    assert _word_uncertain(Word(0.0, 1.0, "х"), None) is False


def test_overlap_zone_split_into_uncertain_block():
    # overlap-aware turns: фраза SPEAKER_00 непрерывна, вставка SPEAKER_01 поверх;
    # слова остаются у SPEAKER_00 (фраза не рвётся), зона нахлёста — отдельный
    # блок с uncertain=True
    seg = _seg(0.0, 10.0, [
        (0.0, 2.0, " Мы"), (2.0, 4.0, " решили"),
        (4.0, 5.0, " так"), (5.0, 7.0, " и"), (7.0, 10.0, " сделаем"),
    ])
    turns = [(0.0, 10.0, "SPEAKER_00"), (4.0, 5.0, "SPEAKER_01")]
    result = split_by_speaker([seg], turns, overlaps=[(4.0, 5.0)])
    assert [(s.text, s.speaker, s.uncertain) for s in result] == [
        ("Мы решили", "SPEAKER_00", False),
        ("так", "SPEAKER_00", True),
        ("и сделаем", "SPEAKER_00", False),
    ]


def test_split_without_overlaps_marks_nothing():
    seg = _seg(0.0, 4.0, [(0.0, 1.0, " Привет"), (3.0, 4.0, " Ага")])
    turns = [(0.0, 2.5, "SPEAKER_00"), (2.5, 4.0, "SPEAKER_01")]
    assert all(s.uncertain is False for s in split_by_speaker([seg], turns))


def test_segment_without_words_gets_uncertain():
    seg = Segment(4.0, 5.0, "Ага")
    turns = [(0.0, 10.0, "SPEAKER_00"), (4.0, 5.0, "SPEAKER_01")]
    result = split_by_speaker([seg], turns, overlaps=[(4.0, 5.0)])
    assert result[0].uncertain is True


def _reference_word_speaker(word, turns):
    """Прежний алгоритм «слово → спикер»: полный перебор ходов."""
    best, best_overlap = None, 0.0
    for start, end, label in turns:
        overlap = min(word.end, end) - max(word.start, start)
        if overlap > best_overlap:
            best, best_overlap = label, overlap
    if best is not None:
        return best
    nearest = min(
        turns,
        key=lambda t: max(t[0] - word.end, word.start - t[1], 0.0),
        default=None,
    )
    return nearest[2] if nearest else None


def test_word_speaker_equal_overlap_goes_to_earlier_turn():
    from meet.diarize import _word_speaker

    turns = [(0.0, 2.0, "SPEAKER_00"), (0.0, 2.0, "SPEAKER_01")]
    assert _word_speaker(Word(0.5, 1.5, " да"), turns) == "SPEAKER_00"
    assert _word_speaker(Word(0.5, 1.5, " да"), turns[::-1]) == "SPEAKER_01"


def test_word_speaker_long_early_turn_with_short_inside():
    from meet.diarize import _word_speaker

    turns = [(0.0, 100.0, "SPEAKER_00"), (10.0, 10.4, "SPEAKER_01"), (50.0, 51.0, "SPEAKER_02")]
    # слово целиком в длинном ходе, короткий вложенный перекрывает меньше
    assert _word_speaker(Word(10.0, 11.0, " так"), turns) == "SPEAKER_00"
    # слово почти целиком во вложенном: у него перекрытие не меньше
    assert _word_speaker(Word(50.0, 51.0, " ага"), turns) == "SPEAKER_00"
    assert _word_speaker(Word(99.9, 100.5, " всё"), turns) == "SPEAKER_00"


def test_word_speaker_matches_reference_on_random_turns():
    import random

    from meet.diarize import _word_speaker

    rng = random.Random(20261118)
    turns = []
    for _ in range(5000):
        start = round(rng.uniform(0, 7200), 2)
        turns.append((start, round(start + rng.choice([0.0, 0.3, 1.0, 4.0, 30.0, 600.0]) * rng.random(), 2),
                      f"SPEAKER_{rng.randrange(6):02d}"))
    # ходы приходят не по порядку начала и с повторами границ
    turns += [(10.0, 20.0, "SPEAKER_07"), (10.0, 20.0, "SPEAKER_08")]
    words = []
    for _ in range(20000):
        start = round(rng.uniform(-1, 7201), 2)
        words.append(Word(start, round(start + rng.choice([0.0, 0.2, 0.5, 1.2]), 2), " х"))
    for word in words:
        assert _word_speaker(word, turns) == _reference_word_speaker(word, turns), word
