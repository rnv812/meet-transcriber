import json
from pathlib import Path

import numpy as np
import pytest

from meet.voices import (
    THRESHOLD,
    add_sample,
    best_match,
    enroll,
    load_voices,
    match_speakers,
    read_sidecar,
    sidecar_path,
    write_sidecar,
)


def test_threshold_above_similar_impostor():
    # 0.75 — по кейсу 03.07.2026 (встреча 16-31): похожий чужой голос дал
    # cos 0.61 и при прежнем пороге 0.6 забрал чужое имя; свои — 0.89-1.00.
    assert THRESHOLD == 0.75


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


def _voices(**people):
    return {name: [np.asarray(v, dtype=np.float32) for v in vecs] for name, vecs in people.items()}


def test_best_match_returns_score_name_second():
    voices = {
        "Демьян": [np.array([1.0, 0.0], dtype=np.float32)],
        "Пётр": [np.array([0.0, 1.0], dtype=np.float32)],
    }
    score, name, second = best_match(np.array([1.0, 0.1], dtype=np.float32), voices)
    assert name == "Демьян"
    assert score > 0.99
    assert second is not None and second < 0.2


def test_best_match_single_person_second_is_none():
    voices = {"Демьян": [np.array([1.0, 0.0], dtype=np.float32)]}
    score, name, second = best_match(np.array([1.0, 0.0], dtype=np.float32), voices)
    assert name == "Демьян"
    assert second is None


def test_best_match_takes_max_over_samples():
    voices = {
        "Демьян": [
            np.array([0.0, 1.0], dtype=np.float32),
            np.array([1.0, 0.0], dtype=np.float32),
        ],
    }
    score, _, _ = best_match(np.array([1.0, 0.0], dtype=np.float32), voices)
    assert score > 0.99


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


def test_match_log_survives_cp866_console(capsys):
    # реальный сбой 02.07.2026: «→» в логе не кодируется в cp866 (консоль Windows),
    # print падал UnicodeEncodeError и except в _match_names глотал весь матчинг
    voices = _voices(Демьян=[[1.0, 0.0]])
    match_speakers(
        {"SPEAKER_00": np.asarray([0.9, 0.1]), "SPEAKER_01": np.asarray([0.0, 1.0])}, voices
    )
    capsys.readouterr().out.encode("cp866")


@pytest.mark.parametrize("bad", [r"..\..\x", "../x", "Демьян: ПМ", "x" * 81, " "])
def test_add_sample_rejects_unsafe_names(tmp_path, bad):
    """Имя человека = имя файла: `..`, `:` (альтернативный поток NTFS) и
    сверхдлинные имена не должны попасть на диск ни мимо папки голосов, ни в неё."""
    base = tmp_path / "voices"
    with pytest.raises(ValueError):
        add_sample(bad, [1.0, 0.0], source="recordings/a", date="2026-06-26", folder=base)
    assert not base.exists() or list(base.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) in ([], ["voices"])


def test_enroll_bad_name_is_friendly_and_writes_nothing(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    _make_sidecar(rec)
    base = tmp_path / "voices"
    with pytest.raises(SystemExit, match="имя"):
        enroll(str(rec), ["Спикер 1=Демьян", r"Спикер 2=..\..\x"], folder=base)
    assert load_voices(base) == {}


def test_enroll_renames_speakers_in_recording_transcript(tmp_path):
    """CLI/Claude-путь (`meet enroll`) должен давать тот же итог, что и окно:
    в transcript.json записи — имена, а не «Спикер N», иначе редактор и
    статистика людей их не видят."""
    from meet import library

    rec = tmp_path / "rec"
    rec.mkdir()
    _make_sidecar(rec)
    library.write_transcript(rec, {"version": 1, "segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Спикер 1", "text": "раз"},
        {"start": 1.0, "end": 2.0, "speaker": "Спикер 2", "text": "два"},
        {"start": 2.0, "end": 3.0, "speaker": "Спикер 3", "text": "три"}]})
    enroll(str(rec), ["Спикер 1=Демьян Петров", "SPEAKER_01=Пётр"], folder=tmp_path / "voices")
    data = library.read_transcript(rec)
    assert [s["speaker"] for s in data["segments"]] == ["Демьян Петров", "Пётр", "Спикер 3"]
    assert data["names"] == {"Спикер 1": "Демьян Петров", "Спикер 2": "Пётр"}
    assert not list(rec.glob("*.tmp"))


def test_enroll_without_transcript_creates_none(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    _make_sidecar(rec)
    enroll(str(rec), ["Спикер 1=Демьян"], folder=tmp_path / "voices")
    assert not (rec / "transcript.json").exists()


# --- образцы с id: точный откат из окна (панель «Спикеры») -------------------

def _file(folder, name):
    return json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))["samples"]


def test_add_sample_gives_each_sample_an_id(tmp_path):
    add_sample("Демьян", [1.0, 0.0], source="recordings/a", date="2026-06-26", folder=tmp_path)
    add_sample("Демьян", [0.0, 1.0], source="recordings/b", date="2026-06-27", folder=tmp_path)
    ids = [s["id"] for s in _file(tmp_path, "Демьян")]
    assert len(set(ids)) == 2 and all(isinstance(i, str) and i for i in ids)


def test_enroll_sample_reports_id_creation_and_what_it_replaced(tmp_path):
    from meet.voices import enroll_sample

    # Старый образец (до id) из той же встречи — без метки: он заменяется и
    # возвращается вызывающему, чтобы откат мог его вернуть.
    (tmp_path / "Демьян.json").write_text(json.dumps({"samples": [
        {"embedding": [0.5, 0.5], "source": "rec/a", "date": "2026-06-01"}]}), encoding="utf-8")
    got = enroll_sample("Демьян", [1.0, 0.0], source="rec/a", date="2026-06-26",
                        label="SPEAKER_01", recording="a", folder=tmp_path)
    assert got["created"] is False
    assert [r["sample"]["embedding"] for r in got["replaced"]] == [[0.5, 0.5]]
    samples = _file(tmp_path, "Демьян")
    assert [s["id"] for s in samples] == [got["sample_id"]]
    assert samples[0]["label"] == "SPEAKER_01" and samples[0]["recording"] == "a"

    new = enroll_sample("Анна", [0.0, 1.0], source="rec/a", date="2026-06-26",
                        label="SPEAKER_02", recording="a", folder=tmp_path)
    assert new["created"] is True and new["replaced"] == []


def test_enroll_sample_keeps_other_clusters_of_the_same_meeting(tmp_path):
    from meet.voices import enroll_sample

    # Диаризация разбила человека на два кластера: оба — его образцы.
    enroll_sample("Демьян", [1.0, 0.0], source="rec/a", date="d", label="SPEAKER_01",
                  recording="a", folder=tmp_path)
    enroll_sample("Демьян", [0.9, 0.1], source="rec/a", date="d", label="SPEAKER_03",
                  recording="a", folder=tmp_path)
    assert len(_file(tmp_path, "Демьян")) == 2


def test_enroll_sample_moves_the_cluster_from_another_person(tmp_path):
    from meet.voices import enroll_sample

    first = enroll_sample("Демьян", [1.0, 0.0], source="rec/a", date="d", label="SPEAKER_01",
                          recording="a", folder=tmp_path)
    moved = enroll_sample("Пётр", [1.0, 0.0], source="rec/a", date="d", label="SPEAKER_01",
                          recording="a", folder=tmp_path)
    assert _file(tmp_path, "Демьян") == []
    assert moved["replaced"] == [{"person": "Демьян", "sample": {
        "embedding": [1.0, 0.0], "source": "rec/a", "date": "d", "id": first["sample_id"],
        "label": "SPEAKER_01", "recording": "a"}}]


def test_remove_sample_by_id_and_restore_replaced(tmp_path):
    from meet.voices import enroll_sample, remove_sample

    (tmp_path / "Демьян.json").write_text(json.dumps({"samples": [
        {"embedding": [0.5, 0.5], "source": "rec/a", "date": "2026-06-01"},
        {"embedding": [0.1, 0.9], "source": "rec/b", "date": "2026-06-02"}]}), encoding="utf-8")
    got = enroll_sample("Демьян", [1.0, 0.0], source="rec/a", date="2026-06-26",
                        label="SPEAKER_01", recording="a", folder=tmp_path)
    left = remove_sample(got["sample_id"], "Демьян", folder=tmp_path, restore=got["replaced"])
    assert left == {"left": {"Демьян": 2}, "skipped": []}
    assert sorted(s["source"] for s in _file(tmp_path, "Демьян")) == ["rec/a", "rec/b"]
    assert all("id" not in s for s in _file(tmp_path, "Демьян"))  # старые — как были


def test_remove_sample_finds_it_after_the_person_was_renamed(tmp_path):
    from meet.voices import enroll_sample, remove_sample

    got = enroll_sample("Демьян", [1.0, 0.0], source="rec/a", date="d", label="SPEAKER_01",
                        recording="a", folder=tmp_path)
    (tmp_path / "Демьян.json").rename(tmp_path / "Демьян Петров.json")
    assert remove_sample(got["sample_id"], "Демьян", folder=tmp_path) == {
        "left": {"Демьян Петров": 0}, "skipped": []}


def test_remove_sample_does_not_resurrect_a_deleted_person(tmp_path):
    from meet.voices import enroll_sample, remove_sample

    enroll_sample("Демьян", [1.0, 0.0], source="rec/a", date="d", label="SPEAKER_01",
                  recording="a", folder=tmp_path)
    moved = enroll_sample("Пётр", [1.0, 0.0], source="rec/a", date="d", label="SPEAKER_01",
                          recording="a", folder=tmp_path)
    (tmp_path / "Демьян.json").unlink()  # удалили в окне «Голоса»
    got = remove_sample(moved["sample_id"], "Пётр", folder=tmp_path, restore=moved["replaced"])
    assert got == {"left": {"Пётр": 0}, "skipped": ["Демьян"]}
    assert not (tmp_path / "Демьян.json").exists()


def test_old_samples_without_id_still_load(tmp_path):
    (tmp_path / "Демьян.json").write_text(json.dumps({"samples": [
        {"embedding": [1.0, 0.0], "source": "rec/a", "date": "2026-06-01"}]}), encoding="utf-8")
    assert np.allclose(load_voices(tmp_path)["Демьян"][0], [1.0, 0.0])
