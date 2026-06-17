from types import SimpleNamespace

from meet.asr import Segment, _segments_from_whisper, drop_hallucinations


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


def test_drop_hallucinations_filters_noise_lowconf_and_empty():
    keep = Segment(0, 1, "норм", no_speech_prob=0.2, avg_logprob=-0.4)
    noise = Segment(1, 2, "шшш", no_speech_prob=0.9, avg_logprob=-0.5)
    lowconf = Segment(2, 3, "ммм", no_speech_prob=0.2, avg_logprob=-2.0)
    empty = Segment(3, 4, "", no_speech_prob=0.1, avg_logprob=-0.1)
    out = drop_hallucinations([keep, noise, lowconf, empty])
    assert [s.text for s in out] == ["норм"]


def test_drop_hallucinations_keeps_when_metrics_none():
    seg = Segment(0, 1, "офлайн без метрик")
    assert drop_hallucinations([seg]) == [seg]
