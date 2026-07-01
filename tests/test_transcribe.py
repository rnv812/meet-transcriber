from meet.transcribe import _find_track, _maybe_align
from meet.asr import Segment


def test_find_track_returns_opus(tmp_path):
    (tmp_path / "sys.opus").write_bytes(b"x")
    assert _find_track(tmp_path, "sys") == tmp_path / "sys.opus"


def test_find_track_falls_back_to_legacy_wav(tmp_path):
    (tmp_path / "mic.wav").write_bytes(b"x")
    assert _find_track(tmp_path, "mic") == tmp_path / "mic.wav"


def test_find_track_prefers_opus_over_wav(tmp_path):
    (tmp_path / "sys.wav").write_bytes(b"x")
    (tmp_path / "sys.opus").write_bytes(b"x")
    assert _find_track(tmp_path, "sys") == tmp_path / "sys.opus"


def test_find_track_missing_returns_none(tmp_path):
    assert _find_track(tmp_path, "sys") is None


def test_maybe_align_disabled_returns_input_unchanged():
    segs = [Segment(0.0, 1.0, "привет")]
    assert _maybe_align(segs, "x.wav", enabled=False) is segs


def test_maybe_align_falls_back_on_error(monkeypatch):
    segs = [Segment(0.0, 1.0, "привет")]
    import meet.align

    def boom(*a, **k):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(meet.align, "align_segments", boom)
    # ошибка alignment не должна ронять транскрибацию — откат на исходные сегменты
    assert _maybe_align(segs, "x.wav", enabled=True) is segs
