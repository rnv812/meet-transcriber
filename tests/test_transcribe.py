from pathlib import Path

import numpy as np

from meet.asr import Segment
from meet.diarize import Diarization
from meet.transcribe import _apply_names, _find_track, _match_names, _maybe_align, _write_sidecar
from meet.voices import read_sidecar, sidecar_path


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


def test_write_sidecar_display_matches_transcript_names(tmp_path):
    out_md = tmp_path / "2026-07-01_transcript.md"
    # в сегментах SPEAKER_01 появляется раньше SPEAKER_00 → он «Спикер 1»;
    # SPEAKER_02 совпал с базой и уже переименован в сегментах
    segments = [
        Segment(0.0, 1.0, "а", "SPEAKER_01"),
        Segment(1.0, 2.0, "б", "Демьян Петров"),
        Segment(2.0, 3.0, "в", "SPEAKER_00"),
    ]
    diar = Diarization(
        turns=[],
        embeddings={
            "SPEAKER_00": np.asarray([1.0]),
            "SPEAKER_01": np.asarray([2.0]),
            "SPEAKER_02": np.asarray([3.0]),
        },
    )
    _write_sidecar(out_md, Path("recordings/x"), "2026-07-01", segments, diar, {"SPEAKER_02": "Демьян Петров"})
    data = read_sidecar(sidecar_path(out_md))
    display = {s["label"]: s["display"] for s in data["speakers"]}
    assert display == {"SPEAKER_01": "Спикер 1", "SPEAKER_00": "Спикер 2", "SPEAKER_02": "Демьян Петров"}


def test_write_sidecar_no_embeddings_writes_nothing(tmp_path):
    out_md = tmp_path / "x.md"
    _write_sidecar(out_md, Path("r"), "2026-07-01", [], Diarization(turns=[]), {})
    assert not sidecar_path(out_md).exists()


def test_write_sidecar_warns_when_no_embeddings(tmp_path, capsys):
    out_md = tmp_path / "x.md"
    diar = Diarization(turns=[(0.0, 1.0, "SPEAKER_00")], embeddings=None)
    _write_sidecar(out_md, Path("r"), "2026-07-01", [], diar, {})
    assert not sidecar_path(out_md).exists()
    assert "не вернул эмбеддинги" in capsys.readouterr().out


def test_no_embeddings_warning_survives_cp866(tmp_path, capsys):
    # предупреждение должно печататься и в консоли cp866 (без «→» и «—»)
    diar = Diarization(turns=[(0.0, 1.0, "SPEAKER_00")], embeddings=None)
    _write_sidecar(tmp_path / "x.md", Path("r"), "2026-07-01", [], diar, {})
    capsys.readouterr().out.encode("cp866")
