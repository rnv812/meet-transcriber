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


def test_transcript_time_is_reported(tmp_path):
    """Окну нужно время транскрипта, чтобы понять, свежее ли упавшая перерасшифровка."""
    assert library.describe(_recording(tmp_path)).transcript_at is None
    folder = _recording(tmp_path, "2026-08-19_11-00", transcript={"segments": []})
    card = library.describe(folder)
    assert card.transcript_at == library.transcript_path(folder).stat().st_mtime
    assert card.to_raw()["transcript_at"] == card.transcript_at


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


def test_create_import_survives_folder_appearing_after_check(tmp_path, monkeypatch):
    """Папку с тем же именем создал кто-то ещё между проверкой и mkdir (два
    импорта одновременно): берём следующий суффикс, чужую папку не трогаем."""
    from pathlib import Path

    stamp = time.mktime((2026, 9, 28, 16, 4, 0, 0, 0, -1))
    rec = tmp_path / "rec"
    taken = rec / "2026-09-28_16-04_import"
    taken.mkdir(parents=True)
    library.write_meta(taken, {"source": "import", "title": "чужой"})
    real_exists = Path.exists
    # «Проверка» не видит папку — так выглядит гонка изнутри.
    monkeypatch.setattr(Path, "exists", lambda self, *a, **k:
                        False if self.name.endswith("_import") else real_exists(self, *a, **k))
    folder = library.create_import(rec, _media(tmp_path, mtime=stamp))
    monkeypatch.undo()
    assert folder.name == "2026-09-28_16-04_import-2"
    assert library.read_meta(taken)["title"] == "чужой"


def test_search_matches_title_and_text(tmp_path):
    a = _recording(tmp_path, "2026-09-29_15-30", transcript={
        "title": "Созвон", "segments": [{"start": 0, "end": 1, "speaker": "Демьян",
                                         "text": "Обсудим CMDB"}]})
    _recording(tmp_path, "2026-09-30_16-04", transcript={
        "title": "Acme", "segments": []})
    assert [r["id"] for r in library.search(tmp_path, "cmdb")] == [a.name]
    assert [r["id"] for r in library.search(tmp_path, "acme")] == ["2026-09-30_16-04"]
    assert len(library.search(tmp_path, "")) == 2


def test_display_names_number_raw_labels_in_order():
    segs = [{"speaker": "SPEAKER_03"}, {"speaker": "Вы"}, {"speaker": "SPEAKER_01"},
            {"speaker": "SPEAKER_03"}]
    assert library.display_names(segs) == {"SPEAKER_03": "Спикер 1", "SPEAKER_01": "Спикер 2"}


def test_write_meta_from_many_threads_keeps_every_key(tmp_path):
    """Экспорт в фоне, переименование и итоги пишут meta.json одновременно:
    ни одно обновление не теряется и временные файлы не сталкиваются."""
    import threading

    folder = tmp_path / "2026-09-30_10-15"
    folder.mkdir()
    errors = []

    def work(n):
        try:
            for i in range(20):
                library.write_meta(folder, {f"k{n}": i})
        except Exception as e:  # pragma: no cover - сбой и есть провал теста
            errors.append(e)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert library.read_meta(folder) == {f"k{n}": 19 for n in range(8)}
    assert not list(folder.glob("*.tmp"))


def test_update_meta_is_read_modify_write_under_the_lock(tmp_path):
    import threading

    folder = tmp_path / "2026-09-30_10-15"
    folder.mkdir()
    library.write_meta(folder, {"count": 0})

    def bump():
        for _ in range(25):
            library.update_meta(folder, lambda d: {**d, "count": d["count"] + 1})

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert library.read_meta(folder)["count"] == 100
