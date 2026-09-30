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


def _run_single_capturing(monkeypatch, tmp_path, **kwargs):
    """Запустить _transcribe_single с заглушками аудио/моделей и снять,
    что дошло до diarize_wav (exclusive) и split_by_speaker (overlaps)."""
    import meet.transcribe as tr

    calls = {}
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(
        tr, "transcribe_wav", lambda p, h: [Segment(0.0, 1.0, "привет")]
    )

    def fake_diarize(path, num_speakers=None, exclusive=False):
        calls["exclusive"] = exclusive
        return Diarization(
            turns=[(0.0, 1.0, "SPEAKER_00")],
            overlaps=None if exclusive else [(0.4, 0.8)],
        )

    def fake_split(segments, turns, overlaps=None):
        calls["overlaps"] = overlaps
        return segments

    monkeypatch.setattr(tr, "diarize_wav", fake_diarize)
    monkeypatch.setattr(tr, "split_by_speaker", fake_split)
    src = tmp_path / "a.wav"
    src.write_bytes(b"x")
    tr._transcribe_single(src, None, None, align=False, **kwargs)
    return calls


def test_transcribe_single_default_is_overlap_aware(monkeypatch, tmp_path):
    calls = _run_single_capturing(monkeypatch, tmp_path)
    assert calls["exclusive"] is False
    assert calls["overlaps"] == [(0.4, 0.8)]


def test_transcribe_single_no_overlap_goes_exclusive(monkeypatch, tmp_path):
    calls = _run_single_capturing(monkeypatch, tmp_path, overlap=False)
    assert calls["exclusive"] is True
    assert calls["overlaps"] is None


# --- прогресс по ступеням пайплайна ---


def _progress_stages(monkeypatch, tmp_path, folder=False, align=False):
    """Прогнать пайплайн с заглушками и снять последовательность ступеней."""
    import meet.transcribe as tr
    from meet import events

    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h: [Segment(0.0, 1.0, "а")])
    monkeypatch.setattr(
        tr, "diarize_wav",
        lambda p, num_speakers=None, exclusive=False: Diarization(turns=[]),
    )
    monkeypatch.setattr(tr, "split_by_speaker", lambda s, t, o=None: s)
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled: s)

    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    if folder:
        (tmp_path / "sys.opus").write_bytes(b"x")
        (tmp_path / "mic.opus").write_bytes(b"x")
        tr.transcribe(str(tmp_path), align=align, bus=bus)
    else:
        src = tmp_path / "a.wav"
        src.write_bytes(b"x")
        tr.transcribe(str(src), align=align, bus=bus)
    return [e.data["stage"] for e in seen if e.kind == "progress"], seen


def test_progress_reports_pipeline_order_for_folder(monkeypatch, tmp_path):
    stages, _ = _progress_stages(monkeypatch, tmp_path, folder=True)
    # порядок ступеней, как их проходит двухдорожечный пайплайн
    assert stages[0] == "convert" and stages[-1] == "render"
    assert stages.index("diarize") < stages.index("voices")
    assert "asr" in stages


def test_progress_skips_align_when_disabled(monkeypatch, tmp_path):
    stages, _ = _progress_stages(monkeypatch, tmp_path, folder=True, align=False)
    assert "align" not in stages


def test_progress_reports_align_when_enabled(monkeypatch, tmp_path):
    stages, _ = _progress_stages(monkeypatch, tmp_path, folder=True, align=True)
    assert "align" in stages


def test_progress_counts_both_tracks(monkeypatch, tmp_path):
    _, seen = _progress_stages(monkeypatch, tmp_path, folder=True)
    convert = [e for e in seen if e.data.get("stage") == "convert"]
    assert convert[0].data["total"] == 2  # sys и mic
    assert convert[-1].data["done"] == 2


def test_progress_final_event_names_result(monkeypatch, tmp_path):
    _, seen = _progress_stages(monkeypatch, tmp_path, folder=True)
    last = [e for e in seen if e.data.get("stage") == "render"][-1]
    assert last.data["note"].endswith("_transcript.md")


def test_single_file_progress_has_one_track(monkeypatch, tmp_path):
    _, seen = _progress_stages(monkeypatch, tmp_path, folder=False)
    convert = [e for e in seen if e.data.get("stage") == "convert"]
    assert convert[0].data["total"] == 1


def test_transcribe_works_without_bus(monkeypatch, tmp_path):
    """Шина необязательна: CLI зовёт transcribe как раньше."""
    stages, _ = _progress_stages(monkeypatch, tmp_path, folder=True)
    assert stages  # заглушки те же, но вызов без bus проверяем отдельно
    import meet.transcribe as tr

    src = tmp_path / "b.wav"
    src.write_bytes(b"x")
    assert tr.transcribe(str(src), align=False).suffix == ".md"


# --- имя владельца микрофона ---------------------------------------------


def test_mic_track_uses_speaker_name_from_settings(monkeypatch, tmp_path):
    """«Вы» — дефолт, а не константа: кому-то удобнее собственное имя."""
    import json

    import meet.transcribe as tr
    from meet.asr import Segment as Seg

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "config.json").write_text(
        json.dumps({"recording": {"speaker_name": "Алексей"}}), encoding="utf-8"
    )
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h: [Seg(0.0, 1.0, "а")])
    monkeypatch.setattr(
        tr, "diarize_wav",
        lambda p, num_speakers=None, exclusive=False: Diarization(turns=[]),
    )
    monkeypatch.setattr(tr, "split_by_speaker", lambda s, t, o=None: s)
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled: s)
    (tmp_path / "sys.opus").write_bytes(b"x")
    (tmp_path / "mic.opus").write_bytes(b"x")
    segments, _, _ = tr._transcribe_two_track(tmp_path, None, None, align=False)
    assert any(seg.speaker == "Алексей" for seg in segments)


def test_import_folder_uses_single_track_and_writes_into_folder(tmp_path, monkeypatch):
    import meet.transcribe as tr
    from meet import library

    folder = tmp_path / "2026-09-28_16-04_import"
    folder.mkdir()
    (folder / "source.mp4").write_bytes(b"media")
    seen = {}

    def fake_single(src, speakers, hotwords, align, overlap, bus):
        seen["src"] = src
        return [Segment(0.0, 1.0, "привет", speaker="SPEAKER_00")], None, {}

    monkeypatch.setattr(tr, "_transcribe_single", fake_single)
    out = tr.transcribe(str(folder))
    assert seen["src"] == folder / "source.mp4"
    assert out == folder / "2026-09-28_transcript.md"
    assert library.read_transcript(folder)["segments"][0]["text"] == "привет"
