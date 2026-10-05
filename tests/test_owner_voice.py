"""Образец голоса владельца (`<voices>/_owner/owner.json`): только векторы,
замена по устройству и встрече, не больше MAX_SAMPLES, сходство с учётом
устройства. Векторы синтетические, устройства и встречи выдуманы."""

import json

import numpy as np
import pytest

from meet import owner_voice, voices

DIM = 8
A = np.eye(DIM)[0]
B = np.eye(DIM)[1]


def _vec(*parts):
    v = np.zeros(DIM)
    for i, x in enumerate(parts):
        v[i] = x
    return v / np.linalg.norm(v)


def test_path_is_subfolder_of_voices(tmp_path):
    assert owner_voice.path(tmp_path) == tmp_path / "_owner" / "owner.json"


def test_path_defaults_to_voices_dir_from_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(voices, "voices_dir", lambda: tmp_path / "база")
    assert owner_voice.path() == tmp_path / "база" / "_owner" / "owner.json"


def test_load_without_file_is_empty(tmp_path):
    assert owner_voice.load(tmp_path) == []


def test_add_writes_vectors_only_and_loads_back(tmp_path):
    got = owner_voice.add(A, source="enroll", seconds=24.6, device="Микрофон (USB)",
                          quality=0.86, voices=tmp_path, date="2026-10-05")
    raw = json.loads(owner_voice.path(tmp_path).read_text(encoding="utf-8"))
    assert raw["version"] == 1 and raw["model"] == owner_voice.DIARIZATION_MODEL
    assert raw["suggestion"] is None
    (sample,) = raw["samples"]
    assert set(sample) == {"id", "embedding", "source", "date", "seconds", "device", "recording", "quality"}
    assert sample["id"] == got.id and len(sample["embedding"]) == DIM
    (back,) = owner_voice.load(tmp_path)
    assert back.id == got.id and back.source == "enroll" and back.device == "Микрофон (USB)"
    assert back.seconds == 24.6 and back.quality == 0.86 and back.recording is None
    assert np.allclose(back.embedding, A)


def test_owner_folder_is_invisible_to_voice_base(tmp_path):
    """База голосов читает *.json нерекурсивно: владелец не становится «человеком»."""
    owner_voice.add(A, source="enroll", seconds=20, voices=tmp_path)
    assert voices.load_voices(tmp_path) == {}


def test_bad_source_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        owner_voice.add(A, source="угадал", seconds=5, voices=tmp_path)
    assert not owner_voice.path(tmp_path).exists()


def test_bad_embedding_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        owner_voice.add(np.array([np.nan] * DIM), source="enroll", seconds=5, voices=tmp_path)
    with pytest.raises(ValueError):
        owner_voice.add(np.zeros(DIM), source="enroll", seconds=5, voices=tmp_path)


def test_enroll_on_same_device_replaces_other_device_kept(tmp_path):
    first = owner_voice.add(A, source="enroll", seconds=20, device="USB", voices=tmp_path)
    other = owner_voice.add(B, source="enroll", seconds=20, device="Onboard", voices=tmp_path)
    again = owner_voice.add(_vec(1, 0.1), source="enroll", seconds=25, device="USB", voices=tmp_path)
    ids = [s.id for s in owner_voice.load(tmp_path)]
    assert first.id not in ids and set(ids) == {other.id, again.id}


def test_meeting_sample_of_same_recording_replaces(tmp_path):
    first = owner_voice.add(A, source="meeting", seconds=60, recording="2026-10-05_13-32", voices=tmp_path)
    other = owner_voice.add(A, source="meeting", seconds=60, recording="2026-10-04_10-00", voices=tmp_path)
    again = owner_voice.add(B, source="meeting", seconds=90, recording="2026-10-05_13-32", voices=tmp_path)
    ids = [s.id for s in owner_voice.load(tmp_path)]
    assert first.id not in ids and set(ids) == {other.id, again.id}


def test_enroll_and_meeting_do_not_replace_each_other(tmp_path):
    a = owner_voice.add(A, source="enroll", seconds=20, device="USB", voices=tmp_path)
    b = owner_voice.add(A, source="meeting", seconds=60, device="USB", recording="r1", voices=tmp_path)
    assert {s.id for s in owner_voice.load(tmp_path)} == {a.id, b.id}


def test_auto_sample_replaces_previous_auto(tmp_path):
    first = owner_voice.add(A, source="auto", seconds=300, voices=tmp_path)
    again = owner_voice.add(B, source="auto", seconds=400, voices=tmp_path)
    assert [s.id for s in owner_voice.load(tmp_path)] == [again.id]
    assert first.id != again.id


def test_at_most_max_samples_oldest_go_first(tmp_path):
    added = [owner_voice.add(A, source="meeting", seconds=60, recording=f"r{i}", voices=tmp_path)
             for i in range(owner_voice.MAX_SAMPLES + 2)]
    ids = [s.id for s in owner_voice.load(tmp_path)]
    assert ids == [s.id for s in added[2:]]


def test_remove_by_id(tmp_path):
    a = owner_voice.add(A, source="enroll", seconds=20, device="USB", voices=tmp_path)
    b = owner_voice.add(B, source="enroll", seconds=20, device="Onboard", voices=tmp_path)
    assert owner_voice.remove(a.id, tmp_path) is True
    assert [s.id for s in owner_voice.load(tmp_path)] == [b.id]
    assert owner_voice.remove("нет-такого", tmp_path) is False
    assert owner_voice.remove(a.id, tmp_path / "пусто") is False


def test_other_keys_survive_writes(tmp_path):
    """suggestion (авто-образец, T8) и прочие ключи файла правка образцов не теряет."""
    p = owner_voice.path(tmp_path)
    p.parent.mkdir(parents=True)
    suggestion = {"embedding": [0.0] * DIM, "meetings": ["r1"], "samples": []}
    p.write_text(json.dumps({"version": 1, "model": owner_voice.DIARIZATION_MODEL,
                             "samples": [], "suggestion": suggestion, "extra": 1}), encoding="utf-8")
    s = owner_voice.add(A, source="enroll", seconds=20, voices=tmp_path)
    owner_voice.remove(s.id, tmp_path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["suggestion"] == suggestion and raw["extra"] == 1


def test_broken_file_and_samples_are_skipped(tmp_path):
    p = owner_voice.path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("{не json", encoding="utf-8")
    assert owner_voice.load(tmp_path) == []
    p.write_text(json.dumps({"version": 1, "model": owner_voice.DIARIZATION_MODEL, "samples": [
        {"id": "x", "embedding": "мусор", "source": "enroll"},
        {"id": "y", "embedding": [1.0] + [0.0] * (DIM - 1), "source": "enroll", "seconds": 20},
        "не образец"]}), encoding="utf-8")
    assert [s.id for s in owner_voice.load(tmp_path)] == ["y"]


def test_samples_of_other_model_are_ignored(tmp_path):
    p = owner_voice.path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"version": 1, "model": "другая/модель", "samples": [
        {"id": "y", "embedding": [1.0] * DIM, "source": "enroll", "seconds": 20}]}), encoding="utf-8")
    assert owner_voice.load(tmp_path) == []


def test_write_is_atomic_no_tmp_left(tmp_path):
    owner_voice.add(A, source="enroll", seconds=20, voices=tmp_path)
    assert [f.name for f in owner_voice.path(tmp_path).parent.iterdir()] == ["owner.json"]


def test_score_is_max_cos(tmp_path):
    owner_voice.add(A, source="enroll", seconds=20, device="USB", voices=tmp_path)
    owner_voice.add(B, source="meeting", seconds=60, recording="r1", voices=tmp_path)
    samples = owner_voice.load(tmp_path)
    assert owner_voice.score(_vec(1, 0.2), samples) == pytest.approx(float(_vec(1, 0.2) @ A))
    assert owner_voice.score(B * 3, samples) == pytest.approx(1.0)
    assert owner_voice.score(A, []) == -1.0
    assert owner_voice.score(np.ones(DIM + 1), samples) == -1.0  # другая размерность
    assert owner_voice.score(np.array([np.nan] * DIM), samples) == -1.0


def test_score_prefers_samples_of_same_device(tmp_path):
    """Есть образцы этого микрофона — сравниваем с ними (и с образцами без
    устройства): чужой микрофон не завышает сходство."""
    owner_voice.add(A, source="enroll", seconds=20, device="USB", voices=tmp_path)
    owner_voice.add(B, source="enroll", seconds=20, device="Onboard", voices=tmp_path)
    samples = owner_voice.load(tmp_path)
    assert owner_voice.score(B, samples, device="USB") == pytest.approx(0.0)
    assert owner_voice.score(B, samples, device="Onboard") == pytest.approx(1.0)
    # Образцов этого устройства нет — все образцы.
    assert owner_voice.score(B, samples, device="Гарнитура") == pytest.approx(1.0)
    assert owner_voice.score(B, samples) == pytest.approx(1.0)


def test_centroid_weighted_by_seconds(tmp_path):
    owner_voice.add(A, source="enroll", seconds=30, device="USB", voices=tmp_path)
    owner_voice.add(B, source="enroll", seconds=10, device="Onboard", voices=tmp_path)
    c = owner_voice.centroid(owner_voice.load(tmp_path))
    assert np.linalg.norm(c) == pytest.approx(1.0)
    assert c[0] > c[1] > 0
    assert owner_voice.centroid([]) is None


def test_best_source_names_the_closest_sample(tmp_path):
    owner_voice.add(A, source="enroll", seconds=20, device="USB", voices=tmp_path)
    owner_voice.add(B, source="meeting", seconds=60, recording="r1", voices=tmp_path)
    samples = owner_voice.load(tmp_path)
    assert owner_voice.best_source(_vec(0.1, 1), samples) == "meeting"
    assert owner_voice.best_source(A, samples) == "enroll"
    assert owner_voice.best_source(A, []) is None
