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


def test_cache_is_bounded_by_text_size(tmp_path):
    cache = search._Cache(limit=500)  # одна короткая реплика — около 400 байт по оценке
    a = _rec(tmp_path, "a", [_seg(0, "Анна", "Двадцать символов тут.")])
    b = _rec(tmp_path, "b", [_seg(0, "Анна", "И ещё двадцать букв.")])
    cache.turns(a)
    cache.turns(b)
    assert list(cache._items) == [str(b)]


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


def test_tokens_are_parsed_once_per_turn(lib, monkeypatch):
    search.search_library(lib, "бюджет")
    seen = []
    real = search.tokenize
    monkeypatch.setattr(search, "tokenize", lambda text: seen.append(text) or real(text))
    assert search.search_library(lib, "задачи спринта")[0]["total"] == 1
    assert "Обсудили задачи спринта." not in seen  # реплики — из кэша, разбираются только названия


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
