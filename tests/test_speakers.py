"""Панель «Спикеры» карточки: подсказки по базе голосов, применение набора
правок и их откат в рамках встречи (meta.json `speaker_history`).

Все данные выдуманные: имена, реплики и «голоса» — короткие векторы."""

import json

import pytest

from meet import library, speakers, voices

SOURCE = "C:/rec/2026-09-30_16-04"


def _seg(start, end, speaker, text, **extra):
    return {"start": start, "end": end, "speaker": speaker, "text": text,
            "uncertain": False, **extra}


SEGMENTS = [
    _seg(0.0, 4.0, "Спикер 1", "Добрый день, начинаем планёрку."),
    _seg(4.0, 6.0, "Спикер 2", "Да, я здесь."),
    _seg(9.0, 19.0, "Спикер 1", "Сначала про сроки поставки оборудования."),
    _seg(19.0, 21.0, "Спикер 3", "Можно вопрос?"),
    _seg(25.0, 29.0, "Спикер 2", "Склад подтвердил отгрузку на четверг."),
    _seg(29.0, 30.0, "Вы", "Отлично."),
]


@pytest.fixture
def meeting(tmp_path):
    root = tmp_path / "recordings"
    folder = root / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "created_at": "2026-09-30T17:00:00",
                                      "speakers": {}, "segments": [dict(s) for s in SEGMENTS]})
    (folder / "2026-09-30_16-04_speakers.json").write_text(json.dumps({
        "model": "m", "source": SOURCE, "date": "2026-09-30",
        "speakers": [
            {"label": "SPEAKER_00", "display": "Спикер 1", "embedding": [1.0, 0.0, 0.0]},
            {"label": "SPEAKER_01", "display": "Спикер 2", "embedding": [0.0, 1.0, 0.0]},
            {"label": "SPEAKER_02", "display": "Спикер 3", "embedding": [0.0, 0.0, 1.0]},
        ]}, ensure_ascii=False), encoding="utf-8")
    return folder


@pytest.fixture
def base(tmp_path):
    folder = tmp_path / "voices"
    folder.mkdir()
    return folder


def _person(base, name, *vectors, source="C:/rec/old"):
    samples = [{"embedding": list(v), "source": source, "date": "2026-09-01"} for v in vectors]
    (base / f"{name}.json").write_text(json.dumps({"samples": samples}, ensure_ascii=False),
                                       encoding="utf-8")


def _speakers_of(folder):
    return [s["speaker"] for s in library.read_transcript(folder)["segments"]]


def _samples(base, name):
    return json.loads((base / f"{name}.json").read_text(encoding="utf-8"))["samples"]


# --- обзор: доли, образцы фраз, подсказки ------------------------------------


def test_overview_lists_speakers_with_share_and_longest_clean_phrases(meeting, base):
    got = speakers.overview(meeting, base, owner="Вы")
    rows = {r["label"]: r for r in got["speakers"]}
    assert [r["label"] for r in got["speakers"]] == ["Спикер 1", "Спикер 2", "Спикер 3", "Вы"]
    assert rows["Спикер 1"]["seconds"] == 14.0
    assert rows["Спикер 1"]["share"] == pytest.approx(14 / 23, abs=1e-3)
    assert rows["Спикер 1"]["name"] is None and rows["Вы"]["name"] == "Вы"
    # Самые длинные реплики — первыми.
    assert [s["start"] for s in rows["Спикер 1"]["samples"]] == [9.0, 0.0]
    assert rows["Спикер 1"]["has_voice"] is True and rows["Вы"]["has_voice"] is False
    assert got["owner"] == "Вы"
    assert got["history"] == [] and got["pos"] == 0


def test_overview_skips_overlapped_phrases_as_samples(meeting, base):
    data = library.read_transcript(meeting)
    data["segments"][2]["uncertain"] = True
    library.write_transcript(meeting, data)
    rows = {r["label"]: r for r in speakers.overview(meeting, base)["speakers"]}
    assert [s["start"] for s in rows["Спикер 1"]["samples"]] == [0.0]


def test_suggestions_rank_people_by_best_sample_and_hide_weak(meeting, base):
    _person(base, "Анна Смирнова", [0.0, 1.0, 0.0], [0.9, 0.1, 0.0])   # 0 и 0.99: берётся лучший
    _person(base, "Борис Козлов", [0.6, 0.8, 0.0])                       # cos 0.6
    _person(base, "Вера Орлова", [0.3, 0.0, 0.95])                       # cos ≈ 0.30 — скрыт
    _person(base, "Глеб Демьянов", [0.5, 0.0, 0.866])                      # cos 0.5
    _person(base, "Дина Ким", [0.45, 0.0, 0.893])                        # cos 0.45 — четвёртый
    rows = {r["label"]: r for r in speakers.overview(meeting, base)["speakers"]}
    sug = rows["Спикер 1"]["suggestions"]
    assert [s["name"] for s in sug] == ["Анна Смирнова", "Борис Козлов", "Глеб Демьянов"]
    assert sug[0]["score"] == pytest.approx(0.99, abs=0.01)
    assert rows["Вы"]["suggestions"] == []


def test_overview_reads_old_raw_labels_as_display_names(meeting, base):
    data = library.read_transcript(meeting)
    for s in data["segments"]:
        s["speaker"] = {"Спикер 1": "SPEAKER_00", "Спикер 2": "SPEAKER_01",
                        "Спикер 3": "SPEAKER_02"}.get(s["speaker"], s["speaker"])
    library.write_transcript(meeting, data)
    got = speakers.overview(meeting, base)
    assert [r["label"] for r in got["speakers"]] == ["Спикер 1", "Спикер 2", "Спикер 3", "Вы"]
    assert got["speakers"][0]["has_voice"] is True


# --- применение ---------------------------------------------------------------


def test_apply_renames_merges_and_enrolls_in_one_step(meeting, base):
    step = speakers.apply(meeting, [
        {"type": "rename", "label": "Спикер 2", "to": "Анна Смирнова"},
        {"type": "merge", "label": "Спикер 3", "to": "Спикер 1"},
        {"type": "rename", "label": "Спикер 1", "to": "Борис Козлов"},
    ], {"Спикер 2": True, "Спикер 3": True, "Спикер 1": False}, base)["step"]
    assert _speakers_of(meeting) == ["Борис Козлов", "Анна Смирнова", "Борис Козлов",
                                     "Борис Козлов", "Анна Смирнова", "Вы"]
    assert [(o["type"], o["from"], o["to"]) for o in step["ops"]] == [
        ("rename", "Спикер 2", "Анна Смирнова"), ("merge", "Спикер 3", "Борис Козлов"),
        ("rename", "Спикер 1", "Борис Козлов")]
    # Голос Спикера 3 запомнен за Борисом (объединён с ним), Спикера 1 — нет.
    assert sorted(e["person"] for e in step["enrolled"]) == ["Анна Смирнова", "Борис Козлов"]
    assert [s["label"] for s in _samples(base, "Борис Козлов")] == ["SPEAKER_02"]
    assert sorted(step["created_people"]) == ["Анна Смирнова", "Борис Козлов"]
    meta = library.read_meta(meeting)
    assert meta["speaker_history_pos"] == 1 and meta["speaker_history"][0]["id"] == step["id"]
    # Метки сайдкара следуют за именами: подсказки и голос остаются у строки.
    rows = {r["label"]: r for r in speakers.overview(meeting, base)["speakers"]}
    assert rows["Борис Козлов"]["has_voice"] is True and rows["Анна Смирнова"]["has_voice"]


def test_apply_swaps_names_at_once(meeting, base):
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 1", "to": "Анна"},
                             {"type": "rename", "label": "Спикер 2", "to": "Борис"}], {}, base)
    speakers.apply(meeting, [{"type": "rename", "label": "Анна", "to": "Борис"},
                             {"type": "rename", "label": "Борис", "to": "Анна"}], {}, base)
    assert _speakers_of(meeting)[:2] == ["Борис", "Анна"]


def test_reset_returns_the_original_speaker_number(meeting, base):
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Анна"}], {}, base)
    step = speakers.apply(meeting, [{"type": "reset", "label": "Анна"}], {}, base)["step"]
    assert step["ops"][0]["to"] == "Спикер 2"
    assert _speakers_of(meeting)[1] == "Спикер 2"


def test_reset_without_a_free_original_number_takes_the_next_free(meeting, base):
    data = library.read_transcript(meeting)
    data["segments"][5]["speaker"] = "Демьян"  # микрофон: в сайдкаре его нет
    library.write_transcript(meeting, data)
    step = speakers.apply(meeting, [{"type": "reset", "label": "Демьян"}], {}, base)["step"]
    assert step["ops"][0]["to"] == "Спикер 4"


@pytest.mark.parametrize("ops, text", [
    ([], "нечего"),
    ([{"type": "rename", "label": "Спикер 9", "to": "Анна"}], "Спикер 9"),
    ([{"type": "rename", "label": "Спикер 1", "to": "a/b"}], "a/b"),
    ([{"type": "rename", "label": "Спикер 1", "to": "Спикер 7"}], "Неизвестный"),
    ([{"type": "merge", "label": "Спикер 1", "to": "Спикер 1"}], "сам"),
    ([{"type": "merge", "label": "Спикер 1", "to": "Спикер 2"},
      {"type": "merge", "label": "Спикер 2", "to": "Спикер 1"}], "по кругу"),
    ([{"type": "rename", "label": "Спикер 1", "to": "Анна"},
      {"type": "reset", "label": "Спикер 1"}], "дважды"),
    ([{"type": "explode", "label": "Спикер 1"}], "explode"),
])
def test_bad_change_sets_are_refused_before_anything_is_written(meeting, base, ops, text):
    before = (meeting / "transcript.json").read_bytes()
    with pytest.raises(speakers.SpeakerError, match=text):
        speakers.apply(meeting, ops, {}, base)
    assert (meeting / "transcript.json").read_bytes() == before
    assert "speaker_history" not in library.read_meta(meeting)
    assert list(base.iterdir()) == []


def test_remember_without_voice_prints_reason_but_names_are_kept(meeting, base):
    (meeting / "2026-09-30_16-04_speakers.json").unlink()
    got = speakers.apply(meeting, [{"type": "rename", "label": "Спикер 1", "to": "Анна"}],
                         {"Спикер 1": True}, base)
    assert got["voices_error"] and _speakers_of(meeting)[0] == "Анна"
    assert got["step"]["enrolled"] == []


# --- отмена, повтор, история --------------------------------------------------


def test_undo_restores_labels_and_removes_enrolled_voice_and_created_person(meeting, base):
    _person(base, "Анна", [0.0, 0.9, 0.1])
    before = library.read_transcript(meeting)
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Анна"},
                             {"type": "merge", "label": "Спикер 3", "to": "Спикер 1"},
                             {"type": "rename", "label": "Спикер 1", "to": "Новый Коллега"}],
                   {"Спикер 2": True, "Спикер 1": True}, base)
    assert len(_samples(base, "Анна")) == 2 and (base / "Новый Коллега.json").exists()
    got = speakers.undo(meeting, base)
    after = library.read_transcript(meeting)
    assert after["segments"] == before["segments"]
    assert after.get("names") == before.get("names")
    assert len(_samples(base, "Анна")) == 1  # прежний образец на месте
    assert not (base / "Новый Коллега.json").exists()  # создан этим шагом — убран
    assert got["pos"] == 0 and len(got["history"]) == 1


def test_undo_restores_a_replaced_old_sample_of_the_same_meeting(meeting, base):
    _person(base, "Анна", [0.0, 0.8, 0.2], source=SOURCE)  # старый «meet enroll» той же встречи
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Анна"}],
                   {"Спикер 2": True}, base)
    assert [s["embedding"] for s in _samples(base, "Анна")] == [[0.0, 1.0, 0.0]]
    speakers.undo(meeting, base)
    assert [s["embedding"] for s in _samples(base, "Анна")] == [[0.0, 0.8, 0.2]]


def test_undo_keeps_a_created_person_who_appears_in_another_meeting(meeting, base):
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Анна"}],
                   {"Спикер 2": True}, base)
    other = meeting.parent / "2026-09-29_10-00"
    other.mkdir()
    (other / "sys.opus").write_bytes(b"x")
    library.write_transcript(other, {"segments": [_seg(0, 3, "Анна", "Привет.")]})
    speakers.undo(meeting, base)
    assert (base / "Анна.json").exists() and _samples(base, "Анна") == []


def test_redo_reapplies_and_reenrolls_from_the_sidecar(meeting, base):
    first = speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Анна"}],
                           {"Спикер 2": True}, base)["step"]
    speakers.undo(meeting, base)
    got = speakers.redo(meeting, base)
    assert _speakers_of(meeting)[1] == "Анна"
    samples = _samples(base, "Анна")
    assert len(samples) == 1 and samples[0]["label"] == "SPEAKER_01"
    assert got["pos"] == 1 and got["history"][0]["id"] == first["id"]
    assert got["history"][0]["enrolled"][0]["sample_id"] == samples[0]["id"]


def test_new_apply_after_undo_truncates_the_redo_tail(meeting, base):
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 1", "to": "Анна"}], {}, base)
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Борис"}], {}, base)
    speakers.undo(meeting, base)
    got = speakers.apply(meeting, [{"type": "rename", "label": "Спикер 3", "to": "Вера"}], {}, base)
    assert [o["to"] for st in got["history"] for o in st["ops"]] == ["Анна", "Вера"]
    with pytest.raises(speakers.SpeakerError, match="повторять нечего"):
        speakers.redo(meeting, base)


def test_revert_walks_back_and_forward_through_history(meeting, base):
    a = speakers.apply(meeting, [{"type": "rename", "label": "Спикер 1", "to": "Анна"}],
                       {"Спикер 1": True}, base)["step"]
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Борис"}],
                   {"Спикер 2": True}, base)
    speakers.apply(meeting, [{"type": "merge", "label": "Спикер 3", "to": "Анна"}], {}, base)
    got = speakers.revert(meeting, a["id"], base)
    assert got["pos"] == 1 and _speakers_of(meeting)[:4] == ["Анна", "Спикер 2", "Анна", "Спикер 3"]
    assert not (base / "Борис.json").exists() and (base / "Анна.json").exists()
    got = speakers.revert(meeting, None, base)
    assert got["pos"] == 0 and _speakers_of(meeting) == [s["speaker"] for s in SEGMENTS]
    assert list(base.iterdir()) == []
    got = speakers.revert(meeting, got["history"][-1]["id"], base)
    assert got["pos"] == 3 and _speakers_of(meeting)[3] == "Анна"
    with pytest.raises(speakers.SpeakerError):
        speakers.revert(meeting, "нет-такого", base)


def test_undo_refuses_when_the_transcript_changed_after_the_step(meeting, base):
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 1", "to": "Анна"}], {}, base)
    data = library.read_transcript(meeting)
    data["segments"][0]["speaker"] = "Кто-то ещё"
    library.write_transcript(meeting, data)
    with pytest.raises(speakers.Stale):
        speakers.undo(meeting, base)
    assert library.read_meta(meeting)["speaker_history_pos"] == 1


def test_retranscription_starts_a_fresh_history(meeting, base):
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 1", "to": "Анна"}], {}, base)
    library.write_transcript(meeting, {"version": 1, "created_at": "2026-10-01T09:00:00",
                                       "segments": [dict(s) for s in SEGMENTS]})
    got = speakers.overview(meeting, base)
    assert got["history"] == [] and got["pos"] == 0
    with pytest.raises(speakers.SpeakerError, match="отменять нечего"):
        speakers.undo(meeting, base)
    step = speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Борис"}],
                          {}, base)
    assert len(step["history"]) == 1


def test_a_person_merge_after_the_step_makes_undo_refuse_honestly(meeting, base):
    from meet import people

    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Анна"}],
                   {"Спикер 2": True}, base)
    _person(base, "Анна Смирнова", [0.0, 0.9, 0.1])
    people.merge("Анна", "Анна Смирнова", base, meeting.parent)
    # Слияние людей переписало имя в транскрипте: шаг уже не отменить честно.
    with pytest.raises(speakers.Stale):
        speakers.undo(meeting, base)
    assert voices.load_voices(base)["Анна Смирнова"]
