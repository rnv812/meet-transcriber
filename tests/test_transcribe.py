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


from meet.diarize import Diarization
from meet.transcribe import _apply_names, _match_names


def test_apply_names_renames_matched_labels():
    turns = [(0.0, 1.0, "SPEAKER_00"), (1.0, 2.0, "SPEAKER_01")]
    got = _apply_names(turns, {"SPEAKER_00": "Демьян Петров"})
    assert got == [(0.0, 1.0, "Демьян Петров"), (1.0, 2.0, "SPEAKER_01")]


def test_apply_names_empty_map_returns_turns():
    turns = [(0.0, 1.0, "SPEAKER_00")]
    assert _apply_names(turns, {}) == turns


def test_match_names_no_embeddings_returns_empty():
    assert _match_names(Diarization(turns=[], embeddings=None)) == {}


def test_match_names_empty_base_returns_empty(monkeypatch):
    import meet.voices

    monkeypatch.setattr(meet.voices, "load_voices", lambda *a, **k: {})
    diar = Diarization(turns=[], embeddings={"SPEAKER_00": [1.0]})
    assert _match_names(diar) == {}


def test_match_names_error_does_not_crash(monkeypatch, capsys):
    import meet.voices

    def boom(*a, **k):
        raise RuntimeError("битая база")

    monkeypatch.setattr(meet.voices, "load_voices", boom)
    diar = Diarization(turns=[], embeddings={"SPEAKER_00": [1.0]})
    assert _match_names(diar) == {}
    assert "матчинг пропущен" in capsys.readouterr().out
