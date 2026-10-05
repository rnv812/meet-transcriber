"""«Это я — {владелец}» + «Запомнить мой голос» в панели «Спикеры»: центроид
голоса владельца встречи — образцом `source="meeting"` (meet.owner_voice),
откат шага его убирает, повтор — записывает снова.

Голос владельца встречи — запись `OWNER` сайдкара (её пишет разделение
микрофона, speakers-design §3.4); нет её — флажка нет, и образец не пишется.
Векторы выдуманные, по три числа."""

import json

import numpy as np
import pytest

from meet import library, owner_voice, speakers, voices

NAME = "2026-10-05_13-32"


def _seg(start, end, speaker, text, track):
    return {"start": start, "end": end, "speaker": speaker, "text": text, "uncertain": False,
            "track": track}


SEGMENTS = [
    _seg(0.0, 4.0, "Спикер 1", "Добрый день.", "sys"),
    _seg(4.0, 10.0, "Вы", "Начнём с отчёта.", "mic"),
    _seg(10.0, 14.0, "Спикер 3", "Я тоже здесь.", "mic"),
    _seg(14.0, 16.0, "Вы", "Хорошо.", "mic"),
]
OWNER = [2.0, 0.0, 0.0]          # норма 2: перед усреднением — к единичной
ROOM = [0.0, 1.0, 0.0]
SYS = [0.0, 0.0, 1.0]


def _sidecar(owner=True):
    entries = [
        {"label": "SPEAKER_00", "display": "Спикер 1", "embedding": SYS},
        {"label": "SPEAKER_M0", "display": "Спикер 3", "embedding": ROOM, "track": "mic"},
    ]
    if owner:
        entries.append({"label": "OWNER", "display": "Вы", "embedding": OWNER, "track": "mic",
                        "owner": True})
    return {"model": "m", "source": f"C:/rec/{NAME}", "date": "2026-10-05", "speakers": entries}


@pytest.fixture
def make(tmp_path):
    def build(owner=True):
        folder = tmp_path / "recordings" / NAME
        folder.mkdir(parents=True)
        library.write_transcript(folder, {"version": 1, "created_at": "2026-10-05T14:00:00",
                                          "track_marks": "pipeline",
                                          "segments": [dict(s) for s in SEGMENTS]})
        (folder / f"{NAME[:10]}_speakers.json").write_text(
            json.dumps(_sidecar(owner), ensure_ascii=False), encoding="utf-8")
        base = tmp_path / "voices"
        base.mkdir(exist_ok=True)
        return folder, base
    return build


def _unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


def _me(folder, base, **kw):
    return speakers.apply(folder, [{"type": "rename", "label": "Спикер 3", "to": "Вы"}], {}, base,
                          remember_owner=True, owner="Вы", **kw)


def test_overview_says_whether_the_meeting_has_owner_voice(make):
    folder, base = make()
    assert speakers.overview(folder, base, owner="Вы")["owner_voice"] is True


def test_overview_without_owner_entry_hides_the_option(make):
    folder, base = make(owner=False)
    assert speakers.overview(folder, base, owner="Вы")["owner_voice"] is False


def test_this_is_me_stores_meeting_centroid_of_owner_mic_voices(make):
    folder, base = make()
    got = _me(folder, base)
    (sample,) = owner_voice.load(base)
    assert sample.source == "meeting" and sample.recording == NAME and sample.device is None
    # «Вы» 8 с (OWNER) + «Спикер 3» 4 с (комнатный кластер микрофона) — по секундам.
    assert sample.seconds == pytest.approx(12.0)
    expected = _unit(8 * _unit(OWNER) + 4 * _unit(ROOM))
    assert np.allclose(sample.embedding, expected, atol=1e-5)
    assert got["step"]["owner_voice"] is True
    assert [s["speaker"] for s in library.read_transcript(folder)["segments"]] == [
        "Спикер 1", "Вы", "Вы", "Вы"]
    # Владелец — не «человек» базы голосов.
    assert voices.load_voices(base) == {}
    step = library.read_meta(folder)[speakers.HISTORY][-1]
    assert step["owner_voice"]["sample_id"] == sample.id


def test_sys_voice_never_goes_into_the_mic_sample(make):
    """Своя реплика, попавшая в звук собеседников, звучит через кодек звонка —
    в образец микрофона её голос не берём."""
    folder, base = make()
    speakers.apply(folder, [{"type": "rename", "label": "Спикер 1", "to": "Вы"}], {}, base,
                   remember_owner=True, owner="Вы")
    (sample,) = owner_voice.load(base)
    assert np.allclose(sample.embedding, _unit(OWNER)) and sample.seconds == pytest.approx(8.0)


def test_remember_alone_is_a_step(make):
    folder, base = make()
    got = speakers.apply(folder, [], {}, base, remember_owner=True, owner="Вы")
    (sample,) = owner_voice.load(base)
    assert sample.seconds == pytest.approx(8.0) and got["changed"] == 0


def test_without_owner_entry_rename_applies_and_says_why_no_sample(make):
    folder, base = make(owner=False)
    got = _me(folder, base)
    assert owner_voice.load(base) == []
    assert "голос" in got["voices_error"].lower()
    assert got["step"]["owner_voice"] is False


def test_undo_removes_the_sample_and_redo_writes_it_again(make):
    folder, base = make()
    _me(folder, base)
    first = owner_voice.load(base)[0]
    speakers.undo(folder, base)
    assert owner_voice.load(base) == []
    assert [s["speaker"] for s in library.read_transcript(folder)["segments"]][2] == "Спикер 3"
    speakers.redo(folder, base)
    (again,) = owner_voice.load(base)
    assert again.id != first.id and np.allclose(again.embedding, first.embedding)
    step = library.read_meta(folder)[speakers.HISTORY][-1]
    assert step["owner_voice"]["sample_id"] == again.id
    speakers.undo(folder, base)
    assert owner_voice.load(base) == []


def test_undo_brings_back_the_sample_it_replaced(make):
    folder, base = make()
    before = owner_voice.add([0.0, 0.0, 1.0], source="meeting", seconds=30, recording=NAME,
                             voices=base)
    enroll = owner_voice.add([1.0, 0.0, 0.0], source="enroll", seconds=20, device="USB", voices=base)
    _me(folder, base)
    assert {s.id for s in owner_voice.load(base)} - {before.id} and before.id not in {
        s.id for s in owner_voice.load(base)}
    speakers.undo(folder, base)
    assert {s.id for s in owner_voice.load(base)} == {before.id, enroll.id}


def test_voice_base_failure_rolls_back_owner_sample_too(make, monkeypatch):
    folder, base = make()
    keep = owner_voice.add([1.0, 0.0, 0.0], source="enroll", seconds=20, device="USB", voices=base)
    raw = owner_voice.path(base).read_bytes()

    def broken(*a, **kw):
        raise OSError("диск полон")

    monkeypatch.setattr(voices, "enroll_sample", broken)
    with pytest.raises(speakers.VoiceBaseError):
        speakers.apply(folder, [{"type": "rename", "label": "Спикер 3", "to": "Вы"},
                                {"type": "rename", "label": "Спикер 1", "to": "Анна"}],
                       {"Спикер 1": True}, base, remember_owner=True, owner="Вы")
    assert owner_voice.path(base).read_bytes() == raw
    assert [s.id for s in owner_voice.load(base)] == [keep.id]
    assert [s["speaker"] for s in library.read_transcript(folder)["segments"]][2] == "Спикер 3"


def test_rollback_removes_owner_file_it_created(make, monkeypatch):
    folder, base = make()

    def broken(*a, **kw):
        raise OSError("диск полон")

    monkeypatch.setattr(voices, "enroll_sample", broken)
    with pytest.raises(speakers.VoiceBaseError):
        speakers.apply(folder, [{"type": "rename", "label": "Спикер 1", "to": "Анна"}],
                       {"Спикер 1": True}, base, remember_owner=True, owner="Вы")
    assert not owner_voice.path(base).exists()


def test_redo_without_owner_entry_says_so(make):
    folder, base = make()
    _me(folder, base)
    speakers.undo(folder, base)
    side = folder / f"{NAME[:10]}_speakers.json"
    side.write_text(json.dumps(_sidecar(owner=False), ensure_ascii=False), encoding="utf-8")
    got = speakers.redo(folder, base)
    assert owner_voice.load(base) == [] and "голос" in got["voices_error"].lower()


def test_resident_passes_remember_owner_with_owner_name(make, monkeypatch, tmp_path):
    from meet import settings, tray, tray_control

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    folder, base = make()
    settings.patch({"recording": {"out_dir": str(folder.parent), "voices_dir": str(base)}})
    state = tray_control.TrayControl(tray.TrayApp())
    got = state.speakers_apply(NAME, {"ops": [{"type": "rename", "label": "Спикер 3", "to": "Вы"}],
                                      "remember": {}, "remember_owner": True})
    assert got["owner_voice"] is True and got["step"]["owner_voice"] is True
    assert [s.source for s in owner_voice.load(base)] == ["meeting"]
    state.speakers_undo(NAME, {})
    assert owner_voice.load(base) == []


def test_remember_alone_without_owner_voice_is_refused_not_an_empty_step(make):
    folder, base = make(owner=False)
    with pytest.raises(speakers.SpeakerError, match="не запомнен"):
        speakers.apply(folder, [], {}, base, remember_owner=True, owner="Вы")
    assert library.read_meta(folder).get(speakers.HISTORY) in (None, [])


def test_rollback_restores_owner_file_even_if_its_folder_is_gone(make, monkeypatch):
    """Откат пишет файл владельца атомарно и заводит папку заново."""
    import shutil

    folder, base = make()
    keep = owner_voice.add([1.0, 0.0, 0.0], source="enroll", seconds=20, device="USB", voices=base)
    raw = owner_voice.path(base).read_bytes()
    real_add = owner_voice.add

    def add_then_lose_folder(*a, **kw):
        got = real_add(*a, **kw)
        shutil.rmtree(owner_voice.path(base).parent)
        return got

    def broken(*a, **kw):
        raise OSError("диск полон")

    monkeypatch.setattr(owner_voice, "add", add_then_lose_folder)
    monkeypatch.setattr(voices, "enroll_sample", broken)
    with pytest.raises(speakers.VoiceBaseError):
        speakers.apply(folder, [{"type": "rename", "label": "Спикер 3", "to": "Вы"},
                                {"type": "rename", "label": "Спикер 1", "to": "Анна"}],
                       {"Спикер 1": True}, base, remember_owner=True, owner="Вы")
    assert owner_voice.path(base).read_bytes() == raw
    assert [s.id for s in owner_voice.load(base)] == [keep.id]
    assert not list(owner_voice.path(base).parent.glob("*.tmp"))
