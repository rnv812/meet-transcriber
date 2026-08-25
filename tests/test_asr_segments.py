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


def test_drop_hallucinations_filters_lowconf_and_empty():
    keep = Segment(0, 1, "норм", no_speech_prob=0.2, avg_logprob=-0.4)
    lowconf = Segment(2, 3, "ммм", no_speech_prob=0.2, avg_logprob=-2.0)
    empty = Segment(3, 4, "", no_speech_prob=0.1, avg_logprob=-0.1)
    out = drop_hallucinations([keep, lowconf, empty])
    assert [s.text for s in out] == ["норм"]


def test_drop_hallucinations_keeps_confident_speech_despite_high_no_speech_prob():
    # no_speech_prob faster-whisper выдаёт на всё окно декодирования, а не на
    # сегмент: порог по ней выбрасывал уверенно распознанную речь вместе со всем
    # окном (в live-режиме — весь 20-секундный тик).
    seg = Segment(0, 20, "уверенная речь", no_speech_prob=0.998, avg_logprob=-0.07)
    assert drop_hallucinations([seg]) == [seg]


def test_drop_hallucinations_logs_dropped_segment(caplog):
    import logging

    seg = Segment(2, 3, "ммм", no_speech_prob=0.2, avg_logprob=-2.0)
    with caplog.at_level(logging.WARNING, logger="meet.asr"):
        assert drop_hallucinations([seg]) == []
    assert "отброшен" in caplog.text
    assert "ммм" in caplog.text


def test_drop_hallucinations_keeps_when_metrics_none():
    seg = Segment(0, 1, "офлайн без метрик")
    assert drop_hallucinations([seg]) == [seg]


def test_drop_hallucinations_drops_credits_phrase_despite_normal_metrics():
    seg = Segment(0, 1, "Субтитры сделал DimaTorzok", no_speech_prob=0.2, avg_logprob=-0.3)
    assert drop_hallucinations([seg]) == []


def test_drop_hallucinations_drops_thanks_for_watching():
    seg = Segment(0, 1, "Спасибо за просмотр!", no_speech_prob=0.2, avg_logprob=-0.3)
    assert drop_hallucinations([seg]) == []


def test_drop_hallucinations_keeps_benign_subtitles_mention():
    seg = Segment(0, 1, "Субтитры мы пока не делали.", no_speech_prob=0.2, avg_logprob=-0.3)
    assert drop_hallucinations([seg]) == [seg]


def test_model_and_language_come_from_settings(monkeypatch, tmp_path):
    """Русская модель — дефолт настройки, а не константа пайплайна."""
    import json

    from meet.asr import DEFAULT_LANGUAGE, MODEL_NAME, _asr_settings

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "пусто"))
    assert _asr_settings() == (MODEL_NAME, DEFAULT_LANGUAGE)

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "config.json").write_text(
        json.dumps({"asr": {"model": "ggml-large-v3", "language": "en"}}),
        encoding="utf-8",
    )
    assert _asr_settings() == ("ggml-large-v3", "en")
