"""Библиотека записей: чтение каталога как источника истины."""

import json

from meet import library
from meet.asr import Segment


def _recording(root, name="2026-08-18_11-00", *, stopped_s=None, transcript=None):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    if stopped_s is not None:
        (folder / "events.jsonl").write_text(
            json.dumps({"kind": "record.started", "at": 1}) + "\n"
            + json.dumps({"kind": "record.stopped", "at": 2,
                          "duration_s": stopped_s}) + "\n",
            encoding="utf-8",
        )
    if transcript is not None:
        library.write_transcript(folder, transcript)
    return folder


def test_describe_reads_folder(tmp_path):
    folder = _recording(tmp_path, stopped_s=612.5)
    card = library.describe(folder)
    assert card.id == "2026-08-18_11-00"
    assert card.started_at == "2026-08-18T11:00:00"
    assert card.duration_s == 612.5
    assert set(card.tracks) == {"sys", "mic"}
    assert card.has_transcript is False and card.has_voices is False


def test_folder_without_tracks_is_not_a_recording(tmp_path):
    empty = tmp_path / "2026-08-18_11-00"
    empty.mkdir()
    assert library.describe(empty) is None


def test_legacy_wav_recording_is_seen(tmp_path):
    """Старые записи в .wav должны находиться так же, как .opus."""
    folder = tmp_path / "2026-06-12_10-59"
    folder.mkdir()
    (folder / "sys.wav").write_bytes(b"x")
    (folder / "mic.wav").write_bytes(b"x")
    assert library.describe(folder).tracks["sys"].name == "sys.wav"


def test_duration_unknown_without_events(tmp_path):
    """Не выдумываем длительность: None честнее числа, которому нельзя верить."""
    assert library.describe(_recording(tmp_path)).duration_s is None


def test_markdown_transcript_counts_as_transcribed(tmp_path):
    folder = _recording(tmp_path)
    (folder / "2026-08-18_transcript.md").write_text("# Встреча", encoding="utf-8")
    assert library.describe(folder).has_transcript is True


def test_voices_sidecar_is_noticed(tmp_path):
    folder = _recording(tmp_path)
    (folder / "2026-08-18_transcript_speakers.json").write_text("{}", encoding="utf-8")
    assert library.describe(folder).has_voices is True


def test_listing_is_newest_first(tmp_path):
    _recording(tmp_path, "2026-08-16_09-00")
    _recording(tmp_path, "2026-08-18_11-00")
    _recording(tmp_path, "2026-08-17_15-30")
    assert [r["id"] for r in library.listing(tmp_path)] == [
        "2026-08-18_11-00", "2026-08-17_15-30", "2026-08-16_09-00",
    ]


def test_listing_of_missing_root_is_empty(tmp_path):
    assert library.listing(tmp_path / "нет") == []


def test_transcript_roundtrip(tmp_path):
    folder = _recording(tmp_path)
    data = library.segments_to_raw(
        [Segment(0.0, 1.5, "привет", "Спикер 1"),
         Segment(1.5, 2.0, "ага", "Вы", uncertain=True)],
        speakers={"SPEAKER_00": "Демьян"}, title="Встреча — 18.08.2026",
    )
    library.write_transcript(folder, data)
    back = library.read_transcript(folder)
    assert back["title"] == "Встреча — 18.08.2026"
    assert back["speakers"] == {"SPEAKER_00": "Демьян"}
    assert back["segments"][0] == {
        "start": 0.0, "end": 1.5, "speaker": "Спикер 1",
        "text": "привет", "uncertain": False,
    }
    assert back["segments"][1]["uncertain"] is True
    assert library.describe(folder).has_transcript is True
    assert library.describe(folder).title == "Встреча — 18.08.2026"


def test_broken_transcript_reads_as_none(tmp_path):
    folder = _recording(tmp_path)
    library.transcript_path(folder).write_text("{сломано", encoding="utf-8")
    assert library.read_transcript(folder) is None


def test_write_transcript_leaves_no_temp(tmp_path):
    folder = _recording(tmp_path)
    library.write_transcript(folder, {"version": 1, "segments": []})
    assert list(folder.glob("*.tmp")) == []


def test_meta_roundtrip_merges(tmp_path):
    folder = _recording(tmp_path)
    assert library.read_meta(folder) == {}
    library.write_meta(folder, {"source": "auto"})
    library.write_meta(folder, {"title": "Встреча с Acme"})
    assert library.read_meta(folder) == {"source": "auto", "title": "Встреча с Acme"}


def test_broken_meta_is_empty_not_error(tmp_path):
    folder = _recording(tmp_path)
    (folder / "meta.json").write_text("{битый", encoding="utf-8")
    assert library.read_meta(folder) == {}


def test_meta_title_wins_over_transcript_title(tmp_path):
    folder = _recording(tmp_path, transcript={"title": "Встреча — 18.08.2026",
                                              "segments": []})
    assert library.describe(folder).title == "Встреча — 18.08.2026"
    library.write_meta(folder, {"title": "Дейлик"})
    card = library.describe(folder)
    assert card.title == "Дейлик"
    assert card.source == "record"  # нет meta.source — обычная запись


import os
import time


def _media(tmp_path, name="созвон.mp4", mtime=None):
    src = tmp_path / "in" / name
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(b"media")
    if mtime is not None:
        os.utime(src, (mtime, mtime))
    return src


def test_create_import_makes_folder_with_meta(tmp_path):
    stamp = time.mktime((2026, 9, 28, 16, 4, 0, 0, 0, -1))
    src = _media(tmp_path, mtime=stamp)
    folder = library.create_import(tmp_path / "rec", src)
    assert folder.name == "2026-09-28_16-04_import"
    meta = library.read_meta(folder)
    assert meta["source"] == "import"
    assert meta["original_name"] == "созвон.mp4"
    assert meta["title"] == "созвон"
    card = library.describe(folder)  # ещё без дорожки — но уже в библиотеке
    assert card is not None and card.source == "import" and card.tracks == {}
    assert card.started_at == "2026-09-28T16:04:00"


def test_two_imports_with_same_mtime_get_distinct_folders(tmp_path):
    stamp = time.mktime((2026, 9, 28, 16, 4, 0, 0, 0, -1))
    a = library.create_import(tmp_path / "rec", _media(tmp_path, "a.mp3", stamp))
    b = library.create_import(tmp_path / "rec", _media(tmp_path, "b.mp3", stamp))
    assert a != b and b.name == "2026-09-28_16-04_import-2"


def test_create_import_rejects_unknown_format(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="формат"):
        library.create_import(tmp_path / "rec", _media(tmp_path, "notes.docx"))
    assert not (tmp_path / "rec").exists() or not any((tmp_path / "rec").iterdir())


def test_import_folder_track_is_source(tmp_path):
    folder = library.create_import(tmp_path / "rec", _media(tmp_path))
    (folder / "source.mp4").write_bytes(b"media")
    assert set(library.describe(folder).tracks) == {"source"}
