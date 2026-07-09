import numpy as np

from meet.voice_id import SAMPLE_RATE, VoiceMatcher


def _base():
    return {
        "Демьян": [np.array([1.0, 0.0], dtype=np.float32)],
        "Пётр": [np.array([0.0, 1.0], dtype=np.float32)],
    }


def _audio(seconds=2.0):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


def test_matches_confident_embedding():
    m = VoiceMatcher(base=_base(), embed_fn=lambda a: np.array([1.0, 0.05], dtype=np.float32))
    assert m.name_for(_audio()) == "Демьян"


def test_below_threshold_returns_none():
    m = VoiceMatcher(base=_base(), embed_fn=lambda a: np.array([1.0, 0.9], dtype=np.float32))
    assert m.name_for(_audio()) is None


def test_short_segment_skipped_without_embedding():
    calls = []

    def embed(a):
        calls.append(a)
        return np.array([1.0, 0.0], dtype=np.float32)

    m = VoiceMatcher(base=_base(), embed_fn=embed)
    assert m.name_for(_audio(seconds=0.5)) is None
    assert calls == []


def test_nan_embedding_returns_none():
    m = VoiceMatcher(base=_base(), embed_fn=lambda a: np.array([np.nan, 0.0], dtype=np.float32))
    assert m.name_for(_audio()) is None


def test_empty_base_disabled():
    m = VoiceMatcher(base={}, embed_fn=lambda a: np.array([1.0, 0.0], dtype=np.float32))
    assert not m.enabled
    assert m.name_for(_audio()) is None


def test_load_with_empty_base_does_not_build_embedder(monkeypatch, capsys):
    import meet.voice_id as vid

    monkeypatch.setattr(vid, "load_voices", lambda: {})
    m = VoiceMatcher()
    m.load()
    assert not m.enabled
    assert "live-имена выключены" in capsys.readouterr().out


def test_match_announced_once(capsys):
    m = VoiceMatcher(base=_base(), embed_fn=lambda a: np.array([1.0, 0.0], dtype=np.float32))
    m.name_for(_audio())
    m.name_for(_audio())
    out = capsys.readouterr().out
    assert out.count("Демьян") == 1
