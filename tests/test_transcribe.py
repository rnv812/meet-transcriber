from meet.transcribe import _find_track


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
