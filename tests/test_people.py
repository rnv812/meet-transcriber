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
    _voice(voices, "Демьян", [m1, tmp_path / "удалённая-запись"])
    _voice(voices, "Матвей", [])
    by_name = {p["name"]: p for p in people.listing(voices, rec)}
    assert by_name["Демьян"]["meetings"] == 1          # пропавшая запись не считается
    assert by_name["Демьян"]["seconds"] == 40
    assert by_name["Демьян"]["samples"] == 2
    assert by_name["Матвей"]["meetings"] == 0 and by_name["Матвей"]["seconds"] == 0
    assert by_name["Демьян"]["color"] == people.color("Демьян")
    assert by_name["Демьян"]["has_avatar"] is False


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
    voices = tmp_path / "voices"
    _voice(voices, "Аркаша", [])
    people.set_avatar("Аркаша", _png(), voices)
    people.rename("Аркаша", "Аркадий", voices)
    assert sorted(p.name for p in voices.iterdir()) == ["Аркадий.json", "Аркадий.png"]
    _voice(voices, "Демьян", [])
    with pytest.raises(FileExistsError):
        people.rename("Аркадий", "Демьян", voices)


def test_merge_appends_samples_and_removes_source(tmp_path):
    voices = tmp_path / "voices"
    _voice(voices, "Аркаша", [tmp_path / "a"])
    _voice(voices, "Аркадий", [tmp_path / "b"])
    people.merge("Аркаша", "Аркадий", voices)
    data = json.loads((voices / "Аркадий.json").read_text(encoding="utf-8"))
    assert len(data["samples"]) == 2
    assert not (voices / "Аркаша.json").exists()


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
        people.merge("Демьян", "Демьян", voices)
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
    people.rename("Аркаша", "Аркадий", voices)
    assert sorted(p.name for p in voices.iterdir()) == ["Аркадий.json", "Аркадий.png"]
    assert (voices / "Аркадий.png").read_bytes() == want
