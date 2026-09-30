"""Люди базы голосов: список со статистикой, образец, аватары, правка базы."""

import io
import json

import pytest

from meet import library, people


def _voice(voices, name, sources):
    voices.mkdir(parents=True, exist_ok=True)
    samples = [{"embedding": [0.1, 0.2], "source": str(s), "date": "2026-09-30"}
               for s in sources]
    (voices / f"{name}.json").write_text(json.dumps({"samples": samples}),
                                         encoding="utf-8")


def _meeting(recordings, rid, segments):
    folder = recordings / rid
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    library.write_transcript(folder, {"segments": segments})
    return folder


def _png(size=(40, 30)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (200, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("bad", ["", "  ", "..", "a/b", "a\\b", "C:x", "имя?", "x" * 81])
def test_valid_name_rejects_path_tricks(bad):
    with pytest.raises(ValueError):
        people.valid_name(bad)


def test_valid_name_keeps_cyrillic_and_spaces():
    assert people.valid_name("  Демьян Петров ") == "Демьян Петров"


def test_listing_counts_meetings_and_seconds(tmp_path):
    rec, voices = tmp_path / "rec", tmp_path / "voices"
    m1 = _meeting(rec, "2026-09-29_15-30", [
        {"start": 0, "end": 10, "speaker": "Демьян", "text": "а"},
        {"start": 10, "end": 15, "speaker": "Матвей", "text": "б"},
        {"start": 15, "end": 45, "speaker": "Демьян", "text": "в"}])
    _meeting(rec, "2026-09-30_10-00", [
        {"start": 0, "end": 20, "speaker": "Матвей", "text": "г"}])
    _voice(voices, "Демьян", [m1, tmp_path / "удалённая-запись"])
    _voice(voices, "Матвей", [])
    _voice(voices, "Пётр", [m1])
    by_name = {p["name"]: p for p in people.listing(voices, rec)}
    assert by_name["Демьян"]["meetings"] == 1
    assert by_name["Демьян"]["seconds"] == 40
    assert by_name["Демьян"]["samples"] == 2
    # Статистика — из всех записей библиотеки, а не только из источников образцов.
    assert by_name["Матвей"]["meetings"] == 2 and by_name["Матвей"]["seconds"] == 25
    assert by_name["Матвей"]["samples"] == 0
    # Образец из записи есть, а реплик в ней нет — встреч ноль.
    assert by_name["Пётр"]["meetings"] == 0 and by_name["Пётр"]["seconds"] == 0
    assert by_name["Демьян"]["color"] == people.color("Демьян")
    assert by_name["Демьян"]["has_avatar"] is False


def test_stats_include_meetings_recognised_without_enrolled_sample(tmp_path):
    """Человека узнали на встрече автоматически — образца из неё в базе нет,
    но встреча с ним была, и его речь в ней считается."""
    rec, voices = tmp_path / "rec", tmp_path / "voices"
    enrolled = _meeting(rec, "2026-09-01_10-00", [
        {"start": 0, "end": 30, "speaker": "Демьян", "text": "первая"}])
    _meeting(rec, "2026-09-29_15-30", [
        {"start": 0, "end": 12, "speaker": "Демьян", "text": "узнан сам"},
        {"start": 12, "end": 20, "speaker": "Спикер 2", "text": "чужой"}])
    _voice(voices, "Демьян", [enrolled])
    got = people.listing(voices, rec)[0]
    assert got["meetings"] == 2 and got["seconds"] == 42 and got["samples"] == 1
    assert people.sample("Демьян", voices, rec)["recording"] == "2026-09-29_15-30"


def test_stats_ignore_folders_outside_the_library(tmp_path):
    """Папка без дорожек (не запись) с transcript.json — не встреча."""
    rec, voices = tmp_path / "rec", tmp_path / "voices"
    stray = rec / "черновик"
    stray.mkdir(parents=True)
    library.write_transcript(stray, {"segments": [
        {"start": 0, "end": 99, "speaker": "Демьян", "text": "x"}]})
    _voice(voices, "Демьян", [])
    got = people.listing(voices, rec)[0]
    assert got["meetings"] == 0 and got["seconds"] == 0
    assert people.sample("Демьян", voices, rec) is None


def test_listing_reads_each_transcript_once(tmp_path, monkeypatch):
    rec, voices = tmp_path / "rec", tmp_path / "voices"
    for i in range(3):
        _meeting(rec, f"2026-09-2{i}_10-00", [
            {"start": 0, "end": 5, "speaker": "Демьян", "text": "а"},
            {"start": 5, "end": 9, "speaker": "Матвей", "text": "б"}])
    for name in ("Демьян", "Матвей", "Пётр"):
        _voice(voices, name, [])
    reads = []
    real = library.read_transcript
    monkeypatch.setattr(library, "read_transcript",
                        lambda folder: reads.append(folder.name) or real(folder))
    people.listing(voices, rec)
    assert sorted(reads) == ["2026-09-20_10-00", "2026-09-21_10-00", "2026-09-22_10-00"]


def test_sample_is_longest_turn_in_latest_meeting(tmp_path):
    rec, voices = tmp_path / "rec", tmp_path / "voices"
    old = _meeting(rec, "2026-09-01_10-00", [
        {"start": 0, "end": 90, "speaker": "Демьян", "text": "старое"}])
    new = _meeting(rec, "2026-09-29_15-30", [
        {"start": 5, "end": 12, "speaker": "Демьян", "text": "а"},
        {"start": 20, "end": 50, "speaker": "Демьян", "text": "б"}])
    _voice(voices, "Демьян", [old, new])
    assert people.sample("Демьян", voices, rec) == {
        "recording": "2026-09-29_15-30", "start": 20, "end": 50, "track": "sys"}
    _voice(voices, "Пётр", [])
    assert people.sample("Пётр", voices, rec) is None


def test_avatar_is_normalised_to_square_png(tmp_path):
    voices = tmp_path / "voices"
    _voice(voices, "Демьян", [])
    path = people.set_avatar("Демьян", _png((400, 300)), voices)
    from PIL import Image

    with Image.open(path) as img:
        assert img.format == "PNG" and img.size == (256, 256)
    assert people.listing(voices, tmp_path / "rec")[0]["has_avatar"] is True
    people.clear_avatar("Демьян", voices)
    assert not path.exists()


def test_not_an_image_is_rejected_without_leftovers(tmp_path):
    voices = tmp_path / "voices"
    _voice(voices, "Демьян", [])
    with pytest.raises(ValueError, match="не изображение"):
        people.set_avatar("Демьян", b"definitely not a png", voices)
    assert sorted(p.name for p in voices.iterdir()) == ["Демьян.json"]


def test_avatar_for_unknown_person_is_rejected(tmp_path):
    with pytest.raises(KeyError):
        people.set_avatar("Никто", _png(), tmp_path / "voices")


def test_rename_moves_voice_and_avatar(tmp_path):
    voices, rec = tmp_path / "voices", tmp_path / "rec"
    _voice(voices, "Аркаша", [])
    people.set_avatar("Аркаша", _png(), voices)
    people.rename("Аркаша", "Аркадий", voices, rec)
    assert sorted(p.name for p in voices.iterdir()) == ["Аркадий.json", "Аркадий.png"]
    _voice(voices, "Демьян", [])
    with pytest.raises(FileExistsError):
        people.rename("Аркадий", "Демьян", voices, rec)


def test_rename_rewrites_transcripts_and_keeps_stats(tmp_path):
    rec, voices = tmp_path / "rec", tmp_path / "voices"
    m1 = _meeting(rec, "2026-09-01_10-00", [
        {"start": 0, "end": 30, "speaker": "Аркаша", "text": "а"},
        {"start": 30, "end": 35, "speaker": "Демьян", "text": "б"}])
    _meeting(rec, "2026-09-29_15-30", [
        {"start": 0, "end": 10, "speaker": "Аркаша", "text": "в"}])
    _voice(voices, "Аркаша", [m1])
    before = people.listing(voices, rec)[0]
    people.rename("Аркаша", "Аркадий", voices, rec)
    after = {p["name"]: p for p in people.listing(voices, rec)}["Аркадий"]
    assert (after["meetings"], after["seconds"]) == (before["meetings"], before["seconds"]) == (2, 40)
    speakers = [s["speaker"] for s in library.read_transcript(m1)["segments"]]
    assert speakers == ["Аркадий", "Демьян"]
    assert not list(rec.rglob("*.tmp"))


def test_case_only_rename(tmp_path):
    """«демьян» → «Демьян»: на Windows это тот же файл, но переименовать можно."""
    rec, voices = tmp_path / "rec", tmp_path / "voices"
    m = _meeting(rec, "2026-09-29_15-30", [
        {"start": 0, "end": 10, "speaker": "демьян", "text": "а"}])
    _voice(voices, "демьян", [m])
    people.set_avatar("демьян", _png(), voices)
    people.rename("демьян", "Демьян", voices, rec)
    assert sorted(p.name for p in voices.iterdir()) == ["Демьян.json", "Демьян.png"]
    assert library.read_transcript(m)["segments"][0]["speaker"] == "Демьян"
    assert people.listing(voices, rec)[0]["seconds"] == 10


def test_merge_appends_samples_and_removes_source(tmp_path):
    voices, rec = tmp_path / "voices", tmp_path / "rec"
    _voice(voices, "Аркаша", [tmp_path / "a"])
    _voice(voices, "Аркадий", [tmp_path / "b"])
    people.merge("Аркаша", "Аркадий", voices, rec)
    data = json.loads((voices / "Аркадий.json").read_text(encoding="utf-8"))
    assert len(data["samples"]) == 2
    assert not (voices / "Аркаша.json").exists()
    assert sorted(p.name for p in voices.iterdir()) == ["Аркадий.json"]  # без .tmp


def test_merge_accumulates_stats_and_rewrites_transcripts(tmp_path):
    rec, voices = tmp_path / "rec", tmp_path / "voices"
    m1 = _meeting(rec, "2026-09-01_10-00", [
        {"start": 0, "end": 30, "speaker": "Аркаша", "text": "а"}])
    m2 = _meeting(rec, "2026-09-29_15-30", [
        {"start": 0, "end": 10, "speaker": "Аркадий", "text": "б"},
        {"start": 10, "end": 15, "speaker": "Аркаша", "text": "в"}])
    _voice(voices, "Аркаша", [m1])
    _voice(voices, "Аркадий", [m2])
    people.merge("Аркаша", "Аркадий", voices, rec)
    got = people.listing(voices, rec)
    assert [p["name"] for p in got] == ["Аркадий"]
    assert (got[0]["meetings"], got[0]["seconds"], got[0]["samples"]) == (2, 45, 2)
    assert library.read_transcript(m1)["segments"][0]["speaker"] == "Аркадий"
    assert {s["speaker"] for s in library.read_transcript(m2)["segments"]} == {"Аркадий"}


def test_merge_with_case_variant_of_self_is_rejected(tmp_path):
    voices, rec = tmp_path / "voices", tmp_path / "rec"
    _voice(voices, "Демьян", [tmp_path / "a"])
    if not (voices / "демьян.json").exists():
        pytest.skip("регистрозависимая файловая система")
    with pytest.raises(ValueError):
        people.merge("демьян", "Демьян", voices, rec)
    assert (voices / "Демьян.json").exists()


def test_delete_removes_voice_and_avatar(tmp_path):
    voices = tmp_path / "voices"
    _voice(voices, "Демьян", [])
    people.set_avatar("Демьян", _png(), voices)
    people.delete("Демьян", voices)
    assert list(voices.iterdir()) == []


def test_color_is_stable_hex():
    assert people.color("Демьян") == people.color("Демьян")
    assert people.color("Демьян") != people.color("Матвей")
    assert people.color("Демьян").startswith("#") and len(people.color("Демьян")) == 7


def test_merge_with_self_is_rejected_and_keeps_voice(tmp_path):
    voices = tmp_path / "voices"
    _voice(voices, "Демьян", [tmp_path / "a"])
    with pytest.raises(ValueError):
        people.merge("Демьян", "Демьян", voices, tmp_path / "rec")
    assert (voices / "Демьян.json").exists()


def test_malformed_turns_are_skipped(tmp_path):
    rec, voices = tmp_path / "rec", tmp_path / "voices"
    m = _meeting(rec, "2026-09-29_15-30", [
        {"start": 0, "end": 10, "speaker": "Демьян", "text": "ок"},
        {"start": 1, "speaker": "Демьян", "text": "нет end"},
        {"start": None, "end": 5, "speaker": "Демьян", "text": "None"},
        {"start": 1, "end": "abc", "speaker": "Демьян", "text": "мусор"}])
    _voice(voices, "Демьян", [m])
    assert people.listing(voices, rec)[0]["seconds"] == 10
    assert people.sample("Демьян", voices, rec)["end"] == 10


def test_rename_over_orphan_avatar(tmp_path):
    voices = tmp_path / "voices"
    _voice(voices, "Аркаша", [])
    people.set_avatar("Аркаша", _png((50, 50)), voices)
    (voices / "Аркадий.png").write_bytes(b"stale")
    want = (voices / "Аркаша.png").read_bytes()
    people.rename("Аркаша", "Аркадий", voices, tmp_path / "rec")
    assert sorted(p.name for p in voices.iterdir()) == ["Аркадий.json", "Аркадий.png"]
    assert (voices / "Аркадий.png").read_bytes() == want
