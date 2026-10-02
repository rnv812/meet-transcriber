"""Латинские термины после GigaAM: кириллическая запись → термин из списка."""

from meet import translit
from meet.asr import Segment, Word


def _seg(text: str) -> Segment:
    words, t = [], 0.0
    for w in text.split(" "):
        words.append(Word(t, t + 0.3, " " + w))
        t += 0.4
    return Segment(0.0, t, text, words=words)


def test_variants_cover_reading_english_and_spelled_forms():
    assert translit.variants("API") == ["апи", "эй пи ай"]
    assert "джира" in translit.variants("Jira")
    assert translit.variants("Kafka") == ["кафка"]
    assert translit.variants("Docker") == ["докер"]


def test_only_latin_terms_without_digits():
    assert translit.variants("Кафка") == []
    assert translit.variants("S3") == []
    assert translit.variants("") == []


def test_ordinary_russian_words_and_short_forms_are_never_produced():
    # «Kot» → «кот», «Port» → «порт», «Dom» → «дом»: обычные слова не трогаем
    for term in ("Kot", "Port", "Dom", "Mir", "Test"):
        assert translit.variants(term) == []
    # меньше трёх букв — слишком рискованно («пр» из PR), остаётся только «пи ар»
    assert translit.variants("PR") == ["пи ар"]


def test_rules_map_back_to_the_term_as_written():
    rules = translit.rules_for(["API", "Jira", "кафка", "S3"])
    assert {"from": "апи", "to": "API"} in rules
    assert {"from": "джира", "to": "Jira"} in rules
    assert all(r["to"] in ("API", "Jira") for r in rules)


def test_apply_replaces_whole_words_and_keeps_case():
    segs = [_seg("Открой апи и джира сразу."), _seg("Джира упала, а апишка жива.")]
    n = translit.apply(segs, ["API", "jira"])
    assert n == 3
    assert segs[0].text == "Открой API и jira сразу."
    # термин со строчной — в регистре исправляемого (начало предложения)
    assert segs[1].text == "Jira упала, а апишка жива."  # «апишка» — не целое слово
    # слова следуют за текстом: на них держится разбиение по спикерам
    for s in segs:
        assert "".join(w.text for w in s.words).strip() == s.text


def test_apply_spelled_acronym_phrase():
    segs = [_seg("Через эй пи ай шлюза.")]
    assert translit.apply(segs, ["API"]) == 1
    assert segs[0].text == "Через API шлюза."


def test_apply_leaves_ordinary_speech_alone():
    segs = [_seg("Кот спит у порта, дом рядом.")]
    assert translit.apply(segs, ["Kot", "Port", "Dom"]) == 0
    assert segs[0].text == "Кот спит у порта, дом рядом."


def test_apply_without_terms_is_noop():
    segs = [_seg("Просто текст.")]
    assert translit.apply(segs, []) == 0
    assert translit.apply(segs, ["Кириллица"]) == 0
