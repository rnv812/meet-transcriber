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


def test_unclosed_think_is_no_answer():
    # Ответ оборвался в рассуждении: черновик из <think> — не ответ.
    text = '<think>Надо вернуть {"chapters": [{"start_i": 0, "title": "Черновик"}], "importance": {}} и потом'
    with pytest.raises(ValueError, match="не закончила рассуждение"):
        extract_object(text, KEYS)


def test_answer_before_an_unclosed_think_is_kept():
    text = '{"chapters": []}\n<think>а ещё можно {"importance": {"1": 1}}'
    assert extract_object(text, KEYS) == {"chapters": []}


def test_uppercase_close_tag_with_wide_lowercase_letters():
    # «İ» в нижнем регистре длиннее — срез по индексам из lower() съехал бы.
    text = "İİİİ рассуждение </THINK>{\"chapters\": []}"
    assert extract_object(text, KEYS) == {"chapters": []}


@pytest.mark.parametrize("text", [
    '{"title": "A", ' * 4000,
    "{" * 60000,
    '{"chapters": [{"title": "A", "start_i": 1, ' * 1400,
    '{"a":' * 3000,
], ids=["title-loop", "braces", "chapters-loop", "deep-nesting"])
def test_degenerate_repetition_is_parsed_fast(text):
    # Маленькая модель зациклилась до лимита: разбор не должен идти минутами.
    import time

    started = time.perf_counter()
    try:
        extract_object(text, KEYS + ("title",))
    except ValueError:
        pass
    assert time.perf_counter() - started < 2.0



def test_strip_reasoning_for_plain_text_replies():
    from meet.llm.jsonreply import strip_reasoning

    assert strip_reasoning("<think>думаю {…}</think>\nВыпуск экспорта") == ("Выпуск экспорта", False)
    assert strip_reasoning("размышление</think> Итоги") == ("Итоги", False)
    assert strip_reasoning("Итоги встречи\n<think>а ещё") == ("Итоги встречи", True)
    assert strip_reasoning("<think>не успела") == ("", True)


def test_depth_cap_keeps_intact_outer_part():
    # Глубже 64 уровней проход кончается как обрыв: берутся целые части, без
    # зависания и без квадратичного прохода.
    import time

    text = '{"title": "ok", "x": ' + '{"a": ' * 70 + '1,' + '}' * 70 + '}'
    started = time.perf_counter()
    assert extract_object(text, ("title",)) == {"title": "ok"}
    runaway = '{"title": "A", ' * 20_000
    try:
        extract_object(runaway, ("title",))
    except ValueError:
        pass
    assert time.perf_counter() - started < 10.0
