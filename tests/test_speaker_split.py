"""«Разделить спикера» по голосу: кэш голосов реплик, кластеризация на K
голосов, разбор по образцам из базы с группой «не уверен», применение одним
шагом истории (и его отмена вместе с сайдкаром голосов).

Голоса — синтетические векторы, реплики и имена выдуманы."""

import json

import numpy as np
import pytest

from meet import library, segvoices, speaker_split, speakers

DIM = 8
A = np.eye(DIM)[0]
B = np.eye(DIM)[1]
C = np.eye(DIM)[2]


def _noisy(center, seed, scale=0.15):
    rng = np.random.default_rng(seed)
    v = center + rng.normal(0, scale, DIM)
    return v / np.linalg.norm(v)


def _seg(start, end, speaker, text, **extra):
    return {"start": start, "end": end, "speaker": speaker, "text": text, "uncertain": False, **extra}


# «Спикер 2» на деле двое: голос A (реплики 1, 3, 5, 9) и голос B (2, 4, 6, 8);
# реплика 7 короче 0,8 с — голоса нет, берёт группу соседа.
VOICE = {1: A, 2: B, 3: A, 4: B, 5: A, 6: B, 8: B, 9: A}
SEGMENTS = [
    _seg(0.0, 3.0, "Спикер 1", "Добрый день, коллеги."),
    _seg(3.0, 8.0, "Спикер 2", "Начну с отчёта по складу."),
    _seg(8.5, 12.0, "Спикер 2", "А у нас в отделе закупок всё по плану."),
    _seg(14.0, 20.0, "Спикер 2", "Отгрузка в четверг, как договаривались."),
    _seg(22.0, 25.0, "Спикер 2", "Поставщик подтвердил цены."),
    _seg(26.0, 30.0, "Спикер 2", "Склад готов принять партию."),
    _seg(31.0, 36.0, "Спикер 2", "Счёт оплатим до пятницы."),
    _seg(36.2, 36.7, "Спикер 2", "Да."),
    _seg(37.0, 41.0, "Спикер 2", "И договор продлим на год."),
    _seg(43.0, 47.0, "Спикер 2", "Тогда на складе всё."),
    _seg(48.0, 50.0, "Вы", "Спасибо, понятно.", track="mic"),
]


@pytest.fixture
def meeting(tmp_path):
    folder = tmp_path / "recordings" / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "created_at": "2026-09-30T17:00:00",
                                      "track_marks": "pipeline",
                                      "segments": [dict(s) for s in SEGMENTS]})
    (folder / "2026-09-30_speakers.json").write_text(json.dumps({
        "model": "m", "source": "C:/rec/2026-09-30_16-04", "date": "2026-09-30",
        "speakers": [
            {"label": "SPEAKER_00", "display": "Спикер 1", "embedding": list(C)},
            {"label": "SPEAKER_01", "display": "Спикер 2", "embedding": list((A + B) / np.sqrt(2))},
        ]}, ensure_ascii=False), encoding="utf-8")
    return folder


def _cache(folder, voices=VOICE):
    segs = library.read_transcript(folder)["segments"]
    segvoices.write_cache(folder, {segvoices.key("sys", segs[i]): _noisy(c, i) for i, c in voices.items()})


@pytest.fixture
def base(tmp_path):
    folder = tmp_path / "voices"
    folder.mkdir()
    return folder


def _person(base, name, vec, source="C:/rec/old"):
    (base / f"{name}.json").write_text(json.dumps({"samples": [
        {"embedding": [float(x) for x in vec], "source": source, "date": "2026-09-01", "id": name}]},
        ensure_ascii=False), encoding="utf-8")


def _labels(folder):
    return [s["speaker"] for s in library.read_transcript(folder)["segments"]]


# --- кэш голосов реплик ---------------------------------------------------------


def test_cache_roundtrip_is_compact_and_keyed_by_track_and_time(meeting):
    seg = SEGMENTS[1]
    segvoices.write_cache(meeting, {segvoices.key("sys", seg): A})
    assert segvoices.key("sys", seg) == "sys:3.00-8.00"
    got = segvoices.read_cache(meeting)
    assert np.allclose(got["sys:3.00-8.00"], A, atol=1e-3)
    raw = json.loads((meeting / segvoices.CACHE_NAME).read_text(encoding="utf-8"))
    assert isinstance(raw["items"]["sys:3.00-8.00"], str)  # base64 float16, не список чисел


def test_compute_embeds_only_missing_long_segments_from_their_track(meeting):
    loaded, embedded = [], []

    def load(src):
        loaded.append(src.name)
        return np.zeros(16000 * 60, dtype=np.int16)

    def embed(audio):
        embedded.append(len(audio))
        return None if len(embedded) == 2 else A.astype(np.float32)  # вторая — модель не справилась

    segvoices.write_cache(meeting, {segvoices.key("sys", SEGMENTS[1]): B})
    got = segvoices.compute(meeting, list(range(1, 11)), embed=embed, load=load)
    # 1 уже в кэше, 7 короче 0,8 с, 10 — микрофон (не наш спикер, но idx его включает: своя дорожка).
    assert got == {"computed": 7, "skipped": 1, "cached": 1}
    assert sorted(loaded) == ["mic.opus", "sys.opus"]
    cache = segvoices.read_cache(meeting)
    assert "mic:48.00-50.00" in cache and "sys:36.20-36.70" not in cache
    # Не посчитанный моделью сегмент не считается заново.
    assert segvoices.missing(meeting, library.read_transcript(meeting)["segments"], list(range(1, 11))) == []
    again = segvoices.compute(meeting, list(range(1, 11)), embed=embed, load=load)
    assert again["computed"] == 0 and len(loaded) == 2


def test_old_call_transcript_gets_mic_tracks_from_labels_without_clusters(meeting):
    data = library.read_transcript(meeting)
    data.pop("track_marks")
    for s in data["segments"]:
        s.pop("track", None)
    data["names"] = {"Спикер 1": "Анна"}
    data["segments"][0]["speaker"] = "Анна"
    library.write_transcript(meeting, data)
    assert speakers.normalize(meeting, tracks=True) is True
    data = library.read_transcript(meeting)
    assert [s.get("track") for s in data["segments"]] == [None] * 10 + ["mic"]
    assert data["track_marks"] == "inferred"
    assert speakers.normalize(meeting, tracks=True) is False  # один раз


# --- кластеризация и разбор по базе --------------------------------------------


def test_cluster_splits_two_voices_and_orders_groups_by_talk_time():
    x = np.stack([_noisy(A, i) for i in range(10)] + [_noisy(B, 100 + i) for i in range(6)])
    w = np.array([1.0] * 10 + [5.0] * 6)  # у B меньше реплик, но говорит дольше
    got = speaker_split.cluster(x, w, 2)
    assert len(set(got[:10])) == 1 and len(set(got[10:])) == 1
    assert got[10] == 0 and got[0] == 1


def test_cluster_three_voices():
    x = np.stack([_noisy(c, n * 10 + i) for n, c in enumerate((A, B, C)) for i in range(7)])
    got = speaker_split.cluster(x, np.ones(len(x)), 3)
    assert [len(set(got[n * 7:(n + 1) * 7])) for n in range(3)] == [1, 1, 1]
    assert len(set(got)) == 3


def test_by_people_puts_weak_and_ambiguous_segments_into_unsure():
    base = {"Анна": [A], "Борис": [B]}
    x = np.stack([_noisy(A, 1, 0.05), _noisy(B, 2, 0.05),
                  speaker_split._unit(A + B),     # одинаково похож на обоих
                  C])                             # ни на кого не похож
    got, score = speaker_split.by_people(x, base, ["Анна", "Борис"])
    assert got.tolist() == [0, 1, -1, -1]
    assert score[0] > 0.9


# --- предпросмотр и применение --------------------------------------------------


def test_status_tells_whether_voices_are_ready(meeting):
    got = speaker_split.status(meeting, "Спикер 2")
    assert got["segments"] == 9 and got["voiced"] == 8 and got["missing"] == 8 and not got["ready"]
    _cache(meeting)
    assert speaker_split.status(meeting, "Спикер 2")["ready"] is True
    with pytest.raises(speakers.SpeakerError):
        speaker_split.status(meeting, "Нет такого")


def test_preview_auto_two_voices_with_suggestions_and_inherited_short_turn(meeting, base):
    _cache(meeting)
    _person(base, "Борис Козлов", B)
    got = speaker_split.preview(meeting, "Спикер 2", base, mode="auto", k=2)
    groups = {tuple(g["idx"]): g for g in got["groups"]}
    assert set(groups) == {(1, 3, 5, 9), (2, 4, 6, 7, 8)}  # «Да.» (7) — к соседям из B
    b = groups[(2, 4, 6, 7, 8)]
    assert b["name"] == "Борис Козлов" and b["suggestions"][0]["score"] > 0.9
    assert groups[(1, 3, 5, 9)]["name"] is None
    assert b["seconds"] == pytest.approx(3.5 + 3 + 5 + 0.5 + 4)
    assert len(b["samples"]) == 3 and all(s["text"] for s in b["samples"])
    assert got["fingerprint"] == speaker_split.status(meeting, "Спикер 2")["fingerprint"]
    assert got["similar"] < 0.3   # голоса групп разные — разделение осмысленно
    with pytest.raises(speakers.SpeakerError):
        speaker_split.preview(meeting, "Спикер 2", base, mode="auto", k=9)


def test_preview_by_people_has_unsure_bucket(meeting, base):
    _cache(meeting, {**VOICE, 9: C})  # последняя реплика — ни на кого не похожа
    _person(base, "Анна Смирнова", A)
    _person(base, "Борис Козлов", B)
    got = speaker_split.preview(meeting, "Спикер 2", base, mode="people",
                                people=["Анна Смирнова", "Борис Козлов"])
    assert [(g["person"], g["idx"]) for g in got["groups"]] == [
        ("Анна Смирнова", [1, 3, 5]), ("Борис Козлов", [2, 4, 6, 7, 8])]
    assert got["unsure"]["idx"] == [9]
    with pytest.raises(speakers.SpeakerError):
        speaker_split.preview(meeting, "Спикер 2", base, mode="people", people=["Анна Смирнова"])


def test_apply_split_is_one_history_step_with_voices_and_undo(meeting, base):
    _cache(meeting)
    prev = speaker_split.preview(meeting, "Спикер 2", base, mode="auto", k=2)
    a, b = sorted(prev["groups"], key=lambda g: g["idx"][0])
    got = speaker_split.apply(meeting, "Спикер 2", [
        {"idx": a["idx"], "to": "Спикер 2"},
        {"idx": b["idx"], "to": "Борис Козлов", "remember": True},
    ], prev["fingerprint"], base)
    labels = _labels(meeting)
    assert [labels[i] for i in a["idx"]] == ["Спикер 2"] * 4
    assert [labels[i] for i in b["idx"]] == ["Борис Козлов"] * 5
    assert got["step"]["ops"][0] == {"type": "split", "label": "Спикер 2", "mode": "auto",
                                     "into": ["Спикер 2", "Борис Козлов"]}
    assert [e["person"] for e in got["step"]["enrolled"]] == ["Борис Козлов"]
    # Смешанный голос «Спикер 2» ушёл, у каждой группы — свой.
    side = json.loads((meeting / "2026-09-30_speakers.json").read_text(encoding="utf-8"))
    displays = {e["display"]: e for e in side["speakers"]}
    assert set(displays) == {"Спикер 1", "Спикер 2", "Борис Козлов"}
    assert np.dot(displays["Спикер 2"]["embedding"], A) > 0.9
    rows = {r["label"]: r for r in speakers.overview(meeting, base)["speakers"]}
    assert rows["Борис Козлов"]["has_voice"] and rows["Спикер 2"]["has_voice"]
    assert (base / "Борис Козлов.json").exists()

    speakers.undo(meeting, base)
    assert _labels(meeting) == [s["speaker"] for s in SEGMENTS]
    side = json.loads((meeting / "2026-09-30_speakers.json").read_text(encoding="utf-8"))
    assert [e["label"] for e in side["speakers"]] == ["SPEAKER_00", "SPEAKER_01"]
    assert not (base / "Борис Козлов.json").exists()

    speakers.redo(meeting, base)
    assert [_labels(meeting)[i] for i in b["idx"]] == ["Борис Козлов"] * 5
    assert (base / "Борис Козлов.json").exists()
    side = json.loads((meeting / "2026-09-30_speakers.json").read_text(encoding="utf-8"))
    assert len(side["speakers"]) == 3


def test_apply_split_new_unnamed_speakers_and_refusals(meeting, base):
    _cache(meeting)
    prev = speaker_split.preview(meeting, "Спикер 2", base, mode="auto", k=2)
    a, b = prev["groups"]
    before = (meeting / "transcript.json").read_bytes()
    with pytest.raises(speakers.Stale):
        speaker_split.apply(meeting, "Спикер 2", [{"idx": a["idx"], "to": None}], "чужой", base)
    with pytest.raises(speakers.SpeakerError):
        speaker_split.apply(meeting, "Спикер 2", [{"idx": a["idx"], "to": None},
                                                  {"idx": a["idx"][:1], "to": None}], prev["fingerprint"], base)
    with pytest.raises(speakers.SpeakerError):
        speaker_split.apply(meeting, "Спикер 2", [{"idx": a["idx"], "to": "Спикер 2"}], prev["fingerprint"], base)
    with pytest.raises(speakers.Stale):
        speaker_split.apply(meeting, "Спикер 2", [{"idx": [0], "to": None}], prev["fingerprint"], base)
    assert (meeting / "transcript.json").read_bytes() == before
    speaker_split.apply(meeting, "Спикер 2", [{"idx": a["idx"], "to": None}, {"idx": b["idx"], "to": None}],
                        prev["fingerprint"], base)
    labels = _labels(meeting)
    assert {labels[i] for i in a["idx"]} == {"Спикер 3"} and {labels[i] for i in b["idx"]} == {"Спикер 4"}


def _old_call(meeting):
    """Старая расшифровка звонка: без пометок дорожек."""
    data = library.read_transcript_full(meeting)
    data.pop("track_marks")
    for s in data["segments"]:
        s.pop("track", None)
    library.write_transcript(meeting, data)


def test_hand_named_remote_turn_stays_on_the_remote_track(meeting, base):
    _old_call(meeting)
    speakers.relabel(meeting, [2], "Ольга Петрова", base)   # имени нет ни у одного кластера
    speakers.normalize(meeting, tracks=True)                 # потом открыли панель
    segs = library.read_transcript(meeting)["segments"]
    assert segs[2]["speaker"] == "Ольга Петрова" and segs[2].get("track") is None
    assert segs[10]["track"] == "mic"


def test_refused_edit_writes_no_track_marks(meeting, base):
    _old_call(meeting)
    before = (meeting / "transcript.json").read_bytes()
    with pytest.raises(speakers.SpeakerError):
        speakers.relabel(meeting, [2], "a/b", base)
    assert (meeting / "transcript.json").read_bytes() == before


def test_track_inference_follows_history_from_diarized_labels(meeting, base):
    """Правка, сделанная до пометок (ранняя сборка): подпись «Ольга» ни к
    одному кластеру не ведёт, но история знает, что её реплики — от «Спикер 2»."""
    _old_call(meeting)
    data = library.read_transcript(meeting)
    data["segments"][2]["speaker"] = "Ольга Петрова"
    library.write_transcript(meeting, data)
    library.write_meta(meeting, {
        speakers.HISTORY: [{"id": "a1", "at": "2026-09-30T18:00:00", "count": 11, "segments": [],
                            "ops": [{"type": "relabel", "from": ["Спикер 2"], "to": "Ольга Петрова",
                                     "segments": 1, "turns": 1}]}],
        speakers.POS: 1, speakers.BASE: "2026-09-30T17:00:00"})
    speakers.normalize(meeting, tracks=True)
    segs = library.read_transcript(meeting)["segments"]
    assert segs[2].get("track") is None and segs[10]["track"] == "mic"


def test_voices_of_segments_that_no_longer_exist_are_pruned_on_commit(meeting, base):
    _cache(meeting)
    segs = library.read_transcript(meeting)["segments"]
    segvoices.write_cache(meeting, {"sys:999.00-1000.00": A})
    speakers.relabel(meeting, [0], "Спикер 2", base)
    cache = segvoices.read_cache(meeting)
    assert "sys:999.00-1000.00" not in cache
    assert segvoices.key("sys", segs[1]) in cache
