"""Поиск голоса владельца по прошлым встречам (meet.owner_derive, T8).

Встречи синтетические: дорожки — пустые файлы, звук отдаёт подставной
декодер по имени папки. В микрофоне голос «закодирован» уровнем отсчётов
(голос N — постоянный уровень N·1000), подставной эмбеддер по уровню куска
отдаёт вектор этого голоса с небольшим шумом и не единичной длины (как у
community-1). Модели не грузятся, сеть не нужна."""

import json

import numpy as np
import pytest

from meet import owner_derive, owner_voice, segvoices

DIM = 16
VOICES = {v: np.eye(DIM)[v] for v in range(1, 10)}
OWNER, OTHER = 1, 2
RUN_S = 4.0
GAP_S = 2.0


def _voice_of(clip: np.ndarray) -> int:
    level = float(np.median(np.abs(clip))) * 32768.0
    return int(round(level / 1000.0))


class FakeEmbed:
    def __init__(self):
        self.calls = 0

    def __call__(self, clip):
        self.calls += 1
        v = _voice_of(clip)
        if v not in VOICES:
            return None
        rng = np.random.default_rng(len(clip) + v)
        return (VOICES[v] + rng.normal(0, 0.05, DIM)) * 3.0


def _runs(voices, start=5.0):
    """Отрезки речи: по RUN_S с паузой GAP_S, голос — по списку."""
    out, t = [], start
    for v in voices:
        out.append((t, t + RUN_S, v))
        t += RUN_S + GAP_S
    return out


def _words(a, b, step=0.5):
    out, t = [], a
    while t < b - 1e-6:
        out.append([round(t, 2), round(min(b, t + step), 2), " слово"])
        t += step
    return out


class Library:
    """Папка записей и звук встреч для подставного декодера."""

    def __init__(self, root):
        self.root = root
        self.audio = {}

    def meeting(self, name, voices, *, minutes=30, words=True, merged=False, call=True,
                loud_sys=(), speaker="Вы", text_phase=False, segments=None):
        folder = self.root / name
        folder.mkdir(parents=True)
        (folder / "mic.opus").write_bytes(b"")
        if call:
            (folder / "sys.opus").write_bytes(b"")
        runs = _runs(voices)
        segs = segments if segments is not None else [
            {"start": a, "end": b, "speaker": speaker, "text": "слово " * int((b - a) / 0.5),
             "track": "mic", **({"words": _words(a, b)} if words else {})} for a, b, _ in runs]
        data = {"version": 1, "segments": segs, "track_marks": "pipeline"}
        if text_phase:
            data["phase"] = "text"
        (folder / "transcript.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        (folder / "events.jsonl").write_text(
            json.dumps({"kind": "record.stopped", "duration_s": minutes * 60.0}) + "\n", encoding="utf-8")
        if merged:
            (folder / "meta.json").write_text(json.dumps({"merged_from": ["a", "b"]}), encoding="utf-8")
        end = (runs[-1][1] if runs else 0.0) + 5.0
        self.audio[name] = {"runs": runs, "end": end, "loud_sys": list(loud_sys)}
        return folder

    def decode(self, path, rate=16000):
        spec = self.audio[path.parent.name]
        n = int(spec["end"] * rate)
        if path.stem == "mic":
            out = np.zeros(n, dtype=np.int16)
            for a, b, v in spec["runs"]:
                out[int(a * rate):int(b * rate)] = v * 1000
            return out
        out = np.zeros(n, dtype=np.int16)
        rng = np.random.default_rng(7)
        for a, b in spec["loud_sys"]:
            out[int(a * rate):int(b * rate)] = rng.integers(-12000, 12000, int(b * rate) - int(a * rate))
        return out


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "recordings")


@pytest.fixture
def voices_dir(tmp_path):
    d = tmp_path / "voices"
    d.mkdir()
    return d


def _find(lib, voices_dir, **kw):
    embed = kw.pop("embed", None) or FakeEmbed()
    return owner_derive.find(lib.root, voices_dir, embed=embed, decode=lib.decode,
                             owner_labels={"Вы"}, threshold=0.75, **kw)


def _five_and_one(lib):
    for day in range(1, 6):
        lib.meeting(f"2026-09-0{day}_10-00", [OWNER] * 20)
    lib.meeting("2026-09-06_10-00", [OTHER] * 20)


# --- кандидаты ---------------------------------------------------------------------


def test_candidates_are_recent_long_unmerged_calls(lib):
    for day in range(1, 10):
        lib.meeting(f"2026-08-0{day}_10-00", [OWNER])
    for day in range(10, 14):
        lib.meeting(f"2026-08-{day}_10-00", [OWNER])
    lib.meeting("2026-09-01_10-00", [OWNER], minutes=4)          # короче 5 минут
    lib.meeting("2026-09-02_10-00", [OWNER], merged=True)        # объединённая
    lib.meeting("2026-09-03_10-00", [OWNER], call=False)         # не звонок
    lib.meeting("2026-09-04_10-00", [OWNER], text_phase=True)    # спикеры ещё не готовы
    got = [f.name for f in owner_derive.candidates(lib.root)]
    assert len(got) == owner_derive.MAX_MEETINGS == 10
    assert got[0] == "2026-08-13_10-00" and got[-1] == "2026-08-04_10-00"


def test_duration_falls_back_to_transcript_end(lib):
    folder = lib.meeting("2026-09-01_10-00", [OWNER] * 2)
    (folder / "events.jsonl").unlink()
    data = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    data["segments"][-1]["end"] = 400.0
    (folder / "transcript.json").write_text(json.dumps(data), encoding="utf-8")
    assert [f.name for f in owner_derive.candidates(lib.root)] == ["2026-09-01_10-00"]


# --- участки речи владельца ------------------------------------------------------------


def test_runs_come_from_words_not_whole_segments(lib):
    """Длинный сегмент со словами на 6-й и 59-й секундах (T0) — не участок на 60 с."""
    seg = {"start": 0.0, "end": 60.0, "speaker": "Вы", "track": "mic", "text": "а б",
           "words": [[5.5, 6.0, " а"], [58.6, 59.0, " б"]]}
    words = _words(70.0, 73.0) + _words(73.4, 75.0) + _words(76.0, 77.0)
    seg2 = {"start": 70.0, "end": 77.0, "speaker": "Вы", "track": "mic",
            "text": "".join(w[2] for w in words).strip(), "words": words}
    room = {"start": 80.0, "end": 90.0, "speaker": "Спикер 1", "track": "mic", "text": "x",
            "words": _words(80.0, 90.0)}
    sys = {"start": 90.0, "end": 99.0, "speaker": "Спикер 2", "text": "x", "words": _words(90.0, 99.0)}
    runs = owner_derive.owner_runs({"segments": [seg, seg2, room, sys], "track_marks": "pipeline"},
                                   {"Вы"})
    # 70–75 (пауза 0,4 с внутри — тот же участок); 76–77 короче 2 с — не участок.
    assert [(r.start, r.end) for r in runs] == [(70.0, 75.0)]


def test_long_runs_are_cut_into_pieces(lib):
    seg = {"start": 0.0, "end": 25.0, "speaker": "Вы", "track": "mic", "text": "x",
           "words": _words(0.0, 25.0)}
    runs = owner_derive.owner_runs({"segments": [seg], "track_marks": "pipeline"}, {"Вы"})
    assert all(owner_derive.RUN_MIN_S <= r.end - r.start <= owner_derive.RUN_MAX_S for r in runs)
    assert runs[0].start == 0.0 and runs[-1].end == 25.0


def test_segments_without_words_use_vad(lib):
    seg = {"start": 10.0, "end": 40.0, "speaker": "Вы", "track": "mic", "text": "x"}
    calls = []

    def vad(audio, rate):
        calls.append(len(audio) / rate)
        return [(1.0, 4.0), (4.3, 6.0), (20.0, 21.0)]

    audio = np.zeros(50 * 16000, dtype=np.int16)
    runs = owner_derive.owner_runs({"segments": [seg], "track_marks": "pipeline"}, {"Вы"},
                                   mic=lambda: audio, vad=vad)
    assert calls == [30.0]
    assert [(r.start, r.end) for r in runs] == [(11.0, 16.0)]


def test_runs_where_sys_talks_are_dropped(lib, voices_dir):
    lib.meeting("2026-09-01_10-00", [OWNER] * 4, loud_sys=[(5.0, 7.0), (11.0, 11.4)])
    folder = lib.root / "2026-09-01_10-00"
    voice = owner_derive.meeting_voice(folder, embed=FakeEmbed(), decode=lib.decode, owner_labels={"Вы"})
    # Первый участок (5–9): sys звучит половину — прочь; второй (11–15): 10 % — годится.
    assert [r.start for r in voice.runs] == [11.0, 17.0, 23.0]


# --- голос встречи --------------------------------------------------------------------


def test_meeting_voice_is_dominant_cluster(lib):
    lib.meeting("2026-09-01_10-00", [OWNER] * 16 + [OTHER] * 4)
    voice = owner_derive.meeting_voice(lib.root / "2026-09-01_10-00", embed=FakeEmbed(),
                                       decode=lib.decode, owner_labels={"Вы"})
    assert voice.usable and voice.seconds == pytest.approx(64.0)
    assert voice.share == pytest.approx(0.8)
    assert float(voice.centroid @ VOICES[OWNER]) > 0.95
    assert np.linalg.norm(voice.centroid) == pytest.approx(1.0)


def test_mixed_meeting_has_no_voice(lib):
    """Два голоса поровну: доминирующего (от 60 %) нет."""
    lib.meeting("2026-09-01_10-00", [OWNER, OTHER] * 10)
    voice = owner_derive.meeting_voice(lib.root / "2026-09-01_10-00", embed=FakeEmbed(),
                                       decode=lib.decode, owner_labels={"Вы"})
    assert not voice.usable


def test_little_speech_has_no_voice(lib):
    lib.meeting("2026-09-01_10-00", [OWNER] * 10)  # 40 с < 60 с
    voice = owner_derive.meeting_voice(lib.root / "2026-09-01_10-00", embed=FakeEmbed(),
                                       decode=lib.decode, owner_labels={"Вы"})
    assert not voice.usable and voice.seconds == pytest.approx(40.0)


def test_cached_segment_voices_are_used_and_normalised(lib):
    lib.meeting("2026-09-01_10-00", [OWNER] * 20)
    folder = lib.root / "2026-09-01_10-00"
    data = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    segvoices.write_cache(folder, {segvoices.key("mic", s): VOICES[OWNER] * 2.5 for s in data["segments"]})
    embed = FakeEmbed()
    voice = owner_derive.meeting_voice(folder, embed=embed, decode=lib.decode, owner_labels={"Вы"})
    assert embed.calls == 0 and voice.usable
    assert float(voice.centroid @ VOICES[OWNER]) == pytest.approx(1.0, abs=1e-3)


def test_cache_of_a_longer_segment_is_not_used_for_a_part_of_it(lib):
    words = _words(0.0, 4.0) + _words(20.0, 24.0)
    seg = {"start": 0.0, "end": 24.0, "speaker": "Вы", "track": "mic",
           "text": "".join(w[2] for w in words).strip(), "words": words}
    lib.meeting("2026-09-01_10-00", [OWNER] * 5, segments=[seg])
    folder = lib.root / "2026-09-01_10-00"
    segvoices.write_cache(folder, {segvoices.key("mic", seg): VOICES[OTHER]})
    embed = FakeEmbed()
    owner_derive.meeting_voice(folder, embed=embed, decode=lib.decode, owner_labels={"Вы"})
    assert embed.calls == 2


# --- по всем встречам --------------------------------------------------------------------


def test_five_meetings_of_one_voice_give_a_suggestion(lib, voices_dir):
    _five_and_one(lib)
    got = _find(lib, voices_dir)
    assert got.status == "suggested" and got.reason is None
    found = got.suggestion
    assert float(found["embedding"] @ VOICES[OWNER]) > 0.95
    assert np.linalg.norm(found["embedding"]) == pytest.approx(1.0)
    assert sorted(found["meetings"]) == [f"2026-09-0{d}_10-00" for d in range(1, 6)]
    refs = found["samples"]
    assert len(refs) == 3 and len({r["recording"] for r in refs}) == 3
    assert all(r["track"] == "mic" and r["recording"] in found["meetings"] for r in refs)
    assert all(owner_derive.SAMPLE_MIN_S <= r["end"] - r["start"] <= owner_derive.RUN_MAX_S for r in refs)
    assert found["seconds"] == pytest.approx(5 * 80.0) and found["quality"] > 0.9
    assert got.checked == 6 and got.found == 5


def test_inconsistent_voices_give_honest_reason(lib, voices_dir):
    for day, v in enumerate((1, 2, 3, 4, 5), start=1):
        lib.meeting(f"2026-09-0{day}_10-00", [v] * 20)
    got = _find(lib, voices_dir)
    assert got.status == "inconsistent" and got.suggestion is None
    assert "по-разному" in got.reason and "5" in got.reason


def test_group_must_cover_sixty_percent_of_candidates(lib, voices_dir):
    """Три встречи из шести с одним голосом — 50 %: мало."""
    for day in range(1, 4):
        lib.meeting(f"2026-09-0{day}_10-00", [OWNER] * 20)
    for day, v in zip(range(4, 7), (2, 3, 4)):
        lib.meeting(f"2026-09-0{day}_10-00", [v] * 20)
    assert _find(lib, voices_dir).status == "inconsistent"


def test_too_few_meetings(lib, voices_dir):
    lib.meeting("2026-09-01_10-00", [OWNER] * 20)
    lib.meeting("2026-09-02_10-00", [OWNER] * 20)
    got = _find(lib, voices_dir)
    assert got.status == "too_few" and got.suggestion is None
    assert "2" in got.reason and "3" in got.reason


def test_too_few_meetings_with_enough_speech(lib, voices_dir):
    lib.meeting("2026-09-01_10-00", [OWNER] * 20)
    lib.meeting("2026-09-02_10-00", [OWNER] * 20)
    lib.meeting("2026-09-03_10-00", [OWNER] * 5)
    lib.meeting("2026-09-04_10-00", [OWNER, OTHER] * 10)
    got = _find(lib, voices_dir)
    assert got.status == "too_few" and "2 из 4" in got.reason


def test_voice_of_someone_in_the_base_is_not_suggested(lib, voices_dir):
    _five_and_one(lib)
    (voices_dir / "Демьян.json").write_text(json.dumps({"samples": [
        {"embedding": [float(x) for x in VOICES[OWNER]]}]}), encoding="utf-8")
    got = _find(lib, voices_dir)
    assert got.status == "in_base" and got.suggestion is None and "Демьян" in got.reason


def test_voice_matching_existing_sample_needs_nothing(lib, voices_dir):
    _five_and_one(lib)
    owner_voice.add(VOICES[OWNER], source="enroll", seconds=20, device="USB", voices=voices_dir)
    got = _find(lib, voices_dir)
    assert got.status == "already" and got.suggestion is None


def test_run_saves_outcome_and_suggestion(lib, voices_dir):
    _five_and_one(lib)
    got = owner_derive.run(lib.root, voices_dir, embed=FakeEmbed(), decode=lib.decode,
                           owner_labels={"Вы"}, threshold=0.75)
    assert got.status == "suggested"
    assert owner_voice.suggestion(voices_dir)["meetings"] == got.suggestion["meetings"]
    assert owner_voice.derived(voices_dir)["status"] == "suggested"
    assert owner_voice.load(voices_dir) == []  # без «Да, это я» — не образец


def test_run_without_result_clears_old_suggestion(lib, voices_dir):
    owner_voice.save_derived({"status": "suggested"}, {"embedding": VOICES[OWNER], "meetings": ["x"],
                                                       "samples": [], "seconds": 90}, voices=voices_dir)
    got = owner_derive.run(lib.root, voices_dir, embed=FakeEmbed(), decode=lib.decode,
                           owner_labels={"Вы"}, threshold=0.75)
    assert got.status == "too_few"
    assert owner_voice.suggestion(voices_dir) is None
    assert owner_voice.derived(voices_dir)["reason"] == got.reason


def test_embedder_is_not_loaded_when_everything_is_cached(lib, voices_dir, monkeypatch):
    for day in range(1, 4):
        folder = lib.meeting(f"2026-09-0{day}_10-00", [OWNER] * 20)
        data = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
        segvoices.write_cache(folder, {segvoices.key("mic", s): VOICES[OWNER] for s in data["segments"]})
    monkeypatch.setattr(owner_derive, "_load_embedder", lambda: pytest.fail("эмбеддер не нужен"))
    got = owner_derive.find(lib.root, voices_dir, decode=lib.decode, owner_labels={"Вы"}, threshold=0.75)
    assert got.status == "suggested"


def test_broken_meeting_is_skipped_not_fatal(lib, voices_dir):
    """Сбой одной встречи (ffmpeg не прочитал дорожку) — встреча пропускается."""
    _five_and_one(lib)
    real = lib.decode

    def decode(path, rate=16000):
        if path.parent.name == "2026-09-03_10-00":
            raise RuntimeError("ffmpeg не смог прочитать sys.opus")
        return real(path, rate)

    logged = []
    got = owner_derive.find(lib.root, voices_dir, embed=FakeEmbed(), decode=decode, owner_labels={"Вы"},
                            threshold=0.75, log=logged.append)
    assert got.status == "suggested" and "2026-09-03_10-00" not in got.suggestion["meetings"]
    assert any("2026-09-03_10-00" in line for line in logged)


def test_every_meeting_failing_is_an_error_not_too_few(lib, voices_dir):
    _five_and_one(lib)

    def decode(path, rate=16000):
        raise RuntimeError("ffmpeg не найден")

    with pytest.raises(owner_derive.DeriveError, match="ffmpeg не найден"):
        owner_derive.find(lib.root, voices_dir, embed=FakeEmbed(), decode=decode, owner_labels={"Вы"},
                          threshold=0.75, log=lambda text: None)


def test_embedder_failure_is_an_error_not_too_few_meetings(lib, voices_dir, monkeypatch):
    _five_and_one(lib)

    def broken():
        raise RuntimeError("нет модели")

    monkeypatch.setattr(owner_derive, "_load_embedder", broken)
    with pytest.raises(owner_derive.EmbedderError, match="нет модели"):
        owner_derive.find(lib.root, voices_dir, decode=lib.decode, owner_labels={"Вы"}, threshold=0.75)


# --- исправления после ревью ------------------------------------------------------


def test_quiet_meetings_do_not_make_consistent_voice_inconsistent(lib, voices_dir):
    """Три встречи с ясным голосом и три, где владелец почти молчал: 60 %
    считается от встреч с голосом, а не от всех кандидатов."""
    for day in range(1, 4):
        lib.meeting(f"2026-09-0{day}_10-00", [OWNER] * 20)
    for day in range(4, 7):
        lib.meeting(f"2026-09-0{day}_10-00", [OWNER] * 5)
    got = _find(lib, voices_dir)
    assert got.status == "suggested" and got.checked == 6 and got.used == 3 and got.found == 3


def test_disagreeing_usable_meetings_are_still_inconsistent(lib, voices_dir):
    for day in range(1, 4):
        lib.meeting(f"2026-09-0{day}_10-00", [OWNER] * 20)
    for day, v in zip(range(4, 6), (2, 3)):
        lib.meeting(f"2026-09-0{day}_10-00", [v] * 20)
    lib.meeting("2026-09-06_10-00", [4] * 20)
    lib.meeting("2026-09-07_10-00", [OWNER] * 5)  # без голоса — в долю не идёт
    got = _find(lib, voices_dir)
    assert got.status == "inconsistent" and "3 из 6" in got.reason and "4" in got.reason


def test_speech_per_meeting_is_capped(lib, voices_dir):
    lib.meeting("2026-09-01_10-00", [OWNER] * 120)  # 480 с
    embed = FakeEmbed()
    voice = owner_derive.meeting_voice(lib.root / "2026-09-01_10-00", embed=embed, decode=lib.decode,
                                       owner_labels={"Вы"})
    used = sum(r.seconds for r in voice.runs)
    assert used <= owner_derive.MAX_SPEECH_S + RUN_S and embed.calls == len(voice.runs)
    # Равномерно по встрече, а не первые минуты.
    assert voice.runs[0].start < 60 and voice.runs[-1].start > 600


def test_long_vad_region_is_cut_into_pieces():
    seg = {"start": 0.0, "end": 40.0, "speaker": "Вы", "track": "mic", "text": "x"}
    audio = np.zeros(40 * 16000, dtype=np.int16)
    runs = owner_derive.owner_runs({"segments": [seg]}, {"Вы"}, mic=lambda: audio,
                                   vad=lambda a, rate: [(1.0, 26.0), (26.3, 27.0)])
    assert len(runs) == 3
    assert all(r.seconds <= owner_derive.RUN_MAX_S + owner_derive.RUN_MIN_S for r in runs)
    assert runs[0].start == 1.0 and runs[-1].end == 27.0


def test_merged_by_source_is_not_a_candidate(lib):
    folder = lib.meeting("2026-09-01_10-00", [OWNER])
    (folder / "meta.json").write_text(json.dumps({"source": "merge"}), encoding="utf-8")
    assert owner_derive.candidates(lib.root) == []


def test_owner_names_and_threshold_come_from_settings(lib, voices_dir, monkeypatch):
    """Без owner_labels — имена владельца из настроек (и прежние); без
    threshold — порог узнавания из настроек, не выше 0.75."""
    from types import SimpleNamespace

    from meet import settings

    cfg = SimpleNamespace(recording=SimpleNamespace(speaker_name="Кузьма", former_speaker_names=("Ник",)),
                          asr=SimpleNamespace(voice_threshold=0.70))
    monkeypatch.setattr(settings, "load", lambda: cfg)
    for day in range(1, 4):
        lib.meeting(f"2026-09-0{day}_10-00", [OWNER] * 20, speaker="Ник" if day == 1 else "Кузьма")
    near = 0.72 * VOICES[OWNER] + np.sqrt(1 - 0.72 ** 2) * VOICES[9]
    (voices_dir / "Демьян.json").write_text(json.dumps({"samples": [
        {"embedding": [float(x) for x in near]}]}), encoding="utf-8")
    got = owner_derive.find(lib.root, voices_dir, embed=FakeEmbed(), decode=lib.decode)
    assert got.status == "in_base" and got.found == 3  # 0.72 ≥ 0.70
    cfg.asr.voice_threshold = 0.82  # → не выше 0.75: 0.72 — не совпадение
    assert owner_derive.find(lib.root, voices_dir, embed=FakeEmbed(), decode=lib.decode).status == "suggested"


def test_outcome_remembers_what_made_it(lib, voices_dir):
    """Чем вызван итог — чтобы окно не показывало устаревшую причину."""
    _five_and_one(lib)
    sample = owner_voice.add(VOICES[OWNER], source="enroll", seconds=20, device="USB", voices=voices_dir)
    raw = _find(lib, voices_dir).to_raw()
    assert raw["status"] == "already" and raw["sample_id"] == sample.id
    assert raw["newest"] == "2026-09-06_10-00"
    owner_voice.remove(sample.id, voices_dir)
    (voices_dir / "Демьян.json").write_text(json.dumps({"samples": [
        {"embedding": [float(x) for x in VOICES[OWNER]]}]}), encoding="utf-8")
    raw = _find(lib, voices_dir).to_raw()
    assert raw["status"] == "in_base" and raw["person"] == "Демьян"


def test_newest_recording_is_by_folder_name(lib, tmp_path):
    lib.meeting("2026-09-01_10-00", [OWNER])
    lib.meeting("2026-09-03_10-00", [OWNER], call=False)
    (lib.root / ".deleting-2026-09-09_10-00").mkdir()
    (lib.root / "заметки").mkdir()
    assert owner_derive.newest_recording(lib.root) == "2026-09-03_10-00"
    assert owner_derive.newest_recording(tmp_path / "нет") is None


def test_voice_unlike_saved_sample_is_flagged(lib, voices_dir):
    """Есть образец, а найденный голос на него не похож: предложение с пометкой."""
    _five_and_one(lib)
    owner_voice.add(VOICES[5], source="enroll", seconds=20, device="USB", voices=voices_dir)
    got = _find(lib, voices_dir)
    assert got.status == "suggested" and got.suggestion["conflict"] is True
    owner_voice.remove(owner_voice.load(voices_dir)[0].id, voices_dir)
    assert _find(lib, voices_dir).suggestion["conflict"] is False


# --- подписи владельца по встрече (segvoices.owner_labels) --------------------------


def _rename(folder, names, sidecar):
    data = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    data["names"] = names
    (folder / "transcript.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (folder / f"{folder.name}_speakers.json").write_text(json.dumps(
        {"model": "m", "speakers": sidecar}, ensure_ascii=False), encoding="utf-8")


def _owner_entry(**extra):
    # Вектор сайдкара — чужой голос: поиск его не читает никогда.
    return {"label": "OWNER", "display": "Вы", "embedding": [float(x) for x in VOICES[OTHER]],
            "track": "mic", "owner": True, **extra}


def test_owner_renamed_in_the_meeting_is_still_the_owner(lib, monkeypatch):
    monkeypatch.setattr(segvoices, "owners", lambda: {"Вы"})
    folder = lib.meeting("2026-09-01_10-00", [OWNER] * 20, speaker="Кузьма")
    _rename(folder, {"Вы": "Кузьма"}, [_owner_entry()])
    voice = owner_derive.meeting_voice(folder, embed=FakeEmbed(), decode=lib.decode, owner_labels=None)
    assert voice.usable and voice.seconds == pytest.approx(80.0)
    assert float(voice.centroid @ VOICES[OWNER]) > 0.95  # голос — из звука, не из сайдкара


def test_rename_of_an_unconfirmed_owner_candidate_is_not_evidence(lib, monkeypatch):
    """Кандидат (образец владельца не узнал) переименован в другого человека —
    его реплики не становятся речью владельца."""
    monkeypatch.setattr(segvoices, "owners", lambda: {"Вы"})
    folder = lib.meeting("2026-09-01_10-00", [OWNER] * 20, speaker="Демьян")
    _rename(folder, {"Вы": "Демьян"}, [_owner_entry(candidate=True)])
    voice = owner_derive.meeting_voice(folder, embed=FakeEmbed(), decode=lib.decode, owner_labels=None)
    assert voice.runs == [] and not voice.usable
    # Те же реплики под подписью владельца — как у встречи без образца.
    folder2 = lib.meeting("2026-09-02_10-00", [OWNER] * 20)
    _rename(folder2, {}, [_owner_entry(candidate=True)])
    assert owner_derive.meeting_voice(folder2, embed=FakeEmbed(), decode=lib.decode, owner_labels=None).usable


def test_find_uses_labels_of_each_meeting(lib, voices_dir, monkeypatch):
    monkeypatch.setattr(segvoices, "owners", lambda: {"Вы"})
    for day in range(1, 4):
        folder = lib.meeting(f"2026-09-0{day}_10-00", [OWNER] * 20, speaker="Кузьма" if day == 2 else "Вы")
        if day == 2:
            _rename(folder, {"Вы": "Кузьма"}, [_owner_entry()])
    got = owner_derive.find(lib.root, voices_dir, embed=FakeEmbed(), decode=lib.decode, threshold=0.75)
    assert got.status == "suggested" and got.found == 3
