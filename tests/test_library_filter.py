"""Общий фильтр библиотеки (`meet.library_filter`): разбор параметров адреса и
проверка карточки — одни для `/recordings`, `/search`, `/categories` и `/groups`.
Данные выдуманы."""

import json

import pytest

from meet import control, library, library_filter, search, settings, tray, tray_control

CFG = settings.Settings()


def _card(**kw):
    card = {"id": "2026-10-01_10-00", "started_at": "2026-10-01T10:00:00", "duration_s": 1800.0,
            "title": "Планёрка", "source": "record", "has_transcript": True, "has_summary": False,
            "has_analysis": False, "category": None, "groups": [], "people": []}
    return {**card, **kw}


def _f(**params):
    return library_filter.from_params(params, CFG)


def test_empty_params_pass_everything():
    flt = _f()
    assert not flt.active and flt.title_only is False
    assert flt(_card()) and flt(_card(started_at=None, duration_s=None))


def test_categories_any_of_with_none_key():
    flt = _f(categories="daily,_none")
    assert flt(_card(category={"id": "daily", "source": "user"}))
    assert flt(_card(category=None))
    assert flt(_card(category={"id": "удалённая", "source": "user"}))  # удалённая — «без категории»
    assert not flt(_card(category={"id": "retro", "source": "ai"}))


def test_groups_any_of():
    flt = _f(groups="g-1,g-2")
    assert flt(_card(groups=["g-2", "g-9"]))
    assert not flt(_card(groups=["g-9"])) and not flt(_card())


def test_people_by_word_prefix_and_every_person_must_be_there():
    card = _card(people=["Анна Петрова", "Борис", "Вы"])
    assert _f(people="анн")(card)
    assert _f(people="Петр")(card)
    assert _f(people="Анна П")(card)
    assert not _f(people="нна")(card)  # только начало слова
    assert _f(people="Анна,Бор")(card)
    assert not _f(people="Анна,Глеб")(card)
    assert _f(people="Ёлкин")(_card(people=["Елкин"]))  # ё = е


def test_people_accept_repeated_params():
    flt = library_filter.from_params({"people": ["Анна", "Борис"]}, CFG)
    assert flt(_card(people=["Анна", "Борис"])) and not flt(_card(people=["Анна"]))


def test_dates_are_inclusive_and_undated_never_pass():
    flt = _f(**{"from": "2026-10-01", "to": "2026-10-02"})
    assert flt(_card(started_at="2026-10-01T00:00:00"))
    assert flt(_card(started_at="2026-10-02T23:59:00"))
    assert not flt(_card(started_at="2026-10-03T00:00:00"))
    assert not flt(_card(started_at="2026-09-30T23:00:00"))
    assert not flt(_card(started_at=None))
    assert _f(to="2026-10-01")(_card()) and not _f(**{"from": "2026-10-02"})(_card())


def test_has_and_lacks():
    card = _card(has_summary=True, source="live")
    assert _f(has="summary")(card) and _f(has="assistant")(card) and _f(has="transcript")(card)
    assert not _f(has="analysis")(card)
    assert _f(lacks="analysis")(card) and not _f(lacks="summary")(card)
    assert _f(has="summary", lacks="analysis")(card)
    assert not _f(has="transcript")(_card(has_transcript=False))
    assert not _f(has="assistant")(_card())


def test_duration_bounds_and_unknown_duration_never_passes():
    assert _f(min_s="1800")(_card()) and not _f(min_s="1801")(_card())
    assert _f(max_s="1800")(_card()) and not _f(max_s="600")(_card())
    assert not _f(min_s="0")(_card(duration_s=None))
    assert not _f(max_s="99999")(_card(duration_s=None))


def test_title_only():
    assert _f(**{"in": "title"}).title_only is True
    assert _f(**{"in": ""}).title_only is False


@pytest.mark.parametrize("params", [
    {"from": "01.10.2026"}, {"to": "2026-02-31"}, {"has": "чудо"}, {"lacks": "x"},
    {"min_s": "много"}, {"max_s": "-5"}, {"min_s": "nan"}, {"in": "body"}, {"groups": "НЕ ТАК"},
])
def test_bad_params_are_refused_in_words(params):
    with pytest.raises(library_filter.FilterError) as e:
        library_filter.from_params(params, CFG)
    assert str(e.value)  # текст — человеку


def test_without_drops_one_facet_for_its_own_counts():
    flt = _f(categories="daily", groups="g-1", people="Анна")
    card = _card(people=["Анна"], groups=["g-1"])
    assert not flt(card)  # категории нет
    assert flt.without("categories")(card)
    assert not flt.without("groups")(card)


def test_unrelated_params_are_ignored():
    assert not library_filter.from_params({"q": "бюджет", "limit": "5", "token": "t"}, CFG).active


# --- резидент: маршруты с фильтром ------------------------------------------------------


def _write_config(tmp_path) -> None:
    path = tmp_path / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(tmp_path / "recordings"), "voices_dir": str(tmp_path / "voices")},
    }), encoding="utf-8")


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    _write_config(tmp_path)
    search.clear_cache()
    return tray_control.TrayControl(tray.TrayApp())


def _rec(tmp_path, name, *, people=(), text="Обсудили бюджет.", title=None, groups=None, seconds=None):
    folder = tmp_path / "recordings" / name
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    segments = [{"start": float(i), "end": i + 1.0, "speaker": p, "text": text} for i, p in enumerate(people)]
    library.write_transcript(folder, {"version": 1, "title": title, "segments": segments})
    if groups is not None:
        library.write_meta(folder, {"groups": groups})
    if seconds is not None:
        (folder / "events.jsonl").write_text(json.dumps(
            {"kind": "record.stopped", "duration_s": seconds}) + "\n", encoding="utf-8")
    return folder


@pytest.fixture
def lib(tmp_path):
    _rec(tmp_path, "2026-09-01_10-00", people=["Анна", "Борис"], groups=["g-alpha"], seconds=3600)
    _rec(tmp_path, "2026-09-15_10-00", people=["Борис"], groups=["g-alpha", "g-beta"], seconds=600,
         title="Бюджет на квартал")
    _rec(tmp_path, "2026-10-01_10-00", people=["Анна"], text="Про отпуск.")
    return tmp_path


def _ids(items):
    return [i["id"] for i in items]


def test_recordings_with_structural_filters(state, lib):
    assert _ids(state.recordings(filters={"groups": "g-alpha"})["items"]) == [
        "2026-09-15_10-00", "2026-09-01_10-00"]
    assert _ids(state.recordings(filters={"people": "анна"})["items"]) == [
        "2026-10-01_10-00", "2026-09-01_10-00"]
    assert _ids(state.recordings(filters={"from": "2026-09-10", "to": "2026-09-30"})["items"]) == [
        "2026-09-15_10-00"]
    assert _ids(state.recordings(filters={"min_s": "1000"})["items"]) == ["2026-09-01_10-00"]
    # фильтр — до лимита: старая запись не теряется за свежими
    assert _ids(state.recordings(limit=1, filters={"groups": "g-beta"})["items"]) == ["2026-09-15_10-00"]
    with pytest.raises(control.BadRequest):
        state.recordings(filters={"from": "вчера"})


def test_search_with_filters_and_title_ranges(state, lib):
    got = state.search("бюджет", filters={"people": "Анна"})["items"]
    assert _ids(got) == ["2026-09-01_10-00"]
    got = state.search("бюджет")["items"]
    titled = next(i for i in got if i["id"] == "2026-09-15_10-00")
    assert titled["title_match"] is True and titled["title_ranges"] == [[0, 6]]
    plain = next(i for i in got if i["id"] == "2026-09-01_10-00")
    assert plain["title_ranges"] == []
    with pytest.raises(control.BadRequest):
        state.search("бюджет", filters={"has": "чудо"})


def test_search_in_title_only(state, lib):
    got = state.search("бюджет", filters={"in": "title"})["items"]
    assert _ids(got) == ["2026-09-15_10-00"]
    assert got[0]["hits"] == [] and got[0]["total"] == 0 and got[0]["title_ranges"] == [[0, 6]]
    assert state.search("отпуск", filters={"in": "title"})["items"] == []


def test_category_counts_respect_the_other_filters(state, lib):
    from meet import categories

    categories.set_user(lib / "recordings" / "2026-09-01_10-00", "daily")
    info = state.categories(filters={"groups": "g-alpha", "categories": "retro"})
    # свой фасет (категории) в счётчиках не участвует, остальные — да
    assert info["counts"] == {"daily": 1} and info["none"] == 1


# --- участники для подсказок (GET /participants) ----------------------------------------


def test_participants_rank_by_meetings_owner_last():
    cards = [_card(people=["Анна", "Вы", "Борис"], started_at="2026-10-01T10:00:00"),
             _card(people=["Анна", "Вы"], started_at="2026-10-03T10:00:00"),
             _card(people=["Борис Петров", "Вы"], started_at="2026-10-02T10:00:00"),
             _card(people=["Глеб"], started_at=None)]
    got = library_filter.participants(cards, owners={"Вы"})
    assert got == [
        {"name": "Анна", "meetings": 2, "last_at": "2026-10-03T10:00:00", "owner": False},
        {"name": "Борис Петров", "meetings": 1, "last_at": "2026-10-02T10:00:00", "owner": False},
        {"name": "Борис", "meetings": 1, "last_at": "2026-10-01T10:00:00", "owner": False},
        {"name": "Глеб", "meetings": 1, "last_at": None, "owner": False},
        {"name": "Вы", "meetings": 3, "last_at": "2026-10-03T10:00:00", "owner": True},
    ]
    assert [p["name"] for p in library_filter.participants(cards, q="пет", owners={"Вы"})] == ["Борис Петров"]
    assert [p["name"] for p in library_filter.participants(cards, q="бор", owners=set())] == [
        "Борис Петров", "Борис"]
    assert len(library_filter.participants(cards, limit=2, owners={"Вы"})) == 2


def test_participants_route(state, lib):
    got = state.participants("", 20)
    # поровну встреч — раньше тот, с кем встречались позже
    assert [(p["name"], p["meetings"]) for p in got] == [("Анна", 2), ("Борис", 2)]
    assert [p["name"] for p in state.participants("ан", 20)] == ["Анна"]
    assert ("GET", "/participants") in control._ROUTES
