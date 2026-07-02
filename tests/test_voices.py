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
