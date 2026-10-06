"""Поиск по тексту встреч: правила (общие с окном), реплики, фрагменты и поиск
по всей библиотеке. Текст выдуманный."""

import json
from pathlib import Path

import pytest

from meet import library, search

CASES = json.loads((Path(__file__).parent / "fixtures" / "search_cases.json")
                   .read_text(encoding="utf-8"))


def _marked(text: str, ranges) -> str:
    out, at = [], 0
    for start, end in ranges:
        out += [text[at:start], "[", text[start:end], "]"]
        at = end
    return "".join(out) + text[at:]


# --- общие с app/src/lib/search.ts случаи ------------------------------------------


@pytest.mark.parametrize("word,expected", CASES["stems"])
def test_shared_stems(word, expected):
    assert search.stem(word) == expected


@pytest.mark.parametrize("case", CASES["queries"], ids=lambda c: c["query"])
def test_shared_query_parsing(case):
    q = search.parse_query(case["query"])
    assert (q.phrases, q.keywords, q.speakers) == (
        case["phrases"], case["keywords"], case["speakers"])


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
def test_shared_cases(case):
    found = search.match_text(case["text"], search.parse_query(case["query"]),
                              case.get("speaker") or "")
    text = search.nfc(case["text"])  # подсветка — по тексту в NFC
    assert (None if found is None else _marked(text, found)) == case["marked"]


@pytest.mark.parametrize("case", CASES["turns"])
def test_shared_turns(case):
    assert [[t.start, t.speaker, t.text] for t in search.turns_of(case["segments"])] == case["turns"]


# --- реплики как в карточке ---------------------------------------------------------


def test_turns_are_merged_like_the_card():
    segments = [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00", "text": "Начнём."},
        {"start": 2.5, "end": 4.0, "speaker": "SPEAKER_00", "text": "Бюджет готов."},
        {"start": 9.0, "end": 10.0, "speaker": "SPEAKER_00", "text": "Ещё вопрос."},
        {"start": 10.5, "end": 11.0, "speaker": None, "text": "Да."},
    ]
    turns = search.turns_of(library.with_display_names({"segments": segments})["segments"])
    assert [(t.start, t.speaker, t.text) for t in turns] == [
        (0.0, "Спикер 1", "Начнём. Бюджет готов."),
        (9.0, "Спикер 1", "Ещё вопрос."),
        (10.5, "Неизвестный", "Да."),
    ]


# --- фрагменты ----------------------------------------------------------------------


def test_snippet_of_short_text_is_whole():
    text = "Бюджет утвердили."
    assert search.snippet(text, [[0, 6]]) == (text, [[0, 6]])


def test_snippet_is_cut_at_words_around_the_first_match():
    text = " ".join(["слово"] * 40) + " бюджет " + " ".join(["хвост"] * 60)
    start = text.index("бюджет")
    snip, ranges = search.snippet(text, [[start, start + 6]])
    assert snip.startswith("…слово") and snip.endswith("хвост…")
    assert len(snip) <= search.SNIPPET_LEN + 2
    (a, b), = ranges
    assert snip[a:b] == "бюджет"


def test_snippet_ranges_are_utf16_for_the_window():
    text = "🙂 Итак: бюджет готов."
    start = text.index("бюджет")
    snip, ranges = search.snippet(text, [[start, start + 6]])
    assert snip == text
    # 🙂 — две единицы UTF-16: окно считает так же, как строки в JS.
    assert ranges == [[start + 1, start + 7]]


# --- вся библиотека -------------------------------------------------------------------


def _rec(root: Path, rid: str, segments, title=None):
    folder = root / rid
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    if title:
        library.write_meta(folder, {"title": title})
    if segments is not None:
        library.write_transcript(folder, {"version": 1, "segments": segments})
    return folder


def _seg(start, speaker, text):
    return {"start": start, "end": start + 1.0, "speaker": speaker, "text": text, "uncertain": False}


@pytest.fixture
def lib(tmp_path):
    search.clear_cache()
    root = tmp_path / "recordings"
    _rec(root, "2026-09-28_10-00", [
        _seg(0, "Анна", "Обсудили задачи спринта."),
        _seg(30, "Борис", "Бюджет утвердим завтра."),
        _seg(60, "Анна", "По бюджету вопросов нет."),
    ], title="Планёрка")
    _rec(root, "2026-09-29_10-00", [_seg(0, "Анна", "Про отпуск.")], title="Бюджет на квартал")
    _rec(root, "2026-09-30_10-00", [
        _seg(i * 10.0, "Борис", f"Бюджет, пункт {i}.") for i in range(5)])
    _rec(root, "2026-09-30_12-00", None, title="Без расшифровки")
    return root


def test_library_search_finds_text_and_titles_newest_first(lib):
    found = search.search_library(lib, "бюджет")
    assert [f["id"] for f in found] == [
        "2026-09-30_10-00", "2026-09-29_10-00", "2026-09-28_10-00"]
    third = found[0]
    assert third["total"] == 5 and len(third["hits"]) == search.MAX_HITS
    assert third["date"] == "2026-09-30"
    by_title = found[1]
    assert by_title["title_match"] is True and by_title["hits"] == [] and by_title["total"] == 0
    first = found[2]
    assert first["title"] == "Планёрка" and first["total"] == 2
    hit = first["hits"][0]
    assert hit == {"t": 30.0, "speaker": "Борис", "snippet": "Бюджет утвердим завтра.",
                   "ranges": [[0, 6]]}
    # Карточка записи целиком: список рисует её как обычный элемент.
    assert first["has_transcript"] is True and first["path"].endswith("2026-09-28_10-00")


def test_library_search_speaker_filter_and_empty_query(lib):
    found = search.search_library(lib, "бюджет спикер:Анна")
    assert [f["id"] for f in found] == ["2026-09-28_10-00"]
    assert found[0]["hits"][0]["t"] == 60.0
    assert search.search_library(lib, "  ") == []
    assert search.search_library(lib, "отсутствует") == []


def test_library_search_limit(lib):
    assert len(search.search_library(lib, "бюджет", limit=1)) == 1


def test_transcripts_are_cached_until_they_change(lib, monkeypatch):
    # Карточку (describe) список читает и так; кэш избавляет от разбора реплик.
    calls = []
    real = search.turns_of
    monkeypatch.setattr(search, "turns_of", lambda segs: calls.append(1) or real(segs))
    search.search_library(lib, "бюджет")
    assert len(calls) == 3  # три расшифрованные записи
    search.search_library(lib, "задачи")
    assert len(calls) == 3
    folder = lib / "2026-09-28_10-00"
    library.write_transcript(folder, {"version": 1, "segments": [_seg(0, "Анна", "Новый текст про бюджет.")]})
    found = search.search_library(lib, "новый")
    assert len(calls) == 4
    assert [f["id"] for f in found] == ["2026-09-28_10-00"]


def test_forget_drops_one_recording_from_the_cache(lib, monkeypatch):
    calls = []
    real = search.turns_of
    monkeypatch.setattr(search, "turns_of", lambda segs: calls.append(1) or real(segs))
    search.search_library(lib, "бюджет")
    assert len(calls) == 3
    # Путь может прийти записанным иначе, чем в кэше (регистр, resolve).
    search.forget(Path(str(lib / "2026-09-28_10-00").upper()))
    search.search_library(lib, "бюджет")
    assert len(calls) == 4


def test_cache_is_bounded_by_text_size(tmp_path):
    a = _rec(tmp_path, "a", [_seg(0, "Анна", "Двадцать символов тут.")])
    b = _rec(tmp_path, "b", [_seg(0, "Анна", "И ещё двадцать букв.")])
    one = search._Cache().doc(b)[1].size
    cache = search._Cache(limit=int(one * 1.5))  # помещается одна такая встреча
    cache.doc(a)
    _, doc = cache.doc(b)
    assert list(cache._items) == [str(b)]
    # исходный регистр и «ё» восстанавливаются из нормализованного текста
    assert (doc.starts[0], doc.speakers[0], doc.text(0)) == (0.0, "Анна", "И ещё двадцать букв.")


def test_size_estimate_counts_wide_characters():
    narrow = search._Doc([search.Turn(0.0, "А", "бюджет" * 10, "бюджет" * 10)])
    wide = search._Doc([search.Turn(0.0, "А", "😀" * 60, "😀" * 60)])
    assert wide.size > narrow.size + 60  # эмодзи — 4 байта на символ, не 2


def test_cards_are_cached_until_folder_or_meta_changes(lib, monkeypatch):
    calls = []
    real = library.describe
    monkeypatch.setattr(library, "describe", lambda f: calls.append(f.name) or real(f))
    search.search_library(lib, "бюджет")
    first = len(calls)
    assert first == 4  # все папки библиотеки
    search.search_library(lib, "задачи")
    assert len(calls) == first
    library.write_meta(lib / "2026-09-28_10-00", {"title": "Бюджетная планёрка"})
    found = search.search_library(lib, "бюджетная")
    assert calls[first:] == ["2026-09-28_10-00"]
    assert found[0]["title"] == "Бюджетная планёрка" and found[0]["title_match"] is True


def test_tokens_are_parsed_lazily_and_once_per_turn(lib, monkeypatch):
    seen = []
    real = search.tokenize
    monkeypatch.setattr(search, "tokenize", lambda text: seen.append(text) or real(text))
    assert search.search_library(lib, "задачи спринта")[0]["total"] == 1
    # разбираются только реплики, попавшие во фрагменты, и названия
    assert seen.count("Обсудили задачи спринта.") == 1
    assert "Бюджет утвердим завтра." not in seen
    search.search_library(lib, "задачи спринта")
    assert seen.count("Обсудили задачи спринта.") == 1  # второй раз — из кэша


def test_one_letter_query_finds_nothing(lib):
    assert search.search_library(lib, " б ") == []
    assert search.search_library(lib, "бю") != []


def test_break_mark_is_its_own_empty_turn():
    from meet import search

    turns = search.turns_of([
        {"start": 0, "end": 1, "speaker": None, "text": "раз"},
        {"start": 1, "end": 1, "speaker": None, "text": "— перерыв 5 мин —", "kind": "break"},
        {"start": 1.5, "end": 2, "speaker": None, "text": "два"},
    ])
    assert [t.text for t in turns] == ["раз", "", "два"]


def test_title_ranges_are_utf16_and_partial():
    q = search.parse_query('бюджета "план работ"')
    # эмодзи — две единицы UTF-16: подсветка сдвигается, как считает окно
    assert search.title_ranges("😀 Бюджет и план работ", q) == [[3, 9], [12, 22]]
    # название запросу целиком не отвечает (нет фразы) — слово всё равно подсвечено
    assert search.title_ranges("Бюджет отдела", q) == [[0, 6]]
    assert search.title_ranges("Отпуск", q) == []
    assert search.title_ranges("Бюджет", search.parse_query("спикер:Анна")) == []


# --- быстрый поиск по нормализованному тексту = правила окна ---------------------------


def _doc_found(texts, query, speaker="", others=None):
    """Встреча из реплик `texts` (спикер `speaker`, кроме номеров из `others`
    — у них «Посторонний») и что в ней находит быстрый поиск."""
    turns = [search.Turn(float(i), "Посторонний" if others and i in others
                         else search.nfc(speaker) or search.NO_SPEAKER,
                         search.nfc(t), search.norm_word(search.nfc(t)))
             for i, t in enumerate(texts)]
    doc = search._Doc(turns)
    return search._search_doc(("тест", doc.serial), doc, search._Plan(search.parse_query(query)))


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
def test_fast_path_agrees_with_shared_cases(case):
    if not search.searchable(case["query"]):
        assert search.parse_query(case["query"]).empty or len(case["query"].strip()) < search.MIN_QUERY
        return  # такой запрос до встреч не доходит
    # Реплика — среди соседних (другого спикера): перевод строки между ними не склеивает слова.
    total, hits = _doc_found(["Вступление.", case["text"], "Заключение."], case["query"],
                             case.get("speaker") or "", others={0, 2})
    want = search.match_text(case["text"], search.parse_query(case["query"]), case.get("speaker") or "")
    assert total == (0 if want is None else 1)
    if want is not None:
        text, marks = search.snippet(search.nfc(case["text"]), want)
        assert hits == [{"t": 1.0, "speaker": search.nfc(case.get("speaker") or "") or search.NO_SPEAKER,
                         "snippet": text, "ranges": marks}]


def test_fast_path_case_yo_rare_scripts_and_turn_borders():
    texts = ["ПЛАН", "работ нет", "Ёлка и ЁЖ", "İstanbul büyük", "Ⓐbc план работ"]
    # фраза не склеивается через границу реплик
    assert _doc_found(texts, '"план работ"')[0] == 1
    total, hits = _doc_found(texts, "ежик ёлк")
    assert total == 0
    total, hits = _doc_found(texts, "елка еж")
    assert total == 1 and hits[0]["snippet"] == "Ёлка и ЁЖ"
    for query in ("istanbul", "büy", "stanbul"):  # нормализация меняет длину — проверка по словам
        want = search.match_text(texts[3], search.parse_query(query))
        total, hits = _doc_found(texts, query)
        assert total == (want is not None)
        assert hits == ([] if want is None else [{"t": 3.0, "speaker": search.NO_SPEAKER,
                                                  "snippet": texts[3], "ranges": search.snippet(texts[3], want)[1]}])
    assert _doc_found(texts, "план")[1][0]["snippet"] == "ПЛАН"
    assert _doc_found(texts, "работ")[1][-1]["snippet"] == "Ⓐbc план работ"


def test_fast_path_counts_every_turn_but_keeps_three_hits():
    total, hits = _doc_found([f"Бюджет {i}." for i in range(10)] + ["Бюджетный."], "бюджет")
    assert total == 11 and [h["t"] for h in hits] == [0.0, 1.0, 2.0]
    total, hits = _doc_found(["а", "б"], "спикер:Анна", speaker="Анна")
    assert total == 2 and hits[0]["ranges"] == []
