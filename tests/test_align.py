from meet.align import _regroup_words
from meet.asr import Word


class _Span:  # имитация torchaudio TokenSpan (кадровые start/end)
    def __init__(self, start, end):
        self.start = start
        self.end = end


def test_regroup_words_maps_char_spans_to_word_times():
    words = [Word(0.0, 1.0, "привет"), Word(1.0, 2.0, "мир")]
    counts = [6, 3]
    spans = [
        _Span(10, 15), _Span(15, 18), _Span(18, 22),
        _Span(22, 30), _Span(30, 35), _Span(35, 40),  # "привет" → кадры 10..40
        _Span(50, 55), _Span(55, 60), _Span(60, 70),  # "мир" → кадры 50..70
    ]
    out = _regroup_words(words, counts, spans, seg_start=100.0, spf=0.02)
    assert [w.text for w in out] == ["привет", "мир"]
    assert abs(out[0].start - (100.0 + 10 * 0.02)) < 1e-6
    assert abs(out[0].end - (100.0 + 40 * 0.02)) < 1e-6
    assert abs(out[1].start - (100.0 + 50 * 0.02)) < 1e-6
    assert abs(out[1].end - (100.0 + 70 * 0.02)) < 1e-6


def test_regroup_words_keeps_unalignable_word_unchanged():
    orig = Word(5.0, 6.0, "123")  # нет символов в словаре → 0 токенов
    words = [Word(0.0, 1.0, "да"), orig]
    counts = [2, 0]
    spans = [_Span(0, 5), _Span(5, 10)]
    out = _regroup_words(words, counts, spans, seg_start=0.0, spf=0.02)
    assert out[1] is orig  # исходное время сохранено
    assert abs(out[0].end - 10 * 0.02) < 1e-6
