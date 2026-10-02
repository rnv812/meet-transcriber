"""Поводы внеочередной подсказки: вопрос на встрече и обращение к владельцу.
Реплики выдуманы."""

import pytest

from meet.assist.triggers import ADDRESSED, QUESTION, owner_names, trigger_of


def _t(text, speaker="Ольга", owner="Кузьма"):
    return trigger_of({"speaker": speaker, "text": text}, owner_speaker=owner)


@pytest.mark.parametrize("text", [
    "Кто возьмёт интеграцию с банком?",
    "А релиз точно в пятницу?",
    "Так и сделаем, да?»",
])
def test_question_mark_from_someone_else(text):
    assert _t(text) == QUESTION


@pytest.mark.parametrize("text", [
    "Кузьма, посмотри, пожалуйста, отчёт",
    "Это вопрос к Кузьме",
    "Скажу Кузьме после встречи",
    "никита сможет взять задачу",
])
def test_owner_called_by_name_in_any_case(text):
    assert _t(text) == ADDRESSED


@pytest.mark.parametrize("text", [
    "Вы сможете прислать оценку к среде",
    "А ты когда будешь готов",
    "Как вам такой вариант",
])
def test_second_person_with_a_question_word(text):
    assert _t(text) == ADDRESSED


@pytest.mark.parametrize("text", [
    "Мы договорились перенести релиз.",
    "Вы молодцы, всё сделали.",              # «вы» без вопроса
    "Как и договаривались, переносим.",     # вопросительное слово без «вы»
    "Никитин отчёт уже у меня",            # фамилия-прилагательное: всё равно обращение — ок
])
def test_plain_statements(text):
    expected = ADDRESSED if text.startswith("Никитин") else None
    assert _t(text) == expected


def test_owner_lines_never_trigger():
    assert _t("Кто возьмёт интеграцию?", speaker="Кузьма") is None


def test_default_label_you_is_not_a_name():
    assert owner_names("Вы") == []
    assert _t("Вывод: всё по плану.", owner="Вы") is None
    assert _t("Вы сможете?", owner="Вы") == ADDRESSED


def test_short_names_match_only_whole_words():
    assert _t("Ян, ты готов?", owner="Ян") == ADDRESSED
    assert _t("Январь был тяжёлым.", owner="Ян") is None
