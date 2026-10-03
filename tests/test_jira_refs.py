"""Задачи Jira, названные во встрече (meet.jira_refs): числа словами и
цифрами, варианты названия проекта, шаблоны, слово-признак и проект по
умолчанию, реплики окна, слияние со ссылками анализа и карточка записи.

Проекты (ORION, SPR, OPS, DEMO) и фразы выдуманы.
"""

import json

import pytest

from meet import analysis, jira_refs, library, settings, tray, tray_control

PROJECTS = [("ORION", ["орайон"]), ("SPR", []), ("OPS", []), ("DEMO", []), ("KDEV", [])]


def _spec(default="ORION", projects=PROJECTS):
    keys = "|".join(p[0] for p in projects)
    return jira_refs.Spec.build(projects, default, rf"(?:{keys})-\d+")


def _keys(text, spec=None):
    return [r.key for r in jira_refs.find(text, spec or _spec())]


# --- числа ----------------------------------------------------------------------


def _number(text):
    text = jira_refs.nfc(text)
    toks = jira_refs.tokens(text)
    num = jira_refs.number_at(toks, 0, text)
    return None if num is None else num.digits


@pytest.mark.parametrize("text, digits", [
    ("2122", "2122"), ("2 122", "2122"), ("21-22", "2122"), ("21 22", "2122"), ("1 234 567", ""),
    ("две тысячи сто двадцать два", "2122"), ("двадцать один двадцать два", "2122"),
    ("сорок четыре пятьдесят два", "4452"), ("тысяча двести", "1200"), ("сто два", "102"),
    ("сто двадцать три сорок пять", "12345"), ("два один два два", "2122"),
    ("девятьсот девяносто девять тысяч девятьсот девяносто девять", "999999"),
    ("один миллион", "1"), ("двадцать один ноль пять", "2105"), ("ноль пять", ""),
    ("twenty one twenty two", "2122"), ("forty-four fifty-two", "4452"),
    ("two thousand one hundred and twenty two", "2122"), ("one hundred two", "102"),
    ("четыре пять шесть семь восемь девять один", ""), ("1234567", ""), ("0042", ""),
])
def test_number_words_and_digits(text, digits):
    assert _number(text) == digits


@pytest.mark.parametrize("text", ["задача", "минус", "ORION", ""])
def test_not_a_number(text):
    assert _number(text) is None


def test_digit_groups_join_only_as_thousands_or_pairs():
    # «2122 2123» — два номера, а не 21222123; «212 12» — не разряды.
    assert _number("2122 2123") == "2122"
    assert _number("212 12") == "212"
    assert _number("21 22 23") == "2122"


# --- варианты названия проекта --------------------------------------------------


def _forms(key, aliases=()):
    return {" ".join("/".join(" ".join(a) for a in slot) for slot in v.slots)
            for v in jira_refs.project_variants(key, aliases)}


def test_project_variants_latin_translit_spelling_and_aliases():
    forms = _forms("ORION", ["орайон"])
    assert "orion" in forms and "o r ion" in forms and "or ion" in forms
    assert "орион" in forms and "орайон" in forms
    kdev = _forms("KDEV")
    assert "кдев" in kdev and "кей/ка дев" in kdev and "кейдев" in kdev
    assert any(f.startswith("кей/ка ди/де и/е ви/ве") for f in kdev)
    # Свои варианты — как слова, «э» и «е» не различаются: «кей дэв» → «кей дев».
    assert "кей дев" in _forms("XY", ["кей дэв"])


def test_short_key_spelling_has_no_one_letter_words():
    # «AI» по буквам — не «а и»: такие слова есть в любой фразе.
    assert not any("а" in f.split() or "и" in f.split() for f in _forms("AI"))


def test_common_word_is_not_a_project_reading():
    # «DOM» читается как «дом» — обычное слово, а не проект.
    assert "дом" not in _forms("DOM")
    assert _keys("в дом 15 квартир", jira_refs.Spec.build([("DOM", [])])) == []


def test_key_with_digits_and_declension():
    spec = jira_refs.Spec.build([("OPS2", []), ("ORION", [])])
    assert _keys("OPS2 15 и в орионе 2122, по ориону 2123", spec) == ["OPS2-15", "ORION-2122", "ORION-2123"]


# --- корпус: что находится и что нет ----------------------------------------------

POSITIVE = [
    # три примера пользователя
    ("ORION 2122", ["ORION-2122"]),
    ("орион двадцать один двадцать два уже в работе", ["ORION-2122"]),
    ("в баге с номером 4452 опять таймаут", ["ORION-4452"]),
    ("Посмотри ORION 2122, там падает экспорт.", ["ORION-2122"]),
    ("в баге 4452 нужен лог", ["ORION-4452"]),
    ("задача номер сорок четыре пятьдесят два готова", ["ORION-4452"]),
    ("тикет 4452 закрыли вчера", ["ORION-4452"]),
    ("кей дев сорок четыре пятьдесят два", ["KDEV-4452"]),
    ("SPR две тысячи сто двадцать два", ["SPR-2122"]),
    ("в орионе 2122 поправили фильтр", ["ORION-2122"]),
    ("орайон 21-22 переоткрыт", ["ORION-2122"]),
    ("по эс пи ар сто два ответа нет", ["SPR-102"]),
    ("OPS № 77 про сертификаты", ["OPS-77"]),
    ("ORION-2122 и SPR-15 связаны", ["ORION-2122", "SPR-15"]),
    ("орион 2122 и 2123", ["ORION-2122", "ORION-2123"]),
    ("по ориону 2122, 2123 и 2124 — всё в ревью", ["ORION-2122", "ORION-2123", "ORION-2124"]),
    ("issue 4452 assigned to Anna", ["ORION-4452"]),
    ("SPR twenty one twenty two is blocked", ["SPR-2122"]),
    ("в джире под номером сорок четыре пятьдесят два", ["ORION-4452"]),
    ("опс номер 5 перезапустили", ["OPS-5"]),
    ("орион 2 122 висит", ["ORION-2122"]),
    ("orion 2122 lowercase", ["ORION-2122"]),
    ("кейдев две тысячи сто двадцать два", ["KDEV-2122"]),
    ("ORI ON 2122", ["ORION-2122"]),
    ("по ориону 3345 и по спр 120", ["ORION-3345", "SPR-120"]),
    ("эпик 512 пора закрывать", ["ORION-512"]),
    ("в задаче орион 2122 два бага", ["ORION-2122"]),
    ("ORION 4452: сорок четыре минуты на ревью", ["ORION-4452"]),
    ("эс пэ эр тысяча двести", ["SPR-1200"]),
    ("SPR 21 22", ["SPR-2122"]),
    ("оу пи эс восемьдесят один", ["OPS-81"]),
    ("демо девятьсот один, демо девятьсот два", ["DEMO-901", "DEMO-902"]),
    ("Орион-2122 и ORION — 2123", ["ORION-2122", "ORION-2123"]),
]

NEGATIVE = [
    "в 2122 году всё изменится",
    "это стоит 2 122 рубля",
    "сорок четыре минуты обсуждали",
    "в орионе 2 122 рубля бюджета",
    "орион двадцать первого релизится",
    "по ориону пять человек",
    "в орионе один из тикетов",
    "задача на 2 часа",
    "тикет с 2023 года висит",
    "у нас 15 багов в бэклоге",
    "орион в 21:22 упал",
    "в спринте двадцать две задачи",
    "ORION двадцать два бага",
    "ORION 1234567",
    "задача была сложная, 2122 строки кода",
    "встретимся в 15:30 обсудить орион",
    "ORION 2,5 процента",
    "орион на 30% быстрее",
    "версия 2.1.22 вышла",
    "XORION-12 и ORION-12a",
    "SPR пять минут",
    "орион ноль пять",
    "по задаче было 15 комментариев",
    "баг висит 2122 часа",
    "орион, где-то 2122 года назад",
    # Даты, время суток, суммы и оценки (ревью 0.3.1, I1).
    "Релиз ориона 15 марта, успеваем?",
    "По ориону 20 мая финальная приёмка.",
    "Давайте сделаем демо 20 октября",
    "Демо 10 утра в четверг",
    "Задача 30 июня должна быть закрыта",
    "У нас задача 300 тысяч пользователей подключить",
    "Задача 10 тысяч строк кода переписать",
    "Стори 13 поинтов",
    "Задача номер один — стабилизировать релиз",
    "Задача номер два — нанять людей",
    "Орион номер один в рейтинге продуктов",
    # Свободные числа после проекта и слова-признака (ревью 0.3.1, m2).
    "Орион, у нас там 12 открытых вопросов и 40 мелочей",
    "Значит, задача 18 переходит дальше",
    # Свои: обычные фразы встреч с датами, суммами и временем.
    "Демо 25 декабря, а орион 1 января уже в проде",
    "Созвон по SPR 14 числа, не забудьте",
    "Орион 3 ночи упал, дежурный поднял",
    "По KDEV 7 вечера выкатываем",
    "В баге 200 сотен строк лога, бесполезно",
    "Тикет 150 рублей стоит подписка",
    "Задача 500 тысяч рублей бюджета",
    "Демо 2026 года будет онлайн",
    "Опс 5 баллов из десяти, так себе",
    "Стори 8 очков, берём в спринт",
    "ORION 25 Dec release",
    "SPR 9 am standup",
    "Орион, кажется, в 2122 году появился",
    "Эпик 120 дней тянется",
    "Задача номер три в списке приоритетов",
    "Тикет 40 процентов готов",
    "Орион в среду, 2122 и 2123 подождут",
]


def _score(cases, negatives, spec):
    tp = fp = fn = 0
    for text, want in cases:
        got = _keys(text, spec)
        tp += len([k for k in got if k in want])
        fp += len([k for k in got if k not in want])
        fn += len([k for k in want if k not in got])
    for text in negatives:
        fp += len(_keys(text, spec))
    return tp, fp, fn


def test_corpus_is_large_enough():
    assert len(POSITIVE) >= 25 and len(NEGATIVE) >= 15


@pytest.mark.parametrize("text, want", POSITIVE)
def test_positive(text, want):
    assert _keys(text) == want


@pytest.mark.parametrize("text", NEGATIVE)
def test_negative(text):
    assert _keys(text) == []


def test_corpus_precision_and_recall():
    tp, fp, fn = _score(POSITIVE, NEGATIVE, _spec())
    assert tp / (tp + fp) == 1.0
    assert tp / (tp + fn) == 1.0


# --- источники и проект по умолчанию ----------------------------------------------


def test_sources_and_spans():
    text = "ORION-2122, потом орион двадцать один двадцать три. И в баге 4452"
    got = jira_refs.find(text, _spec())
    assert [(r.key, r.source, text[r.start:r.end]) for r in got] == [
        ("ORION-2122", "literal", "ORION-2122"),
        ("ORION-2123", "spoken", "орион двадцать один двадцать три"),
        ("ORION-4452", "context", "4452"),
    ]


@pytest.mark.parametrize("trigger", ["в баге", "тикет", "задача номер", "issue", "таска", "ишью",
                                     "в джире", "баг под номером"])
def test_trigger_words_link_to_default_project(trigger):
    assert _keys(f"{trigger} 4452") == ["ORION-4452"]


def test_without_default_project_context_numbers_are_left_to_the_analysis():
    assert _keys("в баге 4452", _spec(default="")) == []
    # Номер дальше трёх слов от признака — не задача.
    assert _keys("баг который мы вчера нашли 4452") == []
    # Двузначный номер по слову-признаку — только с «номер»/«№».
    assert _keys("задача 18") == [] and _keys("задача номер 18") == ["ORION-18"] and _keys("тикет № 7") == ["ORION-7"]


def test_literal_pattern_only_for_configured_projects():
    assert _keys("ABC-12 и SPR-3") == ["SPR-3"]
    anything = jira_refs.Spec.build([], "", settings.DEFAULT_JIRA_KEYS)
    assert _keys("ABC-12 и SPR-3", anything) == ["ABC-12", "SPR-3"]


# --- реплики окна и слияние со ссылками анализа ------------------------------------


def _seg(start, speaker, text, **extra):
    return {"start": start, "end": start + 1.0, "speaker": speaker, "text": text, **extra}


def test_segment_refs_follow_window_turns_and_utf16_offsets():
    segments = [
        _seg(0, "Анна", "Смотрим 🙂 орион"),
        _seg(1, "Анна", "двадцать один двадцать два, ок?"),
        _seg(2, "Борис", "А SPR 15?"),
        {"start": 3, "end": 3, "kind": "break", "text": "— перерыв — SPR 7"},
        _seg(10, "Борис", "две тысячи двадцать два"),
    ]
    refs = jira_refs.segment_refs(segments, _spec())
    assert [(r["segment"], r["key"], r["source"]) for r in refs] == [
        (0, "ORION-2122", "spoken"), (2, "SPR-15", "spoken")]
    first = refs[0]
    # 🙂 — два знака UTF-16: позиции — как у строк JavaScript; ссылка уходит в следующий сегмент.
    assert first["start"] == len("Смотрим 🙂 ".encode("utf-16-le")) // 2
    assert first["spoken"] == "орион двадцать один двадцать два"
    assert first["end"] - first["start"] == len(first["spoken"])


def test_speaker_change_or_pause_splits_turns():
    segments = [_seg(0, "Анна", "орион"), _seg(1, "Борис", "двадцать два")]
    assert jira_refs.segment_refs(segments, _spec()) == []
    segments = [_seg(0, "Анна", "орион"), _seg(5, "Анна", "двадцать два")]
    assert jira_refs.segment_refs(segments, _spec()) == []


def test_agent_issues_fill_gaps_and_lose_on_overlap():
    segments = [_seg(0, "Анна", "Тот баг про экспорт, сорок четыре пятьдесят два, и ORION 2122")]
    issues = [
        {"key": "ORION-4452", "segments": [0], "spoken": "баг про экспорт, сорок четыре пятьдесят два"},
        {"key": "SPR-2122", "segments": [0], "spoken": "ORION 2122"},  # детерминированный слой сильнее
        {"key": "ORION-9", "segments": [0], "spoken": "этих слов нет"},
    ]
    refs = jira_refs.segment_refs(segments, _spec(default=""), issues)
    assert [(r["key"], r["source"], r["spoken"]) for r in refs] == [
        ("ORION-4452", "agent", "баг про экспорт, сорок четыре пятьдесят два"),
        ("ORION-2122", "spoken", "ORION 2122"),
    ]


def test_phrases_for_summary_and_insights():
    got = jira_refs.phrases(["- [ ] Починить ORION-2122\n- орион двадцать один двадцать три — Анна",
                             "Повторяется ORION-2122\nСроки — в баге 4452."], _spec())
    assert got == [
        {"text": "ORION-2122", "key": "ORION-2122", "source": "literal"},
        {"text": "орион двадцать один двадцать три", "key": "ORION-2123", "source": "spoken"},
        # По слову-признаку — вместе со словом: голое «4452» в итогах ссылкой не станет.
        {"text": "баге 4452", "key": "ORION-4452", "source": "context"},
    ]


# --- по настройкам и в карточке записи ----------------------------------------------


def _cfg(**integrations):
    raw = {"integrations": {"jira_base_url": "https://jira.example.com",
                            "jira_projects": [{"key": "ORION", "aliases": []}, {"key": "SPR", "aliases": []}],
                            "jira_default_project": "ORION", **integrations}}
    return settings.Settings.from_raw(raw)


def test_spec_off_without_base_url_or_when_links_are_off():
    assert jira_refs.spec_of(_cfg(jira_base_url="")) is None
    off = settings.Settings.from_raw({"integrations": _cfg().integrations.to_raw(),
                                      "transcript_view": {"jira": False}})
    assert jira_refs.spec_of(off) is None
    assert jira_refs.spec_of(_cfg()).default == "ORION"


def test_for_recording_uses_fresh_analysis_issues_of_configured_projects():
    data = {"segments": [_seg(0, "Анна", "Тот самый, сорок четыре пятьдесят два, и орион 2122")]}
    doc = {"segments": 1, "issues": [
        {"key": "ORION-4452", "segments": [0], "spoken": "сорок четыре пятьдесят два"},
        {"key": "OPS-1", "segments": [0], "spoken": "Тот самый"}],
        "insights": [{"text": "SPR 15 без владельца", "why": "срок — в баге 4452"}]}
    got = jira_refs.for_recording(data, _cfg(), analysis_doc=doc, summary="Итог: ORION-2122")
    assert [(r["key"], r["source"]) for r in got["refs"]] == [("ORION-4452", "agent"), ("ORION-2122", "spoken")]
    assert {p["key"] for p in got["phrases"]} == {"ORION-2122", "SPR-15", "ORION-4452"}
    # Анализ про другую расшифровку (другое число сегментов) или выключенная часть — без него.
    assert [r["source"] for r in jira_refs.for_recording(data, _cfg(), analysis_doc={**doc, "segments": 2})["refs"]] \
        == ["spoken"]
    no_issues = settings.Settings.from_raw({"integrations": _cfg().integrations.to_raw(),
                                            "analysis": {"issues": False}})
    assert len(jira_refs.for_recording(data, no_issues, analysis_doc=doc)["refs"]) == 1


def test_recording_detail_carries_jira_refs(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    config = tmp_path / "meet" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "recording": {"out_dir": str(tmp_path / "recordings"), "voices_dir": str(tmp_path / "voices")},
        "integrations": {"jira_base_url": "https://jira.example.com",
                         "jira_projects": [{"key": "ORION", "aliases": []}]},
    }, ensure_ascii=False), encoding="utf-8")
    folder = tmp_path / "recordings" / "2026-10-01_10-00"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "segments": [_seg(0, "SPEAKER_00", "орион 2122")]})
    (folder / "summary.md").write_text("## Задачи\n- ORION-2123 — Анна\n", encoding="utf-8")
    app = tray.TrayApp()
    app.cfg = tray._auto_config()
    state = tray_control.TrayControl(app)
    got = state.recording(folder.name)
    assert got["jira"]["refs"] == [{"segment": 0, "start": 0, "end": 10, "key": "ORION-2122",
                                    "source": "spoken", "spoken": "орион 2122"}]
    assert got["jira"]["phrases"] == [{"text": "ORION-2123", "key": "ORION-2123", "source": "literal"}]
    # Без адреса Jira — поля нет.
    config.write_text(json.dumps({"recording": {"out_dir": str(tmp_path / "recordings")}}), encoding="utf-8")
    assert "jira" not in state.recording(folder.name)


# --- анализ встречи: ссылки модели ------------------------------------------------


TEXTS = {0: "Тот баг про экспорт, сорок четыре пятьдесят два, надо добить.",
         1: "И ещё орион двадцать один двадцать два."}


def _issues(raw, projects=("ORION", "SPR")):
    return analysis._issues(raw, {0, 1}, TEXTS, set(projects))


def test_analysis_issues_are_validated():
    good = {"key": "orion-4452", "segments": [0, 1, 7], "spoken": "«сорок четыре пятьдесят два»",
            "confidence": 0.8}
    assert _issues([good]) == [{"key": "ORION-4452", "segments": [0], "spoken": "сорок четыре пятьдесят два",
                                "confidence": 0.8}]
    for bad in (
        {**good, "key": "OPS-4452"},  # проекта нет в настройках
        {**good, "key": "ORION-4453"},  # номер не тот, что сказан
        {**good, "confidence": 0.5},  # не уверена
        {**good, "spoken": "этого нет в реплике сорок четыре пятьдесят два"},
        {**good, "segments": [1]},  # не в той реплике
        {**good, "key": "ORION"}, {**good, "key": "ORION-0"}, {**good, "key": "ORION-1234567"},
        "ORION-4452",
    ):
        assert _issues([bad]) == [], bad
    with pytest.raises(ValueError):
        _issues({"ORION-4452": 0})


def test_analysis_issues_prompt_only_with_projects():
    cats = [c.to_raw() for c in settings.default_categories()]
    with_jira = analysis.build_system(analysis.FEATURES, cats, jira=(("ORION", "SPR"), "ORION"))
    assert '"issues"' in with_jira and "ORION, SPR" in with_jira and "по умолчанию" in with_jira
    assert '"issues"' not in analysis.build_system(("chapters",), cats)


class _Runner:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def __call__(self, prompt, **kwargs):
        from meet.llm.base import AgentReply

        self.calls.append({"prompt": prompt, **kwargs})
        return AgentReply(text=json.dumps(self.replies.pop(0), ensure_ascii=False))


def _analysis_folder(tmp_path):
    folder = tmp_path / "2026-10-01_10-00"
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "segments": [
        _seg(0, "Анна", TEXTS[0]), _seg(1, "Борис", TEXTS[1])]})
    return folder


def test_analysis_run_requests_and_stores_issues(tmp_path):
    folder = _analysis_folder(tmp_path)
    runner = _Runner({"issues": [{"key": "ORION-4452", "segments": [0], "spoken": "сорок четыре пятьдесят два",
                                  "confidence": 0.9},
                                 {"key": "ORION-1", "segments": [0], "spoken": "Тот баг", "confidence": 0.9}]})
    cfg = _cfg()
    doc = analysis.run(folder, runner, cfg, features=("issues",), now=1.0)
    assert doc["features"] == ["issues"]
    assert doc["issues"] == [{"key": "ORION-4452", "segments": [0], "spoken": "сорок четыре пятьдесят два",
                              "confidence": 0.9}]
    assert "ORION, SPR" in runner.calls[0]["system_prompt"]


def test_analysis_without_projects_does_not_ask_for_issues(tmp_path):
    folder = _analysis_folder(tmp_path)
    runner = _Runner({"chapters": []})
    cfg = settings.Settings()
    doc = analysis.run(folder, runner, cfg, features=("chapters", "issues"), now=1.0)
    assert doc["features"] == ["chapters"] and "issues" not in doc
    assert '"issues"' not in runner.calls[0]["system_prompt"]


def test_merge_windows_joins_same_issue():
    a = {"issues": [{"key": "ORION-4452", "segments": [0], "spoken": "сорок четыре пятьдесят два",
                     "confidence": 0.7}]}
    b = {"issues": [{"key": "ORION-4452", "segments": [3], "spoken": "Сорок четыре пятьдесят два",
                     "confidence": 0.9},
                    {"key": "SPR-15", "segments": [4], "spoken": "спр 15", "confidence": 0.8}]}
    got = analysis.merge_windows([a, b], [0, 1, 2, 3, 4])["issues"]
    assert got == [{"key": "ORION-4452", "segments": [0, 3], "spoken": "сорок четыре пятьдесят два",
                    "confidence": 0.9},
                   {"key": "SPR-15", "segments": [4], "spoken": "спр 15", "confidence": 0.8}]


def test_recording_detail_reuses_refs_until_something_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    config = tmp_path / "meet" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "recording": {"out_dir": str(tmp_path / "recordings"), "voices_dir": str(tmp_path / "voices")},
        "integrations": {"jira_base_url": "https://jira.example.com",
                         "jira_projects": [{"key": "ORION", "aliases": []}]},
    }, ensure_ascii=False), encoding="utf-8")
    folder = tmp_path / "recordings" / "2026-10-01_10-00"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "segments": [_seg(0, "SPEAKER_00", "орион 2122")]})
    app = tray.TrayApp()
    app.cfg = tray._auto_config()
    state = tray_control.TrayControl(app)
    calls = []
    real = jira_refs.for_recording
    monkeypatch.setattr(jira_refs, "for_recording", lambda *a, **k: calls.append(1) or real(*a, **k))
    first = state.recording(folder.name)["jira"]
    assert state.recording(folder.name)["jira"] == first and len(calls) == 1
    (folder / "summary.md").write_text("- ORION-7\n", encoding="utf-8")
    assert state.recording(folder.name)["jira"]["phrases"][0]["key"] == "ORION-7" and len(calls) == 2
