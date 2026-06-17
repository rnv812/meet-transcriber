from types import SimpleNamespace

from meet.asr import Segment, _segments_from_whisper


def _raw(start, end, text, words=(), nsp=0.1, alp=-0.3):
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        words=[SimpleNamespace(start=w[0], end=w[1], word=w[2]) for w in words],
        no_speech_prob=nsp,
        avg_logprob=alp,
    )


def test_segments_from_whisper_applies_offset_and_strips():
    segs = _segments_from_whisper(
        [_raw(1.0, 2.0, " привет ", words=[(1.0, 1.5, "при"), (1.5, 2.0, "вет")])],
        offset_s=10.0,
    )
    assert len(segs) == 1
    assert segs[0].start == 11.0 and segs[0].end == 12.0
    assert segs[0].text == "привет"
    assert segs[0].words[0].start == 11.0 and segs[0].words[1].end == 12.0
    assert segs[0].no_speech_prob == 0.1 and segs[0].avg_logprob == -0.3


def test_segments_from_whisper_zero_offset_default():
    segs = _segments_from_whisper([_raw(3.0, 4.0, "ок")])
    assert segs[0].start == 3.0
