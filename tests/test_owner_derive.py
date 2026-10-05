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
    _five_and_one(lib)
    (lib.root / "2026-09-03_10-00" / "transcript.json").write_text("{битый", encoding="utf-8")
    got = _find(lib, voices_dir)
    assert got.status == "suggested" and "2026-09-03_10-00" not in got.suggestion["meetings"]


def test_embedder_failure_is_an_error_not_too_few_meetings(lib, voices_dir, monkeypatch):
    _five_and_one(lib)

    def broken():
        raise RuntimeError("нет модели")

    monkeypatch.setattr(owner_derive, "_load_embedder", broken)
    with pytest.raises(owner_derive.EmbedderError, match="нет модели"):
        owner_derive.find(lib.root, voices_dir, decode=lib.decode, owner_labels={"Вы"}, threshold=0.75)
