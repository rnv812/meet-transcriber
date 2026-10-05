"""«Переразделить на спикеров»: только диаризация (поддельная) по звуку
записи, реплики раздаются новым спикерам (со словами — с разрезом по слову,
без слов — целиком), предпросмотр, применение одним шагом и отмена.

Тексты, имена и «голоса» выдуманы."""

import json
from pathlib import Path

import numpy as np
import pytest

from meet import jobs, library, rediarize, speakers
from meet.diarize import Diarization


def _w(*items):
    return [[s, e, t] for s, e, t in items]


SEGMENTS = [
    # Два человека, слитые в «Спикер 1»: смена говорящего внутри сегмента (на 2.0 с).
    {"start": 0.0, "end": 4.0, "speaker": "Спикер 1", "text": "Склад готов. Да, отгрузка в четверг.",
     "uncertain": False, "words": _w((0.0, 0.6, " Склад"), (0.6, 1.4, " готов."), (2.1, 2.4, " Да,"),
                                     (2.4, 3.2, " отгрузка"), (3.2, 3.5, " в"), (3.5, 4.0, " четверг."))},
    {"start": 4.2, "end": 5.0, "speaker": "Вы", "text": "Отлично.", "uncertain": False, "track": "mic"},
    # Старый сегмент без слов — целиком тому, кто говорил дольше.
    {"start": 6.0, "end": 9.0, "speaker": "Спикер 1", "text": "Счёт оплатим до пятницы.", "uncertain": False},
    {"start": 9.0, "end": 9.0, "speaker": None, "text": "— перерыв 5 мин —", "uncertain": False, "kind": "break"},
    {"start": 10.0, "end": 12.0, "speaker": "Спикер 2", "text": "Договор продлим.", "uncertain": False},
]

TURNS = [(0.0, 2.0, "SPEAKER_00"), (2.0, 6.5, "SPEAKER_01"), (6.5, 9.5, "SPEAKER_00"), (9.5, 13.0, "SPEAKER_02")]
EMB = {"SPEAKER_00": np.array([1.0, 0.0, 0.0]), "SPEAKER_01": np.array([0.0, 1.0, 0.0]),
       "SPEAKER_02": np.array([0.0, 0.0, 1.0])}


@pytest.fixture
def meeting(tmp_path):
    folder = tmp_path / "recordings" / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "created_at": "2026-09-30T17:00:00", "track_marks": "pipeline",
                                      "names": {"Спикер 3": "Спикер 1"},
                                      "segments": json.loads(json.dumps(SEGMENTS))})
    (folder / "2026-09-30_speakers.json").write_text(json.dumps({
        "model": "m", "source": "C:/rec/x", "date": "2026-09-30", "speakers": [
            {"label": "SPEAKER_00", "display": "Спикер 1", "embedding": [0.7, 0.7, 0.0]},
            {"label": "SPEAKER_01", "display": "Спикер 2", "embedding": [0.0, 0.0, 1.0]}]},
        ensure_ascii=False), encoding="utf-8")
    return folder


@pytest.fixture
def base(tmp_path):
    folder = tmp_path / "voices"
    folder.mkdir()
    (folder / "Анна Смирнова.json").write_text(json.dumps({"samples": [
        {"embedding": [0.0, 0.98, 0.2], "source": "C:/rec/old", "date": "2026-09-01"}]}, ensure_ascii=False),
        encoding="utf-8")
    return folder


def _fake(calls):
    def diarize(wav, **kw):
        calls.append(kw)
        return Diarization(turns=TURNS, embeddings=EMB, overlaps=[])
    return diarize


def _to_wav(src, dst, normalize=False):
    Path(dst).write_bytes(b"wav")
    return Path(dst)


def _run(meeting, base, monkeypatch, **kw):
    from meet import voices

    monkeypatch.setattr(voices, "voices_dir", lambda: base)
    calls = []
    out = rediarize.run(meeting, diarize=_fake(calls), to_wav=_to_wav, **kw)
    return out, calls


def _segs(folder):
    return library.read_transcript_full(folder)["segments"]


def test_rediarize_on_cpu_says_why_in_the_log_and_progress(meeting, base, monkeypatch):
    """Переразделение — подпроцесс задачи: голый print очередь выбрасывает,
    а событие `log` source="device" она пишет в журнал резидента."""
    from meet import asr, events, voices

    monkeypatch.setattr(voices, "voices_dir", lambda: base)
    monkeypatch.setattr(asr, "torch_cpu_reason", lambda setting=None: "torch не видит видеокарту")
    bus = events.EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(e.to_dict()))

    def diarize(wav, **kw):
        return Diarization(turns=TURNS, embeddings=EMB, overlaps=[], device="cpu")

    rediarize.run(meeting, diarize=diarize, to_wav=_to_wav, bus=bus)
    logs = [e["text"] for e in seen if e["kind"] == "log" and e.get("source") == "device"]
    assert logs == ["диаризация на процессоре: torch не видит видеокарту"]
    assert seen[-1]["kind"] == "progress"
    assert seen[-1]["warning"] == "Диаризация на процессоре: torch не видит видеокарту"


def test_sensitivity_maps_to_pipeline_clustering_threshold():
    assert rediarize.clustering_threshold(None) is None
    assert rediarize.clustering_threshold(0.5) is None       # как у обычной расшифровки
    assert rediarize.clustering_threshold(1.0) == 0.4        # чувствительнее — порог ниже
    assert rediarize.clustering_threshold(0.0) == 0.8
    assert rediarize.clustering_threshold(0.75) == 0.5


def test_run_reassigns_text_without_asr_and_previews(meeting, base, monkeypatch):
    out, calls = _run(meeting, base, monkeypatch, num_speakers=3, sensitivity=1.0)
    assert out.name == rediarize.PREVIEW_NAME
    assert callable(calls[0].pop("on_progress"))  # ход диаризации — в шкалу задачи
    assert calls == [{"num_speakers": 3, "min_speakers": None, "max_speakers": None,
                      "exclusive": False, "clustering_threshold": 0.4}]
    assert _segs(meeting) == SEGMENTS  # транскрипт не тронут до применения
    got = rediarize.preview(meeting)
    assert got["stale"] is False and got["segments"] == 5 and got["before"] == 3
    rows = {r["label"]: r for r in got["speakers"]}
    # SPEAKER_01 узнан по базе (0.98), остальные — «Спикер N» по порядку появления.
    assert list(rows) == ["Спикер 1", "Анна Смирнова", "Вы", "Спикер 2"]
    assert got["cut"] == 1 and got["changed"] == 1
    assert rows["Анна Смирнова"]["samples"][0]["text"].startswith("Да, отгрузка")
    assert library.describe(meeting).rediarize_ready is True


def test_apply_cuts_at_words_relabels_and_undoes(meeting, base, monkeypatch):
    _run(meeting, base, monkeypatch)
    got = rediarize.apply(meeting, base)
    segs = _segs(meeting)
    assert [(s["speaker"], s["text"]) for s in segs] == [
        ("Спикер 1", "Склад готов."),
        ("Анна Смирнова", "Да, отгрузка в четверг."),
        ("Вы", "Отлично."),
        ("Спикер 1", "Счёт оплатим до пятницы."),
        (None, "— перерыв 5 мин —"),
        ("Спикер 2", "Договор продлим."),
    ]
    assert segs[1]["start"] == 2.1 and segs[1]["words"][0] == [2.1, 2.4, " Да,"]
    assert "names" not in library.read_transcript(meeting)
    side = json.loads((meeting / "2026-09-30_speakers.json").read_text(encoding="utf-8"))
    assert [(e["label"], e["display"]) for e in side["speakers"]] == [
        ("SPEAKER_00", "Спикер 1"), ("SPEAKER_01", "Анна Смирнова"), ("SPEAKER_02", "Спикер 2")]
    assert got["step"]["ops"][0]["type"] == "rediarize" and got["step"]["ops"][0]["speakers"] == 4
    assert not (meeting / rediarize.PREVIEW_NAME).exists()
    rows = {r["label"]: r for r in speakers.overview(meeting, base)["speakers"]}
    assert rows["Анна Смирнова"]["has_voice"]

    speakers.undo(meeting, base)
    data = library.read_transcript_full(meeting)
    assert data["segments"] == SEGMENTS and data["names"] == {"Спикер 3": "Спикер 1"}
    side = json.loads((meeting / "2026-09-30_speakers.json").read_text(encoding="utf-8"))
    assert [e["display"] for e in side["speakers"]] == ["Спикер 1", "Спикер 2"]
    speakers.redo(meeting, base)
    assert len(_segs(meeting)) == 6
    # После переразделения — обычные правки поверх и их отмена.
    speakers.relabel(meeting, [0], "Анна Смирнова", base)
    speakers.undo(meeting, base)
    speakers.undo(meeting, base)
    assert _segs(meeting) == SEGMENTS


def test_apply_refuses_if_text_changed_since_the_run_but_not_after_a_rename(meeting, base, monkeypatch):
    _run(meeting, base, monkeypatch)
    # Переименование после расчёта результат не портит: подписи раздаются заново.
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Олег"}], {}, base)
    assert rediarize.preview(meeting)["stale"] is False
    data = library.read_transcript_full(meeting)
    data["segments"][4]["text"] = "Договор продлим на год."
    library.write_transcript(meeting, data)
    assert rediarize.preview(meeting)["stale"] is True
    with pytest.raises(speakers.Stale, match="запустите"):
        rediarize.apply(meeting, base)
    assert rediarize.discard(meeting) is True
    assert rediarize.preview(meeting) is None
    with pytest.raises(speakers.SpeakerError):
        rediarize.apply(meeting, base)


def test_names_given_by_hand_and_turn_fixes_survive_by_voice(meeting, base, monkeypatch):
    from meet import voices

    monkeypatch.setattr(voices, "voices_dir", lambda: base)
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 2", "to": "Олег"}], {}, base)
    speakers.relabel(meeting, [2], "Вера", base)          # ручная правка одной реплики
    emb = {"SPEAKER_00": np.array([0.9, 0.3, 0.0]),       # тот же голос, что прежний «Спикер 1»
           "SPEAKER_01": np.array([0.0, 1.0, 0.0]),       # новый — узнаётся по базе (Анна)
           "SPEAKER_02": np.array([0.0, 0.05, 1.0])}      # тот же, что «Олег»
    rediarize.run(meeting, diarize=lambda wav, **kw: Diarization(turns=TURNS, embeddings=emb, overlaps=[]),
                  to_wav=_to_wav)
    got = rediarize.preview(meeting)
    assert got["kept"] == ["Олег"]
    assert [r["label"] for r in got["speakers"]] == ["Спикер 1", "Анна Смирнова", "Вы", "Вера", "Олег"]
    assert got["changed"] == 1      # только разрезанная реплика: имена и правка на месте
    rediarize.apply(meeting, base)
    assert [s["speaker"] for s in _segs(meeting)] == [
        "Спикер 1", "Анна Смирнова", "Вы", "Вера", None, "Олег"]


def test_voice_pairs_are_one_to_one_above_the_floor():
    sim = np.array([[0.9, 0.8], [0.85, 0.3], [0.5, 0.6]])
    assert sorted(rediarize._pairs(sim)) == [(0, 1), (1, 0)]
    assert rediarize._pairs(np.array([[0.6]])) == []


def test_rediarize_job_argv_and_worker(meeting, monkeypatch, capsys):
    from meet import job_worker

    job = jobs.Job(id="r", kind=jobs.REDIARIZE, folder=str(meeting),
                   options={"min_speakers": 2, "max_speakers": 6, "sensitivity": 0.7})
    assert jobs.worker_argv(job)[-5:] == ["rediarize", str(meeting), "--min-speakers=2", "--max-speakers=6",
                                          "--sensitivity=0.7"]
    seen = {}
    monkeypatch.setattr(rediarize, "run", lambda folder, **kw: seen.update(kw) or folder / "x.json")
    assert job_worker.main(["rediarize", str(meeting), "--num-speakers=4"]) == 0
    assert seen["num_speakers"] == 4 and seen["sensitivity"] is None
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert last == {"kind": "job.result", "path": str(meeting / "x.json")}


def test_new_transcription_drops_a_pending_rediarize_result(meeting, monkeypatch, tmp_path):
    from meet import tray, tray_control

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (meeting / rediarize.PREVIEW_NAME).write_text("{}", encoding="utf-8")

    class Queue:
        def submit(self, kind, folder, options=None):
            return jobs.Job(id="t", kind=kind, folder=folder)

        def active_for(self, folder, kinds):
            return None

    state = tray_control.TrayControl(tray.TrayApp(), queue=Queue())
    state._submit_once(jobs.TRANSCRIBE, meeting)
    assert not (meeting / rediarize.PREVIEW_NAME).exists()


def test_temp_dirs_of_dead_jobs_are_swept(tmp_path):
    dead = tmp_path / f"{jobs.TEMP_PREFIX}999999-abc"
    live = tmp_path / f"{jobs.TEMP_PREFIX}123-abc"
    foreign = tmp_path / "other-999999-abc"
    for d in (dead, live, foreign):
        d.mkdir()
        (d / "audio16.wav").write_bytes(b"wav")
    got = jobs.sweep_temp(tmp_path, alive=lambda pid: pid == 123)
    assert got == [dead.name]
    assert not dead.exists() and live.exists() and foreign.exists()
    with jobs.temp_dir() as td:
        assert Path(td).name.startswith(f"{jobs.TEMP_PREFIX}")


def _got(parts, voices, names=None):
    return {"parts": parts, "voices": {k: list(v) for k, v in voices.items()}, "names": names or {}}


def test_labels_follow_what_the_transcript_shows_even_if_sidecar_displays_are_stale(meeting, base):
    """Имена поменяли местами в базе голосов: транскрипт переписан, подписи
    кластеров в сайдкаре — нет. Новый кластер берёт подпись по времени речи."""
    data = library.read_transcript_full(meeting)
    data["segments"][2]["speaker"] = "Борис"     # голос прежнего кластера «Спикер 1»
    data["segments"][4]["speaker"] = "Анна"      # голос прежнего «Спикер 2»
    data["segments"][0]["speaker"] = "Борис"
    library.write_transcript(meeting, data)
    data = speakers._transcript(meeting)
    seg = data["segments"]
    parts = [[{**seg[0], "speaker": "R0"}], None, [{**seg[2], "speaker": "R0"}], None,
             [{**seg[4], "speaker": "R1"}]]
    done = rediarize.finish(meeting, data, _got(parts, {"R0": [0.0, 0.0, 1.0], "R1": [0.7, 0.7, 0.0]}))
    # По голосу R0 похож на прежний «Спикер 2», но по расшифровке это «Борис».
    assert [p[0]["speaker"] for p in done["parts"]] == ["Борис", "Вы", "Борис", None, "Анна"]
    assert done["kept"] == ["Анна", "Борис"]


def test_label_falls_back_to_voice_when_time_does_not_decide(meeting, base, monkeypatch):
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 1", "to": "Глеб"}], {}, base)
    data = speakers._transcript(meeting)
    seg = data["segments"]
    # Все реплики R0 исправлены вручную — по времени он не голосует.
    monkeypatch.setattr(rediarize, "_manual_keys", lambda folder, data: {(0.0, 4.0), (6.0, 9.0)})
    parts = [[{**seg[0], "speaker": "R0"}], None, [{**seg[2], "speaker": "R0"}], None,
             [{**seg[4], "speaker": "R1"}]]
    done = rediarize.finish(meeting, data, _got(parts, {"R0": [0.7, 0.7, 0.0], "R1": [0.0, 0.05, 1.0]}))
    # По голосу R0 — прежний «Спикер 1», ныне «Глеб».
    assert [v["display"] for v in done["voices"]] == ["Глеб", "Спикер 2"]
    assert done["kept"] == ["Глеб"]
    assert [p[0]["speaker"] for p in done["parts"]] == ["Глеб", "Вы", "Глеб", None, "Спикер 2"]
    # Голос не похож — новый номер, не занятый оставшимися подписями.
    done = rediarize.finish(meeting, data, _got(parts, {"R0": [1.0, -1.0, 0.0], "R1": [0.0, 0.05, 1.0]}))
    assert [v["display"] for v in done["voices"]] == ["Спикер 1", "Спикер 2"]
    assert done["kept"] == []


def test_two_new_clusters_never_share_a_name(meeting, base):
    """Оба новых кластера база узнала как одного человека (или один наследует
    имя, а другого база зовёт так же): имя получает один, второй — свободный
    «Спикер N», а не два разных голоса под одним именем."""
    data = speakers._transcript(meeting)
    seg = data["segments"]
    parts = [[{**seg[0], "speaker": "R0"}], None, [{**seg[2], "speaker": "R1"}], None,
             [{**seg[4], "speaker": "R1"}]]
    voices = {"R0": [1.0, -1.0, 0.0], "R1": [-1.0, 1.0, 0.0]}
    done = rediarize.finish(meeting, data, _got(parts, voices, {"R0": "Анна Смирнова", "R1": "Анна Смирнова"}))
    labels = [v["display"] for v in done["voices"]]
    assert labels[0] == "Анна Смирнова" and labels[1] != "Анна Смирнова"
    assert len(set(labels)) == 2
    # Наследник по времени («Спикер 1» → имя из базы у другого кластера).
    speakers.apply(meeting, [{"type": "rename", "label": "Спикер 1", "to": "Глеб"}], {}, base)
    data = speakers._transcript(meeting)
    seg = data["segments"]
    parts = [[{**seg[0], "speaker": "R0"}], None, [{**seg[2], "speaker": "R0"}], None,
             [{**seg[4], "speaker": "R1"}]]
    done = rediarize.finish(meeting, data, _got(parts, voices, {"R1": "Глеб"}))
    labels = [v["display"] for v in done["voices"]]
    assert labels[0] == "Глеб" and labels[1] not in ("Глеб", "Вы") and len(set(labels)) == 2


def test_old_call_recording_keeps_owner_turns_decided_by_audio(meeting, base, monkeypatch):
    """Старая запись звонка без пометок: задача решает по звуку, какие реплики
    с микрофона (их не переразделяют), и применение пишет дорожки
    с `track_source: "audio"`."""
    from meet import segvoices

    data = library.read_transcript_full(meeting)
    data.pop("track_marks")
    for s in data["segments"]:
        s.pop("track", None)
    data["segments"][1]["speaker"] = "Олег"     # владелец, переименованный без настроек
    library.write_transcript(meeting, data)
    rate = segvoices.ENERGY_RATE
    rng = np.random.default_rng(3)
    mic = rng.normal(0, 3, rate * 14)
    sys_ = rng.normal(0, 3, rate * 14)
    mic[int(4.2 * rate):int(5.0 * rate)] += rng.normal(0, 8000, int(0.8 * rate))
    for a, b in ((0.0, 4.0), (6.0, 9.0), (10.0, 12.0)):
        sys_[int(a * rate):int(b * rate)] += rng.normal(0, 6000, int((b - a) * rate))
    tracks = {"mic.opus": mic.astype(np.int16), "sys.opus": sys_.astype(np.int16)}
    _run(meeting, base, monkeypatch, energy=lambda src: tracks[src.name])
    assert segvoices.read_decisions(meeting) == {"0.00-4.00": "sys", "4.20-5.00": "mic",
                                                 "6.00-9.00": "sys", "10.00-12.00": "sys"}
    got = json.loads((meeting / rediarize.PREVIEW_NAME).read_text(encoding="utf-8"))
    assert got["parts"][1] is None                  # реплика владельца не переразделяется
    rediarize.apply(meeting, base)
    segs = library.read_transcript(meeting)["segments"]
    owner = next(s for s in segs if s["text"] == "Отлично.")
    assert owner["speaker"] == "Олег" and (owner["track"], owner["track_source"]) == ("mic", "audio")
    assert {(s["track"], s["track_source"]) for s in segs if s.get("kind") != "break" and s is not owner} \
        == {("sys", "audio")}
