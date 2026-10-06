"""JSON-объект из ответа модели (meet.llm.jsonreply): как отвечают локальные
модели — в ```json, с текстом вокруг, с рассуждением <think>, обрезанным
ответом, лишними запятыми, обёрткой {"analysis": {...}}."""

import pytest

from meet.llm.jsonreply import extract_object

KEYS = ("chapters", "importance")


def test_plain_object():
    assert extract_object('{"chapters": []}', KEYS) == {"chapters": []}


def test_fenced_with_prose_around():
    text = 'Вот разметка встречи:\n```json\n{"chapters": [], "importance": {"1": 0.9}}\n```\nНадеюсь, помог!'
    assert extract_object(text, KEYS) == {"chapters": [], "importance": {"1": 0.9}}


def test_prose_with_braces_after_the_object():
    # «до последней }» склеило бы объект с текстом после него.
    text = 'Ответ: {"chapters": [{"start_i": 0, "end_i": 3, "title": "А"}]} — формат {номер: тип} соблюдён.'
    assert extract_object(text, KEYS) == {"chapters": [{"start_i": 0, "end_i": 3, "title": "А"}]}


def test_think_block_with_braces_is_skipped():
    text = ('<think>Нужно вернуть {"chapters": ...}, начну с {глав}.</think>\n'
            '{"chapters": [], "importance": {}}')
    assert extract_object(text, KEYS) == {"chapters": [], "importance": {}}


def test_reasoning_without_opening_tag():
    # Шаблон чата Qwen3/DeepSeek-R1 открывает <think> сам — в ответе только закрывающий.
    text = 'рассуждаю про {"x": 1}...\n</think>\n\n{"chapters": []}'
    assert extract_object(text, KEYS) == {"chapters": []}


def test_example_object_before_the_answer_is_not_taken():
    text = 'Схема: {"x": 1}. Ответ:\n{"importance": {"2": 0.5}}'
    assert extract_object(text, KEYS) == {"importance": {"2": 0.5}}


def test_trailing_commas():
    text = '{"chapters": [{"start_i": 0, "end_i": 2, "title": "А",},], "importance": {"1": 0.5,},}'
    assert extract_object(text, KEYS) == {
        "chapters": [{"start_i": 0, "end_i": 2, "title": "А"}], "importance": {"1": 0.5}}


def test_wrapped_in_an_outer_key():
    text = '{"analysis": {"chapters": [], "importance": {"3": 0.7}}}'
    assert extract_object(text, KEYS) == {"chapters": [], "importance": {"3": 0.7}}


def test_truncated_reply_keeps_the_complete_parts():
    # Ответ оборвался (лимит токенов): главы целы, важность — нет.
    text = ('{"chapters": [{"start_i": 0, "end_i": 4, "title": "Начало"}, '
            '{"start_i": 5, "end_i": 9, "title": "Ко'
            )
    info = {}
    got = extract_object(text, KEYS, info)
    assert info["truncated"] is True
    # Оборванная глава закрыта без названия — её отбросит проверка глав.
    assert got == {"chapters": [{"start_i": 0, "end_i": 4, "title": "Начало"}, {"start_i": 5, "end_i": 9}]}


def test_truncated_inside_the_second_field():
    text = '```json\n{"importance": {"1": 0.9, "2": 0.4}, "chapters": [{"start_i": 0, "end_i'
    got = extract_object(text, KEYS)
    assert got["importance"] == {"1": 0.9, "2": 0.4}


@pytest.mark.parametrize("text", ["Извините, не могу.", "", "[1, 2, 3]", "{не json}"])
def test_no_object_is_an_error(text):
    with pytest.raises(ValueError):
        extract_object(text, KEYS)


def test_any_object_when_no_keys_expected():
    assert extract_object('ok {"a": 1}') == {"a": 1}


def test_inner_piece_of_a_broken_object_is_not_the_answer():
    # Объект целый по скобкам, но с комментарием — не JSON; его глава с "title"
    # не должна стать ответом (название встречи = название главы).
    text = '{"chapters": [{"start_i": 0, "title": "План"}] // главы\n}\nИ ещё: {"importance": {"1": 0.5}}'
    assert extract_object(text, ("chapters", "importance", "title")) == {"importance": {"1": 0.5}}
