"""Исправление распознанных слов: совпадения по правилам поиска (регистр, ё=е,
целые слова), замена одного или всех одним шагом истории встречи, слова
words.json остаются выровненными; правила замены для новых расшифровок.
Тексты выдуманные."""

import json

import pytest

from meet import library, speakers, textfix


def _w(*items):
    return [[s, e, t] for s, e, t in items]


SEGMENTS = [
    {"start": 0.0, "end": 4.0, "speaker": "Спикер 1", "text": "Поднимем кубер нетис на стенде.",
     "uncertain": False, "words": _w((0.0, 0.5, " Поднимем"), (0.6, 1.0, " кубер"), (1.0, 1.6, " нетис"),
                                     (1.7, 1.9, " на"), (1.9, 2.5, " стенде."))},
    {"start": 4.5, "end": 6.0, "speaker": "Спикер 2", "text": "Кубер нетис уже ЁЛКА.", "uncertain": False,
     "words": _w((4.5, 4.9, " Кубер"), (4.9, 5.3, " нетис"), (5.3, 5.5, " уже"), (5.5, 6.0, " ЁЛКА."))},
    {"start": 6.5, "end": 8.0, "speaker": "Спикер 1", "text": "Старый сегмент про кубер нетис без слов.",
     "uncertain": False},
    {"start": 8.0, "end": 8.0, "speaker": None, "text": "Перерыв: кубер нетис", "kind": "break",
     "uncertain": False},
    {"start": 9.0, "end": 10.0, "speaker": "Спикер 2", "text": "Кубернетисы и елка.",
     "uncertain": False, "words": _w((9.0, 9.6, " Кубернетисы"), (9.6, 9.7, " и"), (9.7, 10.0, " елка."))},
]


@pytest.fixture
def meeting(tmp_path):
    folder = tmp_path / "recordings" / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    library.write_transcript(folder, {"version": 1, "created_at": "2026-09-30T17:00:00",
                                      "segments": json.loads(json.dumps(SEGMENTS))})
    return folder


@pytest.fixture
def base(tmp_path):
    return tmp_path / "voices"


def _segs(folder):
    return library.read_transcript_full(folder)["segments"]


# --- совпадения ----------------------------------------------------------------


def test_matches_are_case_insensitive_yo_insensitive_and_whole_word():
    assert textfix.matches("Кубер нетис и кубер-нетис", "кубер нетис") == [(0, 11), (14, 25)]
    assert textfix.matches("ЁЛКА и елка, ёлочка", "елка") == [(0, 4), (7, 11)]
    # Целое слово: «кубер» внутри «кубернетис» — не совпадение.
    assert textfix.matches("кубернетис", "кубер") == []
    # Без «целого слова» — подстрока (тоже без регистра и с ё=е).
    assert textfix.matches("Кубернетис", "кубер", whole_word=False) == [(0, 5)]
    assert textfix.matches("всё", "все") == [(0, 3)]
    # Знаки препинания в искомом не мешают; без букв искать нечего.
    assert textfix.matches("кубер, нетис", "кубер нетис") == [(0, 12)]
    assert textfix.matches("текст", " ,. ") == []


def test_preview_counts_with_samples_and_the_time_of_the_chosen_occurrence(meeting):
    got = textfix.preview(meeting, "кубер нетис", segment=1, offset=0)
    # Отметка перерыва — не реплика: в ней не ищем.
    assert got["count"] == 3
    assert [(s["segment"], s["offset"], s["match"]) for s in got["samples"]] == [
        (0, 9, "кубер нетис"), (1, 0, "Кубер нетис"), (2, 19, "кубер нетис")]
    assert got["samples"][0]["before"].endswith("Поднимем ") and got["samples"][0]["after"].startswith(" на")
    # Время совпадения — по словам; без слов — сегмент целиком.
    assert got["here"] == {"start": 4.5, "end": 5.3}
    assert got["samples"][0]["start"] == 0.6 and got["samples"][2]["start"] == 6.5
    assert textfix.preview(meeting, "елка")["count"] == 2


def test_preview_limits_samples(meeting):
    got = textfix.preview(meeting, "кубер нетис", limit=2)
    assert got["count"] == 3 and len(got["samples"]) == 2


# --- замена и слова ------------------------------------------------------------


def test_replace_one_occurrence_merges_word_spans(meeting, base):
    got = textfix.apply(meeting, "кубер нетис", "Kubernetes", "one", base, segment=0, offset=9, count=5)
    segs = _segs(meeting)
    assert segs[0]["text"] == "Поднимем Kubernetes на стенде."
    assert segs[0]["words"] == _w((0.0, 0.5, " Поднимем"), (0.6, 1.6, " Kubernetes"), (1.7, 1.9, " на"),
                                  (1.9, 2.5, " стенде."))
    assert library.words_match(segs[0])
    assert segs[1]["text"] == "Кубер нетис уже ЁЛКА."  # другие совпадения не тронуты
    assert got["changed"] == 1
    assert got["step"]["ops"] == [{"type": "text", "from": "кубер нетис", "to": "Kubernetes",
                                   "count": 1, "scope": "one"}]


def test_replace_all_keeps_case_and_words_aligned(meeting, base):
    got = textfix.apply(meeting, "кубер нетис", "kubernetes", "all", base)
    segs = _segs(meeting)
    assert [s["text"] for s in segs] == [
        "Поднимем kubernetes на стенде.", "Kubernetes уже ЁЛКА.", "Старый сегмент про kubernetes без слов.",
        "Перерыв: кубер нетис", "Кубернетисы и елка."]
    assert segs[1]["words"][0] == [4.5, 5.3, " Kubernetes"]
    assert all(library.words_match(s) for s in segs if "words" in s)
    assert "words" not in segs[2]
    assert got["changed"] == 3 and got["step"]["ops"][0]["count"] == 3
    # words.json: записи по сегментам с тем же временем.
    items = json.loads((meeting / "words.json").read_text(encoding="utf-8"))["items"]
    assert items[1] == [4.5, 6.0, segs[1]["words"]]


def test_one_word_into_several_spreads_over_its_time(meeting, base):
    textfix.apply(meeting, "Кубернетисы", "Kubernetes кластеры", "one", base, segment=4, offset=0)
    seg = _segs(meeting)[4]
    assert seg["text"] == "Kubernetes кластеры и елка."
    first, second = seg["words"][0], seg["words"][1]
    assert first[0] == 9.0 and second[1] == 9.6 and first[1] == second[0]
    assert (first[2], second[2]) == (" Kubernetes", " кластеры")
    assert 9.0 < first[1] < 9.6
    assert library.words_match(seg)


def test_partial_word_with_punctuation_keeps_the_rest_of_the_token(meeting, base):
    textfix.apply(meeting, "елка", "ёлка", "all", base)
    segs = _segs(meeting)
    assert segs[1]["text"] == "Кубер нетис уже ЁЛКА."  # уже «ЁЛКА» — то же слово в верхнем регистре
    assert segs[4]["text"] == "Кубернетисы и ёлка."
    assert segs[4]["words"][-1] == [9.7, 10.0, " ёлка."]


def test_case_like():
    assert textfix.case_like("Кубер", "kubernetes") == "Kubernetes"
    assert textfix.case_like("КУБЕР", "kubernetes") == "KUBERNETES"
    assert textfix.case_like("кубер", "Kubernetes") == "Kubernetes"
    assert textfix.case_like("кубер", "kubernetes") == "kubernetes"
    assert textfix.case_like("Кубер", "iOS") == "iOS"  # заглавные в замене — как написано


def test_text_steps_undo_redo_mixed_with_speaker_steps(meeting, base):
    textfix.apply(meeting, "кубер нетис", "Kubernetes", "all", base)
    speakers.relabel(meeting, [1], "Спикер 1", base)
    textfix.apply(meeting, "елка", "ёлка", "all", base)
    steps = speakers.overview(meeting, base)["history"]
    assert [s["ops"][0]["type"] for s in steps] == ["text", "relabel", "text"]

    speakers.undo(meeting, base)
    speakers.undo(meeting, base)
    assert _segs(meeting)[1]["speaker"] == "Спикер 2" and _segs(meeting)[1]["text"] == "Kubernetes уже ЁЛКА."
    speakers.undo(meeting, base)
    assert _segs(meeting) == SEGMENTS
    speakers.revert(meeting, steps[-1]["id"], base)
    segs = _segs(meeting)
    assert segs[4]["text"] == "Кубернетисы и ёлка." and segs[1]["speaker"] == "Спикер 1"
    speakers.revert(meeting, None, base)
    assert _segs(meeting) == SEGMENTS


def test_undo_refuses_when_the_text_changed_since(meeting, base):
    textfix.apply(meeting, "кубер нетис", "Kubernetes", "one", base, segment=0, offset=9)
    data = library.read_transcript(meeting)
    data["segments"][0]["text"] = "Правили руками."
    library.write_transcript(meeting, data)
    with pytest.raises(speakers.Stale):
        speakers.undo(meeting, base)


def test_refusals_write_nothing(meeting, base):
    before = (meeting / "transcript.json").read_bytes()
    with pytest.raises(speakers.SpeakerError, match="нет"):
        textfix.apply(meeting, "Docker", "Докер", "all", base)
    with pytest.raises(speakers.SpeakerError):
        textfix.apply(meeting, "кубер нетис", "  ", "all", base)
    with pytest.raises(speakers.SpeakerError):
        textfix.apply(meeting, "кубер нетис", "Kubernetes", "кое-где", base)
    with pytest.raises(speakers.SpeakerError, match="уже"):
        textfix.apply(meeting, "кубер нетис", "кубер нетис", "all", base)
    with pytest.raises(speakers.Stale):
        textfix.apply(meeting, "кубер нетис", "Kubernetes", "one", base, segment=0, offset=3)
    with pytest.raises(speakers.Stale):
        textfix.apply(meeting, "кубер нетис", "Kubernetes", "all", base, count=4)
    with pytest.raises(speakers.Stale):
        textfix.apply(meeting, "кубер нетис", "Kubernetes", "one", base, segment=3, offset=9)
    assert (meeting / "transcript.json").read_bytes() == before


def test_case_only_fix_of_one_occurrence_is_allowed(meeting, base):
    got = textfix.apply(meeting, "Кубер нетис", "кубер нетис", "one", base, segment=1, offset=0)
    assert _segs(meeting)[1]["text"] == "кубер нетис уже ЁЛКА."
    assert got["changed"] == 1


def test_sample_context_is_cut_at_word_boundaries(meeting):
    long = "Сначала очень длинное вступление про планы, сроки и договорённости, а потом кубер нетис " \
           "и ещё одна длинная фраза после совпадения про стенд и деплой."
    data = library.read_transcript(meeting)
    data["segments"][2]["text"] = long
    library.write_transcript(meeting, data)
    sample = textfix.preview(meeting, "кубер нетис")["samples"][2]
    assert sample["before"].startswith("…") and sample["before"].endswith("потом ")
    assert sample["after"].endswith("…") and sample["after"].startswith(" и ещё")
    assert " " not in sample["before"][1:2]  # без обрывка слова


# --- правила для новых расшифровок ---------------------------------------------


def test_rules_fix_pipeline_segments_and_their_words():
    from meet.asr import Segment, Word

    segs = [Segment(0.0, 2.0, "Кубер нетис упал, кубер нетис встал.",
                    words=[Word(0.0, 0.4, " Кубер"), Word(0.4, 0.8, " нетис"), Word(0.8, 1.0, " упал,"),
                           Word(1.0, 1.3, " кубер"), Word(1.3, 1.6, " нетис"), Word(1.6, 2.0, " встал.")]),
            Segment(2.0, 3.0, "Без слов: кубернетис.")]
    rules = [{"from": "кубер нетис", "to": "kubernetes"}, {"from": "кубернетис", "to": "Kubernetes"}]
    assert textfix.apply_rules(segs, rules) == 3
    assert segs[0].text == "Kubernetes упал, kubernetes встал."
    assert [(w.start, w.end, w.text) for w in segs[0].words[:2]] == [(0.0, 0.8, " Kubernetes"), (0.8, 1.0, " упал,")]
    assert "".join(w.text for w in segs[0].words).strip() == segs[0].text
    assert segs[1].text == "Без слов: Kubernetes."
    assert textfix.apply_rules(segs, []) == 0


def test_clean_rules_drops_junk_and_repeats():
    got = textfix.clean_rules([{"from": " кубер нетис ", "to": "Kubernetes"}, {"from": "", "to": "x"},
                               {"from": "Кубер  Нетис", "to": "K8s"}, "мусор", {"from": "a", "to": ""},
                               {"from": "...", "to": "x"}, {"from": "ёлка", "to": "Ёлка"}])
    assert got == [{"from": "Кубер Нетис", "to": "K8s"}, {"from": "ёлка", "to": "Ёлка"}]
    assert textfix.clean_rules(None) == []
