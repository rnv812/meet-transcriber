"""Живое состояние встречи: сводка и подсказки, правка патчами модели.

Все реплики и тексты в тестах выдуманы."""

import json

import pytest

from meet.assist.live_state import (
    LIVE_STATE_JSON,
    LiveState,
    PatchError,
    load_saved,
    parse_clock,
    parse_reply,
    similar,
)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        self.t += 1.0
        return self.t


def _state(**kw):
    kw.setdefault("clock", Clock())
    return LiveState(**kw)


POINTS = [
    "Партнёр готов к тестам", "Бюджет утверждён до марта", "Стенд поднимут к пятнице",
    "Дизайн экрана оплаты согласован", "Миграцию базы откладываем", "Юристы смотрят договор",
    "Нужен второй разработчик", "Отчёт для банка пишет Ольга", "Релиз мобильного клиента в мае",
]


def _hint(text, kind="question", t="00:01:05", **extra):
    return {"op": "add", "section": "hints", "kind": kind, "text": text,
            "why": "почему важно", "t": t, **extra}


# --- разбор ответа модели -----------------------------------------------------


def test_parse_reply_takes_plain_json_and_fenced_json():
    assert parse_reply('{"topic": null, "ops": []}') == {"topic": None, "ops": []}
    fenced = 'Вот:\n```json\n{"topic": "Запуск", "ops": []}\n```'
    assert parse_reply(fenced)["topic"] == "Запуск"


@pytest.mark.parametrize("reply", ["", "всё хорошо", "[1, 2]", '{"ops": [', "{'a': 1}"])
def test_parse_reply_rejects_non_objects(reply):
    with pytest.raises(PatchError):
        parse_reply(reply)


def test_parse_clock_accepts_hms_ms_and_numbers():
    assert parse_clock("00:01:05") == 65.0
    assert parse_clock("12:30") == 750.0
    assert parse_clock(42) == 42.0
    assert parse_clock("[00:00:07]") == 7.0
    assert parse_clock("скоро") is None
    assert parse_clock(None) is None
    assert parse_clock(True) is None


# --- схема: всё или ничего ----------------------------------------------------


@pytest.mark.parametrize("patch", [
    {"ops": "нет"},
    {"topic": 5, "ops": []},
    {"ops": [{"op": "move", "section": "points", "text": "x"}]},
    {"ops": [{"op": "add", "section": "чепуха", "text": "x"}]},
    {"ops": [{"op": "add", "section": "points"}]},
    {"ops": [{"op": "add", "section": "points", "text": "   "}]},
    {"ops": [{"op": "add", "section": "tasks", "who": "Ольга"}]},
    {"ops": [{"op": "update", "id": "p99", "text": "x"}]},
    {"ops": [{"op": "remove", "id": "h7"}]},
    {"ops": [_hint("Уточнить срок", kind="шутка")]},
    {"ops": [_hint("Уточнить срок", t="когда-нибудь")]},
    {"ops": [{"op": "add", "section": "hints", "kind": "risk", "text": "Без срока"}]},
    {"ops": ["ADD points :: x"]},
])
def test_invalid_patch_is_rejected_whole_and_state_untouched(patch):
    s = _state()
    s.apply({"ops": [{"op": "add", "section": "points", "text": "Обсудили запуск"}]})
    before = json.dumps(s.to_dict(), ensure_ascii=False)
    good = {"op": "add", "section": "decisions", "text": "Запуск в среду"}
    bad = dict(patch)
    if isinstance(bad.get("ops"), list):
        bad["ops"] = [good, *bad["ops"]]  # хорошая операция рядом не применяется
    with pytest.raises(PatchError):
        s.apply(bad)
    assert json.dumps(s.to_dict(), ensure_ascii=False) == before


def test_too_many_ops_is_an_error():
    ops = [{"op": "add", "section": "points", "text": f"Тезис {i}"} for i in range(40)]
    with pytest.raises(PatchError):
        _state().apply({"ops": ops})


def test_extra_keys_are_ignored_not_fatal():
    s = _state()
    assert s.apply({"topic": "Запуск", "ops": [
        {"op": "add", "section": "points", "text": "Тезис", "confidence": 0.9}],
        "comment": "лишнее"})
    assert s.to_dict()["summary"]["points"][0]["text"] == "Тезис"


# --- сводка ---------------------------------------------------------------------


def test_add_update_remove_by_stable_ids():
    s = _state()
    assert s.apply({"topic": "Интеграция платежей", "ops": [
        {"op": "add", "section": "points", "text": "Партнёр готов к тестам"},
        {"op": "add", "section": "decisions", "text": "Тестовый стенд к пятнице"},
        {"op": "add", "section": "tasks", "who": "Ольга", "what": "Выдать доступы", "due": "пятница"},
        {"op": "add", "section": "open_questions", "text": "Кто подписывает акт?"},
    ]})
    d = s.to_dict()["summary"]
    assert d["topic"] == "Интеграция платежей"
    assert [p["id"] for p in d["points"]] == ["p1"]
    assert d["decisions"][0]["id"] == "d1"
    assert d["tasks"][0] == {"id": "t1", "who": "Ольга", "what": "Выдать доступы", "due": "пятница"}
    assert d["open_questions"][0]["id"] == "q1"
    v = s.version
    assert s.apply({"topic": None, "ops": [
        {"op": "update", "id": "p1", "text": "Партнёр готов к тестам с понедельника"},
        {"op": "update", "id": "t1", "due": "четверг"},
        {"op": "remove", "id": "q1"},
        {"op": "add", "section": "points", "text": "Нужен второй стенд"},
    ]})
    d = s.to_dict()["summary"]
    assert d["topic"] == "Интеграция платежей"  # null — без изменений
    assert d["points"][0]["text"].endswith("с понедельника")
    assert [p["id"] for p in d["points"]] == ["p1", "p2"]  # новое — в конец
    assert d["tasks"][0]["due"] == "четверг" and d["tasks"][0]["who"] == "Ольга"
    assert d["open_questions"] == []
    assert s.version == v + 1


def test_empty_patch_changes_nothing():
    s = _state()
    assert s.apply({"topic": None, "ops": []}) is False
    assert s.version == 0


def test_points_capped_at_seven_oldest_drop():
    s = _state()
    for text in POINTS:
        s.apply({"ops": [{"op": "add", "section": "points", "text": text}]})
    points = s.to_dict()["summary"]["points"]
    assert len(points) == 7 and points[0]["id"] == "p3" and points[-1]["id"] == "p9"


def test_near_duplicate_summary_item_is_not_added_twice():
    s = _state()
    s.apply({"ops": [{"op": "add", "section": "decisions", "text": "Запуск переносим на среду"}]})
    s.apply({"ops": [{"op": "add", "section": "decisions", "text": "Запуск переносим на среду."}]})
    assert len(s.to_dict()["summary"]["decisions"]) == 1


def test_long_texts_are_trimmed_and_flattened():
    s = _state()
    s.apply({"topic": "Т" * 500, "ops": [_hint("Вопрос " + "очень " * 100, why="строка\nвторая")]})
    d = s.to_dict()
    assert len(d["summary"]["topic"]) <= 120
    h = d["hints"][0]
    assert len(h["text"]) <= 200 and "\n" not in h["why"]


# --- подсказки ------------------------------------------------------------------


def test_hint_fields_and_source_time():
    s = _state()
    s.apply({"ops": [_hint("Спросить, кто отвечает за акт", kind="risk", t="00:12:30")]})
    h = s.to_dict()["hints"][0]
    assert h["id"] == "h1" and h["kind"] == "risk" and h["source_t"] == 750.0
    assert h["why"] == "почему важно" and h["pinned"] is False and h["dismissed"] is False
    assert h["created_at"] == h["updated_at"]


def test_similar_hint_is_deduplicated():
    s = _state()
    s.apply({"ops": [_hint("Уточнить, кто подписывает акт приёмки")]})
    assert s.apply({"ops": [_hint("Уточните, кто подписывает акт приёмки?")]}) is False
    s.apply({"ops": [_hint("Спросить про бюджет на второй этап")]})
    assert [h["id"] for h in s.to_dict()["hints"]] == ["h1", "h2"]


def test_similar_is_symmetric_and_ignores_case_and_punctuation():
    assert similar("Кто отвечает за релиз?", "кто отвечает за релиз")
    assert similar("Ёлка в среду", "елка в среду")
    assert not similar("Кто отвечает за релиз?", "Какой бюджет у проекта?")


def test_dismissed_hint_never_comes_back():
    s = _state()
    s.apply({"ops": [_hint("Спросить про сроки тестов")]})
    assert s.dismiss("h1") is True
    assert s.to_dict()["hints"] == []
    # Модель снова предлагает то же самое и правит скрытую по id — тишина.
    assert s.apply({"ops": [_hint("Спросить про сроки тестов!")]}) is False
    assert s.apply({"ops": [{"op": "update", "id": "h1", "text": "Новый текст"},
                            {"op": "remove", "id": "h1"}]}) is False
    assert s.to_dict()["hints"] == []
    assert s.dismiss("h1") is False and s.dismiss("h404") is False


def test_restore_right_after_dismiss_brings_the_hint_back():
    """«Вернуть» в панели: скрытие ушло сразу (текст запомнен — «не
    предлагать снова»), а возврат в течение RESTORE_S снимает и его."""
    s = _state()
    s.apply({"ops": [_hint("Спросить про сроки тестов")]})
    s.dismiss("h1")
    assert s.apply({"ops": [_hint("Спросить про сроки тестов!")]}) is False  # скрыта — не повторять
    v = s.version
    assert s.restore("h1") is True
    assert [h["text"] for h in s.to_dict()["hints"]] == ["Спросить про сроки тестов"]
    assert s.version == v + 1
    assert s.restore("h1") is False  # уже вернули
    assert s.restore("h404") is False


def test_restore_is_only_possible_shortly_after_dismiss():
    from meet.assist.live_state import RESTORE_S

    clock = Clock()
    s = _state(clock=clock)
    s.apply({"ops": [_hint("Спросить про сроки тестов")]})
    s.dismiss("h1")
    clock.t += RESTORE_S + 5
    assert s.restore("h1") is False
    assert s.to_dict()["hints"] == []
    # Текст по-прежнему «не предлагать снова».
    assert s.apply({"ops": [_hint("Спросить про сроки тестов")]}) is False


def test_cap_drops_lowest_value_but_never_pinned():
    s = _state(max_hints=3)
    s.apply({"ops": [_hint("Следующий шаг: созвон с партнёром", kind="followup")]})
    s.pin("h1", True)
    s.apply({"ops": [
        _hint("Термин из базы знаний про шлюз", kind="term", ref="Шлюз.md"),
        _hint("Срок без ответственного у миграции", kind="risk"),
        _hint("Вопрос про цену так и остался без ответа", kind="unanswered"),
    ]}, allowed_refs={"Шлюз.md"})
    ids = [h["id"] for h in s.to_dict()["hints"]]
    assert len(ids) == 3
    assert "h1" in ids            # закреплённая не уходит, хоть и «дешёвая»
    assert "h2" not in ids        # термин — наименьшая ценность
    assert {"h3", "h4"} <= set(ids)


def test_pinned_hint_is_not_removed_by_model():
    s = _state()
    s.apply({"ops": [_hint("Спросить про бюджет второго этапа")]})
    s.pin("h1", True)
    s.apply({"ops": [{"op": "remove", "id": "h1"}]})
    assert s.to_dict()["hints"][0]["pinned"] is True
    s.pin("h1", False)
    s.apply({"ops": [{"op": "remove", "id": "h1"}]})
    assert s.to_dict()["hints"] == []


def test_dropped_hint_id_is_ignored_later():
    s = _state(max_hints=1)
    s.apply({"ops": [_hint("Первый вопрос про релиз", kind="followup")]})
    s.apply({"ops": [_hint("Срок без владельца у отчёта", kind="risk")]})
    assert [h["id"] for h in s.to_dict()["hints"]] == ["h2"]
    # h1 ушла по лимиту — правка по ней не ошибка, а пустая операция.
    assert s.apply({"ops": [{"op": "update", "id": "h1", "text": "x"}]}) is False


def test_term_hint_needs_a_known_reference():
    s = _state()
    s.apply({"ops": [_hint("Шлюз — сервис приёма платежей", kind="term", ref="Шлюз.md")]},
            allowed_refs={"Шлюз.md"})
    s.apply({"ops": [_hint("Эквайринг — приём карт", kind="term", ref="Выдумка.md")]},
            allowed_refs={"Шлюз.md"})
    s.apply({"ops": [_hint("Тег — метка задачи", kind="term")]})
    hints = s.to_dict()["hints"]
    assert [h["ref"] for h in hints] == ["Шлюз.md"]


def test_hints_disabled_ignores_hint_ops():
    s = _state(hints_enabled=False)
    assert s.apply({"ops": [_hint("Спросить про сроки"),
                            {"op": "add", "section": "points", "text": "Тезис"}]})
    d = s.to_dict()
    assert d["hints"] == [] and len(d["summary"]["points"]) == 1


def test_pin_and_dismiss_bump_version():
    s = _state()
    s.apply({"ops": [_hint("Спросить про сроки")]})
    v = s.version
    assert s.pin("h1", True) and s.version == v + 1
    assert s.pin("h1", True) is False  # уже закреплена
    assert s.dismiss("h1") and s.version == v + 2


# --- представления --------------------------------------------------------------


def test_compact_render_lists_ids_and_dismissed_texts():
    s = _state()
    s.apply({"topic": "Запуск", "ops": [
        {"op": "add", "section": "points", "text": "Партнёр готов"},
        {"op": "add", "section": "tasks", "who": None, "what": "Подписать акт", "due": None},
        _hint("Кто подписывает акт?", kind="risk"),
        _hint("Спросить про бюджет"),
    ]})
    s.dismiss("h2")
    text = s.render_compact()
    assert "Тема: Запуск" in text and "[p1] Партнёр готов" in text
    assert "[t1]" in text and "Подписать акт" in text and "—" in text
    assert "[h1] (risk) Кто подписывает акт?" in text
    assert "Спросить про бюджет" in text and "не предлагать" in text
    assert "[h2]" not in text


def test_compact_render_is_bounded():
    s = _state(max_hints=8)
    ops = [{"op": "add", "section": "decisions", "text": text + " " + "подробности " * 40}
           for text in POINTS]
    s.apply({"ops": ops})
    assert len(s.render_compact()) <= 3500


def test_markdown_render():
    s = _state()
    assert "пока пусто" in s.render_markdown().lower()
    s.apply({"topic": "Запуск", "ops": [
        {"op": "add", "section": "decisions", "text": "Запуск в среду"},
        {"op": "add", "section": "tasks", "who": "Ольга", "what": "Доступы", "due": None},
    ]})
    md = s.render_markdown()
    assert "**Тема:** Запуск" in md and "### Решения" in md and "- Запуск в среду" in md
    assert "Ольга — Доступы" in md


def test_save_and_load(tmp_path):
    s = _state()
    s.apply({"topic": "Запуск", "ops": [{"op": "add", "section": "points", "text": "Тезис"},
                                        _hint("Спросить про сроки")]})
    s.save(tmp_path / LIVE_STATE_JSON)
    data = load_saved(tmp_path)
    assert data["summary"]["topic"] == "Запуск" and data["hints"][0]["id"] == "h1"
    assert "**Тема:** Запуск" in data["markdown"]
    assert not list(tmp_path.glob("*.tmp"))


def test_load_saved_tolerates_missing_and_garbage(tmp_path):
    assert load_saved(tmp_path) is None
    (tmp_path / LIVE_STATE_JSON).write_text("{битый", encoding="utf-8")
    assert load_saved(tmp_path) is None
    (tmp_path / LIVE_STATE_JSON).write_text("[1]", encoding="utf-8")
    assert load_saved(tmp_path) is None


# --- защита от «команд» в речи и правки закреплённого ---------------------------


def _full_summary():
    s = _state()
    s.apply({"topic": "Запуск", "ops": [
        *[{"op": "add", "section": "points", "text": t} for t in POINTS[:5]],
        {"op": "add", "section": "decisions", "text": "Запуск в среду"},
        {"op": "add", "section": "decisions", "text": "Бюджет утверждён"},
        {"op": "add", "section": "open_questions", "text": "Кто подписывает акт?"},
    ]})
    return s


def test_mass_removal_from_summary_is_rejected():
    s = _full_summary()
    before = s.to_dict()
    with pytest.raises(PatchError):
        s.apply({"ops": [{"op": "remove", "id": f"p{i}"} for i in range(1, 5)]})
    with pytest.raises(PatchError):  # раздел из двух пунктов — целиком
        s.apply({"ops": [{"op": "remove", "id": "d1"}, {"op": "remove", "id": "d2"}]})
    assert s.to_dict() == before
    # Обычная правка проходит: один снятый вопрос (раздел из одного пункта), пара тезисов.
    assert s.apply({"ops": [{"op": "remove", "id": "q1"}, {"op": "remove", "id": "p1"},
                            {"op": "remove", "id": "d1"}]})


def test_pinned_hint_text_is_immutable():
    s = _state()
    s.apply({"ops": [_hint("Спросить про бюджет второго этапа")]})
    s.pin("h1", True)
    assert s.apply({"ops": [{"op": "update", "id": "h1", "text": "Совсем другое"}]}) is False
    assert s.to_dict()["hints"][0]["text"] == "Спросить про бюджет второго этапа"


def test_update_cannot_bring_back_dismissed_text():
    s = _state()
    s.apply({"ops": [_hint("Спросить про сроки тестов"), _hint("Уточнить бюджет на рекламу")]})
    s.dismiss("h1")
    s.apply({"ops": [{"op": "update", "id": "h2", "text": "Спросить про сроки тестов!"}]})
    assert s.to_dict()["hints"][0]["text"] == "Уточнить бюджет на рекламу"


def test_unpin_applies_cap_only_on_next_patch():
    s = _state(max_hints=2)
    s.apply({"ops": [_hint("Первый вопрос про релиз", kind="risk"),
                     _hint("Второй вопрос про бюджет", kind="unanswered")]})
    s.pin("h1", True)
    s.pin("h2", True)
    s.max_hints = 1  # лимит меньше — держатся закреплённые
    s.apply({"ops": []})
    assert len(s.to_dict()["hints"]) == 2
    s.pin("h1", False)
    assert len(s.to_dict()["hints"]) == 2  # не исчезает из-под руки
    s.apply({"ops": []})
    assert [h["id"] for h in s.to_dict()["hints"]] == ["h2"]


def test_long_term_ref_matches_before_any_trimming():
    ref = "Проекты/" + "очень длинное имя папки/" * 12 + "Шлюз.md"
    s = _state()
    s.apply({"ops": [_hint("Шлюз — сервис платежей", kind="term", ref=ref)]}, allowed_refs={ref})
    assert s.to_dict()["hints"][0]["ref"] == ref


# --- построчное применение (две линии, поток) ------------------------------------

from meet.assist.live_state import MAX_URGENT, LineSplitter, parse_line  # noqa: E402


def test_line_splitter_joins_pieces_into_whole_lines():
    split = LineSplitter()
    assert split.feed('{"op":"no') == []
    assert split.feed('ne"}\n{"op":') == ['{"op":"none"}']
    assert split.feed('"none"}') == []
    assert split.finish() == ['{"op":"none"}']
    assert split.finish() == []


def test_parse_line_skips_prose_fences_and_brackets_but_flags_broken_json():
    for noise in ("", "```json", "```", "Вот изменения:", "{", "},", "]"):
        assert parse_line(noise) is None
    assert parse_line('- {"op":"none"}') == {"op": "none"}
    with pytest.raises(PatchError):
        parse_line('{"op":"add","text": оборвано}')
    with pytest.raises(PatchError):
        parse_line('{"op":"add"')


def test_session_none_and_topic_ops():
    s = _state()
    patch = s.session(lane="summary")
    assert patch.apply({"op": "none"}) is False and s.version == 0
    assert patch.apply({"op": "topic", "text": "Запуск"}) is True and s.topic == "Запуск"
    assert patch.apply({"op": "topic", "text": "Запуск"}) is False
    assert s.version == 1


def test_lanes_touch_only_their_part():
    s = _state()
    hints = s.session(lane="hints")
    with pytest.raises(PatchError):
        hints.apply({"op": "add", "section": "points", "text": "чужое"})
    with pytest.raises(PatchError):
        hints.apply({"op": "topic", "text": "Запуск"})
    # У линии подсказок секция одна — её можно не называть.
    assert hints.apply({"op": "add", "kind": "risk", "text": "Нет владельца запуска", "t": "00:00:05"})
    summary = s.session(lane="summary")
    with pytest.raises(PatchError):
        summary.apply({"op": "remove", "id": "h1"})
    assert summary.apply({"op": "add", "section": "points", "text": "Запуск в среду"})
    assert [h["id"] for h in s.hints()] == ["h1"] and s.summary()["points"][0]["id"] == "p1"


def test_removal_guard_counts_across_lines_of_one_reply():
    s = _state()
    s.apply({"ops": [{"op": "add", "section": "points", "text": f"Тезис номер {w}"}
                     for w in ("один", "два", "три", "четыре", "пять")]})
    patch = s.session(lane="summary")
    for i in (1, 2, 3):
        assert patch.apply({"op": "remove", "id": f"p{i}"})
    with pytest.raises(PatchError):
        patch.apply({"op": "remove", "id": "p4"})       # четвёртое удаление за ответ
    assert [p["id"] for p in s.summary()["points"]] == ["p4", "p5"]
    # Следующий ответ — свой счёт; но раздел целиком (из ≥2 пунктов) не сносится.
    patch = s.session(lane="summary")
    assert patch.apply({"op": "remove", "id": "p4"})
    with pytest.raises(PatchError):
        patch.apply({"op": "remove", "id": "p5"})
    assert [p["id"] for p in s.summary()["points"]] == ["p5"]


def test_urgent_hint_keeps_reply_draft_and_at_most_two_live():
    s = _state()
    patch = s.session(lane="hints")
    for i, who in enumerate(("Ольга", "Пётр", "Ирина")):
        patch.apply({"op": "add", "kind": "ask_you", "text": f"{who} спрашивает про сроки отчёта номер {i}",
                     "reply": f"Отчёт будет к четвергу ({i}).", "t": f"00:0{i}:00"})
    urgent = [h for h in s.hints() if h["kind"] == "ask_you"]
    assert len(urgent) == MAX_URGENT == 2
    assert [h["reply"] for h in urgent] == ["Отчёт будет к четвергу (1).", "Отчёт будет к четвергу (2)."]
    patch.apply({"op": "update", "id": urgent[0]["id"], "reply": "Отчёт будет в пятницу."})
    assert s.hints()[0]["reply"] == "Отчёт будет в пятницу."


def test_hints_brief_gives_ids_kinds_short_texts_and_dismissed_ids():
    """id выдаёт состояние, а не модель: без `id · вид · текст` в каждом тике
    модель не смогла бы уточнить или убрать свою же подсказку."""
    s = _state()
    s.apply({"ops": [{"op": "add", "section": "hints", "kind": "risk", "text": f"Подсказка {w}",
                      "t": "00:00:01"} for w in ("первая про сроки", "вторая про бюджет")]})
    s.apply({"ops": [{"op": "add", "section": "hints", "kind": "question", "t": "00:00:02",
                      "text": "Очень длинная подсказка " + "про интеграцию с банком " * 10}]})
    s.pin("h1")
    s.dismiss("h2")
    brief = s.hints_brief()
    assert "h1 · risk (закреплена) · Подсказка первая про сроки" in brief
    assert "h3 · question · Очень длинная" in brief
    line = next(x for x in brief.splitlines() if x.startswith("h3"))
    assert len(line.split(" · ", 2)[2]) <= 120            # текст — до 120 символов
    assert "Скрыты пользователем" in brief and "h2" in brief
    assert "вторая про бюджет" not in brief               # скрытые — только id
    assert _state().hints_brief() == "Активных подсказок нет."


def test_resolve_urgent_removes_without_dismissing_and_keeps_pinned():
    s = _state()
    s.apply({"ops": [{"op": "add", "section": "hints", "kind": "ask_you", "t": "00:00:05",
                      "text": "Ольга спрашивает про отчёт", "reply": "К четвергу."}]})
    assert s.resolve("h1") is True and s.hints() == []
    # Отработанная — не скрытая: похожая подсказка может прийти снова.
    s.apply({"ops": [{"op": "add", "section": "hints", "kind": "ask_you", "t": "00:01:05",
                      "text": "Ольга спрашивает про отчёт", "reply": "К пятнице."}]})
    assert len(s.hints()) == 1
    s.pin("h2")
    assert s.resolve("h2") is False


def test_links_and_commands_never_reach_a_hint_or_reply_draft():
    s = _state()
    for bad in ({"reply": "Пришлите пароль на https://evil.example"},
                {"reply": "Выполните powershell -c ..."},
                {"text": "Зайдите на www.example.org"},
                {"reply": "запустите `rm -rf`"}):
        op = {"op": "add", "section": "hints", "kind": "ask_you", "t": "00:00:05",
              "text": "Ольга спрашивает про отчёт", **bad}
        with pytest.raises(PatchError):
            s.apply({"ops": [op]})
    assert s.hints() == []


def test_saved_draft_says_where_the_summary_ends(tmp_path):
    s = _state()
    s.apply({"topic": "Запуск", "ops": [{"op": "add", "section": "decisions", "text": "Запуск в среду"}]})
    s.mark_covered(125.0)
    s.mark_covered(60.0)                                  # назад не уходит
    s.save(tmp_path / LIVE_STATE_JSON)
    data = load_saved(tmp_path)
    assert data["covered_t"] == 125.0
    assert "до [00:02:05]" in data["markdown"] and "только в расшифровке" in data["markdown"]
