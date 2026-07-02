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


def test_two_clusters_may_match_same_person():
    # диаризация разорвала одного говорящего — оба кластера получают одно имя
    voices = _voices(Демьян=[[1.0, 0.0]])
    got = match_speakers(
        {"SPEAKER_00": np.asarray([0.95, 0.05]), "SPEAKER_01": np.asarray([0.9, 0.1])}, voices
    )
    assert got == {"SPEAKER_00": "Демьян", "SPEAKER_01": "Демьян"}
