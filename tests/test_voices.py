import json

import numpy as np

from meet.voices import add_sample, load_voices


def test_add_sample_creates_file_and_load_roundtrips(tmp_path):
    add_sample("Демьян Петров", [1.0, 0.0], source="recordings/a", date="2026-06-26", folder=tmp_path)
    voices = load_voices(tmp_path)
    assert list(voices) == ["Демьян Петров"]
    assert np.allclose(voices["Демьян Петров"][0], [1.0, 0.0])


def test_add_sample_same_source_replaces_not_duplicates(tmp_path):
    add_sample("Демьян", [1.0, 0.0], source="recordings/a", date="2026-06-26", folder=tmp_path)
    add_sample("Демьян", [0.0, 1.0], source="recordings/a", date="2026-06-27", folder=tmp_path)
    voices = load_voices(tmp_path)
    assert len(voices["Демьян"]) == 1
    assert np.allclose(voices["Демьян"][0], [0.0, 1.0])


def test_add_sample_other_source_appends(tmp_path):
    add_sample("Демьян", [1.0, 0.0], source="recordings/a", date="2026-06-26", folder=tmp_path)
    add_sample("Демьян", [0.0, 1.0], source="recordings/b", date="2026-06-27", folder=tmp_path)
    assert len(load_voices(tmp_path)["Демьян"]) == 2


def test_load_voices_missing_folder_returns_empty(tmp_path):
    assert load_voices(tmp_path / "нет") == {}


def test_load_voices_skips_broken_json(tmp_path, capsys):
    (tmp_path / "Битый.json").write_text("{не json", encoding="utf-8")
    add_sample("Демьян", [1.0], source="a", date="2026-06-26", folder=tmp_path)
    voices = load_voices(tmp_path)
    assert list(voices) == ["Демьян"]
    assert "Битый.json" in capsys.readouterr().out


from meet.voices import match_speakers


def _voices(**people):
    return {name: [np.asarray(v, dtype=np.float32) for v in vecs] for name, vecs in people.items()}


def test_match_confident_hit():
    voices = _voices(Демьян=[[1.0, 0.0]], Пётр=[[0.0, 1.0]])
    got = match_speakers({"SPEAKER_00": np.asarray([0.9, 0.1])}, voices)
    assert got == {"SPEAKER_00": "Демьян"}


def test_match_below_threshold_rejected(capsys):
    voices = _voices(Демьян=[[1.0, 0.0]])
    got = match_speakers({"SPEAKER_00": np.asarray([0.3, 0.95])}, voices)
    assert got == {}
    assert "не распознан" in capsys.readouterr().out


def test_match_small_margin_rejected():
    # два человека почти одинаково близки — не угадываем
    voices = _voices(Демьян=[[1.0, 0.0]], Пётр=[[0.95, 0.31]])
    got = match_speakers({"SPEAKER_00": np.asarray([0.99, 0.15])}, voices, threshold=0.5, margin=0.05)
    assert got == {}


def test_match_single_person_needs_only_threshold():
    voices = _voices(Демьян=[[1.0, 0.0]])
    got = match_speakers({"SPEAKER_00": np.asarray([0.9, 0.1])}, voices)
    assert got == {"SPEAKER_00": "Демьян"}


def test_match_best_sample_of_person_wins():
    # у человека несколько образцов — берётся максимум по ним
    voices = _voices(Демьян=[[0.0, 1.0], [1.0, 0.0]], Пётр=[[0.5, 0.5]])
    got = match_speakers({"SPEAKER_00": np.asarray([1.0, 0.05])}, voices)
    assert got == {"SPEAKER_00": "Демьян"}


def test_match_empty_base_returns_empty():
    assert match_speakers({"SPEAKER_00": np.asarray([1.0])}, {}) == {}


def test_two_clusters_may_match_same_person():
    # диаризация разорвала одного говорящего — оба кластера получают одно имя
    voices = _voices(Демьян=[[1.0, 0.0]])
    got = match_speakers(
        {"SPEAKER_00": np.asarray([0.95, 0.05]), "SPEAKER_01": np.asarray([0.9, 0.1])}, voices
    )
    assert got == {"SPEAKER_00": "Демьян", "SPEAKER_01": "Демьян"}


from pathlib import Path

from meet.voices import read_sidecar, sidecar_path, write_sidecar


def test_sidecar_path_for_folder_transcript():
    assert sidecar_path(Path("r/2026-07-01_transcript.md")) == Path("r/2026-07-01_speakers.json")


def test_sidecar_path_for_single_file():
    assert sidecar_path(Path("r/meeting.md")) == Path("r/meeting_speakers.json")


def test_sidecar_roundtrip(tmp_path):
    out_md = tmp_path / "2026-07-01_transcript.md"
    speakers = [{"label": "SPEAKER_00", "display": "Спикер 1", "embedding": [1.0, 0.0]}]
    p = write_sidecar(out_md, source="recordings/x", date="2026-07-01", speakers=speakers)
    assert p == tmp_path / "2026-07-01_speakers.json"
    data = read_sidecar(p)
    assert data["source"] == "recordings/x"
    assert data["speakers"][0]["display"] == "Спикер 1"


import pytest

from meet.voices import enroll


def _make_sidecar(folder, name="2026-07-01_speakers.json"):
    speakers = [
        {"label": "SPEAKER_00", "display": "Спикер 1", "embedding": [1.0, 0.0]},
        {"label": "SPEAKER_01", "display": "Спикер 2", "embedding": [0.0, 1.0]},
    ]
    p = folder / name
    p.write_text(
        json.dumps(
            {"model": "m", "source": "recordings/x", "date": "2026-07-01", "speakers": speakers},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


def test_enroll_by_display_and_label(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    _make_sidecar(rec)
    base = tmp_path / "voices"
    enroll(str(rec), ["Спикер 1=Демьян Петров", "SPEAKER_01=Пётр"], folder=base)
    voices = load_voices(base)
    assert set(voices) == {"Демьян Петров", "Пётр"}
    assert np.allclose(voices["Демьян Петров"][0], [1.0, 0.0])


def test_enroll_unknown_speaker_lists_available(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    _make_sidecar(rec)
    with pytest.raises(SystemExit, match="Спикер 1"):
        enroll(str(rec), ["Спикер 9=Демьян"], folder=tmp_path / "voices")


def test_enroll_no_sidecar_says_retranscribe(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    with pytest.raises(SystemExit, match="перетранскриб"):
        enroll(str(rec), ["Спикер 1=Демьян"], folder=tmp_path / "voices")


def test_enroll_bad_mapping_format(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    _make_sidecar(rec)
    with pytest.raises(SystemExit, match="="):
        enroll(str(rec), ["Спикер 1 Демьян"], folder=tmp_path / "voices")


def test_enroll_broken_sidecar_friendly_error(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "2026-07-01_speakers.json").write_text("{не json", encoding="utf-8")
    with pytest.raises(SystemExit, match="Битый сайдкар"):
        enroll(str(rec), ["Спикер 1=Демьян"], folder=tmp_path / "voices")
