"""Группы встреч (`meet.groups`): описания в `.meet-groups.json`, членство в
meta.json, маршруты резидента и событие `groups.changed`. Данные выдуманы."""

import json
import threading

import pytest

from meet import control, groups, library, merge, search, tray, tray_control


def _rec(root, name):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    return folder


def _file(root):
    return json.loads((root / groups.FILE).read_text(encoding="utf-8"))


# --- описания групп -------------------------------------------------------------------


def test_create_writes_the_file_with_a_fresh_id(tmp_path):
    got = groups.create(tmp_path, "  Проект   Альфа ")
    assert groups.ID_RE.match(got["id"]) and got["id"].startswith("g-") and len(got["id"]) == 10
    assert got["name"] == "Проект Альфа" and groups.COLOR.match(got["color"])
    assert isinstance(got["created_at"], str) and got["created_at"]
    data = _file(tmp_path)
    assert data == {"version": 1, "groups": [got]}
    assert groups.load(tmp_path) == [got]


def test_names_are_unique_without_case_and_yo(tmp_path):
    groups.create(tmp_path, "Ёлка")
    for name in ("ёлка", "ЕЛКА", " елка "):
        with pytest.raises(groups.GroupError):
            groups.create(tmp_path, name)


@pytest.mark.parametrize("name", ["", "   ", "x" * 61, "Все записи", "все  ЗАПИСИ", None, 5])
def test_bad_names_are_refused(tmp_path, name):
    with pytest.raises(groups.GroupError):
        groups.create(tmp_path, name)
    assert not (tmp_path / groups.FILE).exists()


def test_color_is_checked(tmp_path):
    assert groups.create(tmp_path, "А", color="#AABBCC")["color"] == "#AABBCC"
    with pytest.raises(groups.GroupError):
        groups.create(tmp_path, "Б", color="red")


def test_ids_are_not_reused(tmp_path, monkeypatch):
    tokens = iter(["aaaaaaaa", "bbbbbbbb", "cccccccc"])
    monkeypatch.setattr(groups.secrets, "token_hex", lambda n: next(tokens))
    # g-aaaaaaaa ещё лежит в meta.json встреч (группу удалили) — выдаём другой
    assert groups.create(tmp_path, "А", taken={"g-aaaaaaaa"})["id"] == "g-bbbbbbbb"
    assert groups.create(tmp_path, "Б")["id"] == "g-cccccccc"


def test_restore_with_the_same_id_and_place(tmp_path):
    a = groups.create(tmp_path, "А")
    b = groups.create(tmp_path, "Б")
    c = groups.create(tmp_path, "В")
    gone = groups.delete(tmp_path, b["id"])
    assert gone == {"group": b, "index": 1}
    back = groups.create(tmp_path, "Б", color=b["color"], gid=b["id"], index=1, created_at="2020-01-02T03:04:05")
    assert back["id"] == b["id"] and back["created_at"] == "2020-01-02T03:04:05"  # время создания — прежнее
    with pytest.raises(groups.GroupError):
        groups.create(tmp_path, "Д", created_at="вчера")
    assert [g["id"] for g in groups.load(tmp_path)] == [a["id"], b["id"], c["id"]]
    with pytest.raises(groups.GroupError):
        groups.create(tmp_path, "Г", gid=a["id"])  # такой id уже есть
    with pytest.raises(groups.GroupError):
        groups.create(tmp_path, "Г", gid="НЕ ТАК")
    # «Назвать» неизвестную группу: тот же id, в конец
    named = groups.create(tmp_path, "Найденная", gid="g-0000abcd", index=99)
    assert groups.load(tmp_path)[-1] == named


def test_update_and_unknown_group(tmp_path):
    a = groups.create(tmp_path, "А")
    groups.create(tmp_path, "Б")
    got = groups.update(tmp_path, a["id"], name="Альфа", color="#112233")
    assert (got["name"], got["color"], got["created_at"]) == ("Альфа", "#112233", a["created_at"])
    assert groups.update(tmp_path, a["id"], name="альфа")["name"] == "альфа"  # своё имя — можно
    with pytest.raises(groups.GroupError):
        groups.update(tmp_path, a["id"], name="б")
    with pytest.raises(groups.GroupError):
        groups.update(tmp_path, a["id"])
    with pytest.raises(groups.NoGroup):
        groups.update(tmp_path, "g-ffffffff", name="Х")
    with pytest.raises(groups.NoGroup):
        groups.delete(tmp_path, "g-ffffffff")


def test_reorder(tmp_path):
    a, b, c = (groups.create(tmp_path, n)["id"] for n in "АБВ")
    got = groups.reorder(tmp_path, [c, a])
    assert [g["id"] for g in got["groups"]] == [c, a, b] and got["changed"] is True  # недостающие — в конец
    assert [g["id"] for g in groups.load(tmp_path)] == [c, a, b]
    before = (tmp_path / groups.FILE).stat().st_mtime_ns
    same = groups.reorder(tmp_path, [c, a, b])
    assert same["changed"] is False and [g["id"] for g in same["groups"]] == [c, a, b]
    assert (tmp_path / groups.FILE).stat().st_mtime_ns == before  # тот же порядок — без записи
    with pytest.raises(groups.GroupError):
        groups.reorder(tmp_path, [a, "g-ffffffff"])
    with pytest.raises(groups.GroupError):
        groups.reorder(tmp_path, [a, a])
    with pytest.raises(groups.GroupError):
        groups.reorder(tmp_path, "abc")


def test_broken_file_is_set_aside_not_overwritten(tmp_path):
    (tmp_path / groups.FILE).write_text("{не json", encoding="utf-8")
    assert groups.load(tmp_path) == []
    assert groups.state(tmp_path) == {"items": [], "broken": True, "newer": False}
    assert (tmp_path / groups.FILE).read_text(encoding="utf-8") == "{не json"  # чтение не трогает
    got = groups.create(tmp_path, "А")
    aside = list(tmp_path.glob(groups.FILE + ".broken-*"))
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == "{не json"
    assert got["moved_broken"] == str(aside[0])  # первая запись говорит, куда отложен
    assert [g["name"] for g in groups.load(tmp_path)] == ["А"]
    assert groups.state(tmp_path)["broken"] is False
    assert "moved_broken" not in groups.create(tmp_path, "Б")


@pytest.mark.parametrize("content", ["[]", '{"groups": "нет"}', "", '{"version": 1}'])
def test_wrong_shape_is_broken_too(tmp_path, content):
    (tmp_path / groups.FILE).write_text(content, encoding="utf-8")
    assert groups.state(tmp_path)["broken"] is True


def test_broken_copies_never_overwrite_each_other(tmp_path, monkeypatch):
    class Frozen(groups.datetime):
        @classmethod
        def now(cls, tz=None):
            return groups.datetime(2026, 10, 6, 12, 0, 0)

    monkeypatch.setattr(groups, "datetime", Frozen)
    for text in ("{первый", "{второй"):
        (tmp_path / groups.FILE).write_text(text, encoding="utf-8")
        groups.create(tmp_path, f"Группа {text}")
    copies = groups.broken_copies(tmp_path)
    assert len(copies) == 2  # одна и та же секунда — разные имена
    assert sorted(c.read_text(encoding="utf-8") for c in copies) == ["{второй", "{первый"]
    (tmp_path / groups.FILE).write_text("{третий", encoding="utf-8")
    assert groups.state(tmp_path)["broken_copy"] == str(copies[0])


def test_busy_file_is_retried_and_never_set_aside(tmp_path, monkeypatch):
    a = groups.create(tmp_path, "Альфа")
    b = groups.create(tmp_path, "Бета")
    real = type(tmp_path).read_text
    fails = {"left": 1}

    def flaky(self, *args, **kwargs):
        if self.name == groups.FILE and fails["left"] > 0:
            fails["left"] -= 1
            raise PermissionError(32, "файл занят другим процессом")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "read_text", flaky)
    monkeypatch.setattr(groups.time, "sleep", lambda s: None)
    c = groups.create(tmp_path, "Вера")  # одна неудача — повтор, и всё на месте
    assert [g["id"] for g in groups.load(tmp_path)] == [a["id"], b["id"], c["id"]]
    assert "moved_broken" not in c and groups.broken_copies(tmp_path) == []
    # занят всё время — ошибка, файл не тронут, ничего не записано
    fails["left"] = 10**6
    before = (tmp_path / groups.FILE).read_bytes()
    with pytest.raises(groups.Busy):
        groups.create(tmp_path, "Гамма")
    with pytest.raises(groups.Busy):
        groups.state(tmp_path)
    fails["left"] = 0
    assert (tmp_path / groups.FILE).read_bytes() == before
    assert groups.broken_copies(tmp_path) == []
    assert [g["name"] for g in groups.load(tmp_path)] == ["Альфа", "Бета", "Вера"]


def test_unknown_fields_survive_and_newer_files_are_read_only(tmp_path):
    (tmp_path / groups.FILE).write_text(json.dumps({"version": 1, "owner": "кто-то", "groups": [
        {"id": "g-1", "name": "А", "color": "#000000", "created_at": "2026-01-01T00:00:00", "parent": "g-0"}]}),
        encoding="utf-8")
    groups.create(tmp_path, "Б")
    data = _file(tmp_path)
    assert data["owner"] == "кто-то" and data["groups"][0]["parent"] == "g-0"
    newer = {"version": 2, "groups": [{"id": "g-1", "name": "А", "color": "#000000", "created_at": "x"}]}
    (tmp_path / groups.FILE).write_text(json.dumps(newer), encoding="utf-8")
    assert [g["id"] for g in groups.load(tmp_path)] == ["g-1"]  # читать можно
    assert groups.state(tmp_path)["newer"] is True
    for action in (lambda: groups.create(tmp_path, "В"), lambda: groups.update(tmp_path, "g-1", name="Х"),
                   lambda: groups.delete(tmp_path, "g-1"), lambda: groups.reorder(tmp_path, ["g-1"])):
        with pytest.raises(groups.GroupError, match="новой версией"):
            action()
    assert _file(tmp_path) == newer  # не переписан как v1


def test_malformed_entries_are_skipped(tmp_path):
    (tmp_path / groups.FILE).write_text(json.dumps({"version": 1, "groups": [
        {"id": "g-1", "name": "Годная", "color": "#000000", "created_at": "x"},
        {"id": "НЕ ТАК", "name": "Плохой id"}, {"id": "g-2"}, "строка",
        {"id": "g-1", "name": "Повтор"}, {"id": "g-3", "name": "Без цвета"}]}), encoding="utf-8")
    got = groups.load(tmp_path)
    assert [g["id"] for g in got] == ["g-1", "g-3"]
    assert groups.COLOR.match(got[1]["color"])


def test_concurrent_creates_are_not_lost(tmp_path):
    errors = []

    def make(i):
        try:
            groups.create(tmp_path, f"Группа {i}")
        except Exception as e:  # pragma: no cover - покажет причину
            errors.append(e)

    threads = [threading.Thread(target=make, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(groups.load(tmp_path)) == 12


# --- членство -------------------------------------------------------------------------


def test_members_add_remove_with_honest_partial_failure(tmp_path):
    a = _rec(tmp_path, "2026-10-01_10-00")
    b = _rec(tmp_path, "2026-10-02_10-00")
    library.write_meta(b, {"title": "Своё", "group": "g-other"})
    gid = groups.create(tmp_path, "Альфа")["id"]
    got = groups.members(tmp_path, gid, add=[a.name, b.name, "2030-01-01_00-00", "../x"])
    assert got["changed"] == [a.name, b.name]
    assert [f["id"] for f in got["failed"]] == ["2030-01-01_00-00", "../x"]
    # служебные папки (отложенные к удалению, на проверке) — не записи
    hidden = _rec(tmp_path, ".2026-10-03_10-00.deleting-1a2b3c4d")
    got_hidden = groups.members(tmp_path, gid, add=[hidden.name])
    assert got_hidden["changed"] == [] and [f["id"] for f in got_hidden["failed"]] == [hidden.name]
    assert "group" not in library.read_meta(hidden)
    assert all(f["error"] for f in got["failed"])
    # у встречи одна группа: прежняя («g-other») заменена
    assert library.read_meta(b) == {"title": "Своё", "group": gid}
    assert groups.members(tmp_path, gid, add=[a.name])["changed"] == []  # уже в группе
    got = groups.members(tmp_path, gid, remove=[a.name, b.name])
    assert got == {"changed": [a.name, b.name], "failed": []}
    assert library.read_meta(b) == {"title": "Своё"}
    assert "group" not in library.read_meta(a)


def test_adding_to_another_group_moves_the_meeting(tmp_path):
    a = _rec(tmp_path, "2026-10-01_10-00")
    alpha = groups.create(tmp_path, "Альфа")["id"]
    beta = groups.create(tmp_path, "Бета")["id"]
    groups.members(tmp_path, alpha, add=[a.name])
    assert groups.members(tmp_path, beta, add=[a.name])["changed"] == [a.name]
    assert library.describe(a).group == beta  # из «Альфы» ушла
    # убрать из группы, где встречи нет, — ничего не меняет
    assert groups.members(tmp_path, alpha, remove=[a.name]) == {"changed": [], "failed": []}
    assert library.read_meta(a)["group"] == beta


def test_legacy_groups_list_is_read_leniently_and_rewritten(tmp_path):
    a = _rec(tmp_path, "2026-10-01_10-00")
    library.write_meta(a, {"groups": ["НЕ ТАК", "g-first", "g-second"]})
    assert groups.of(library.read_meta(a)) == "g-first"
    assert library.describe(a).to_raw()["group"] == "g-first"
    beta = groups.create(tmp_path, "Бета")["id"]
    groups.members(tmp_path, beta, add=[a.name])
    assert library.read_meta(a) == {"group": beta}  # пишется всегда `group`
    library.write_meta(a, {"group": "НЕ ТАК"})
    assert groups.of(library.read_meta(a)) is None


def test_members_of_unknown_groups(tmp_path):
    a = _rec(tmp_path, "2026-10-01_10-00")
    library.write_meta(a, {"group": "g-lost"})
    with pytest.raises(groups.GroupError):
        groups.members(tmp_path, "g-lost", add=[a.name])  # в неизвестную — нельзя
    # «Убрать из встреч» неизвестную группу — можно
    assert groups.members(tmp_path, "g-lost", remove=[a.name])["changed"] == [a.name]
    with pytest.raises(groups.GroupError):
        groups.members(tmp_path, "НЕ ТАК", remove=[a.name])
    with pytest.raises(groups.GroupError):
        groups.members(tmp_path, "g-lost", add="строка")
    with pytest.raises(groups.GroupError):
        groups.members(tmp_path, "g-lost")  # нечего делать


def test_deleting_a_group_keeps_meta(tmp_path):
    a = _rec(tmp_path, "2026-10-01_10-00")
    gid = groups.create(tmp_path, "Альфа")["id"]
    groups.members(tmp_path, gid, add=[a.name])
    groups.delete(tmp_path, gid)
    assert library.read_meta(a)["group"] == gid


def test_summary_counts_known_and_unknown():
    known = [{"id": "g-a", "name": "А", "color": "#000000", "created_at": "x"},
             {"id": "g-b", "name": "Б", "color": "#000000", "created_at": "x"}]
    cards = [{"group": "g-a"}, {"group": "g-a"}, {"group": "g-x"}, {"group": "g-y"}, {"group": "g-x"},
             {"group": None}, {}]
    got = groups.summary(known, cards)
    assert got["groups"] == [{"id": "g-a", "name": "А", "color": "#000000", "count": 2},
                             {"id": "g-b", "name": "Б", "color": "#000000", "count": 0}]
    assert got["unknown"] == [{"id": "g-x", "count": 2}, {"id": "g-y", "count": 1}]
    assert got["none"] == 2  # без группы


def test_file_lock_serialises_threads(tmp_path):
    target = tmp_path / groups.FILE
    order = []

    def second():
        with library.file_lock(target):
            order.append("второй")

    with library.file_lock(target):
        order.append("первый")
        other = threading.Thread(target=second)
        other.start()
        other.join(timeout=0.3)
        assert other.is_alive()  # ждёт, пока первый не отпустит
        order.append("первый отпустил")
    other.join(timeout=5)
    assert order == ["первый", "первый отпустил", "второй"]


# --- объединение ------------------------------------------------------------------------


def test_merge_takes_the_group_of_the_first_part_that_has_one(tmp_path):
    a = _rec(tmp_path, "2026-10-01_10-00")
    b = _rec(tmp_path, "2026-10-01_11-00")
    c = _rec(tmp_path, "2026-10-01_12-00")
    library.write_meta(b, {"group": "g-b"})
    library.write_meta(c, {"group": "g-c"})
    # части по времени: a (без группы), b, c — берётся группа b
    target = merge.create(tmp_path, [c, a, b], keep_originals=True)
    assert library.read_meta(target)["group"] == "g-b" and "groups" not in library.read_meta(target)
    target = merge.create(tmp_path, [_rec(tmp_path, "2026-10-02_10-00"), a], keep_originals=True)
    assert "group" not in library.read_meta(target)


# --- резидент ---------------------------------------------------------------------------


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
    st = tray_control.TrayControl(tray.TrayApp())
    st.seen = []
    st.bus.subscribe(lambda e: st.seen.append(e.kind) if not e.kind.startswith("level") else None)
    return st


def _changed(st):
    n = st.seen.count(tray_control.GROUPS_CHANGED)
    st.seen.clear()
    return n


def test_group_routes_and_one_event_per_operation(state, tmp_path):
    root = tmp_path / "recordings"
    a = _rec(root, "2026-10-01_10-00")
    b = _rec(root, "2026-10-02_10-00")
    assert state.groups() == {"groups": [], "unknown": [], "none": 2, "scope": "library", "broken": False,
                              "newer": False}
    alpha = state.create_group({"name": "Альфа", "color": "#123456"})
    beta = state.create_group({"name": "Бета"})
    assert _changed(state) == 2
    got = state.group_members(alpha["id"], {"add": [a.name, b.name, "2030-01-01_00-00"]})
    assert got["changed"] == [a.name, b.name] and [f["id"] for f in got["failed"]] == ["2030-01-01_00-00"]
    assert _changed(state) == 1
    assert [s for s in state.seen if s.startswith("recording.")] == []
    info = state.groups()
    assert [(g["id"], g["count"]) for g in info["groups"]] == [(alpha["id"], 2), (beta["id"], 0)]
    assert info["none"] == 0
    assert state.recordings()["items"][0]["group"] == alpha["id"]  # карточка перечитана
    # перенос в другую группу — одно событие, «Альфа» пустеет
    assert state.group_members(beta["id"], {"add": [a.name]})["changed"] == [a.name]
    assert _changed(state) == 1
    assert [(g["id"], g["count"]) for g in state.groups()["groups"]] == [(alpha["id"], 1), (beta["id"], 1)]
    state.group_members(alpha["id"], {"add": [a.name]})
    _changed(state)
    assert state.patch_group(alpha["id"], {"name": "Альфа-2"})["name"] == "Альфа-2"
    assert [g["id"] for g in state.order_groups({"ids": [beta["id"], alpha["id"]]})["groups"]] == [
        beta["id"], alpha["id"]]
    assert _changed(state) == 2
    state.order_groups({"ids": [beta["id"], alpha["id"]]})  # тот же порядок — без события
    assert _changed(state) == 0
    gone = state.delete_group(alpha["id"])
    assert gone["index"] == 1 and _changed(state) == 1
    info = state.groups()
    assert info["unknown"] == [{"id": alpha["id"], "count": 2}]
    # отмена удаления: тот же id, место и время создания
    back = state.create_group({**gone["group"], "index": gone["index"]})
    assert back == gone["group"]
    assert [g["id"] for g in state.groups()["groups"]] == [beta["id"], alpha["id"]]
    # ничего не поменялось — события нет
    state.group_members(alpha["id"], {"add": [a.name]})
    assert _changed(state) == 1  # только от create_group выше


def test_group_route_errors(state, tmp_path):
    _rec(tmp_path / "recordings", "2026-10-01_10-00")
    with pytest.raises(control.BadRequest):
        state.create_group({"name": ""})
    with pytest.raises(control.BadRequest):
        state.create_group(None)
    with pytest.raises(control.BadRequest):
        state.group_members("g-ffffffff", {"add": ["2026-10-01_10-00"]})
    assert state.patch_group("g-ffffffff", {"name": "Х"}) == {"error": "группы нет"}
    assert state.delete_group("g-ffffffff") == {"error": "группы нет"}
    with pytest.raises(control.BadRequest):
        state.order_groups({"ids": ["g-ffffffff"]})
    assert _changed(state) == 0


def test_group_counts_among_found(state, tmp_path):
    root = tmp_path / "recordings"
    a = _rec(root, "2026-10-01_10-00")
    b = _rec(root, "2026-10-02_10-00")
    library.write_transcript(a, {"segments": [{"start": 0, "end": 1, "speaker": "Анна", "text": "Бюджет."}]})
    gid = state.create_group({"name": "Альфа"})["id"]
    state.group_members(gid, {"add": [a.name, b.name]})
    assert state.groups()["groups"][0]["count"] == 2
    found = state.groups("бюджет")
    assert found["scope"] == "search" and found["groups"][0]["count"] == 1
    # фильтр по группам на счётчики групп не влияет, остальные условия — да
    assert state.groups(filters={"groups": "g-other", "people": "Анна"})["groups"][0]["count"] == 1


def test_routes_exist():
    assert ("GET", "/groups") in control._ROUTES
    assert ("POST", "/groups") in control._ROUTES
    assert ("PUT", "/groups/order") in control._ROUTES
    paths = [(m, p.pattern) for m, p, _ in control._PATTERNS]
    assert ("PATCH", r"^/groups/([^/]+)$") in paths
    assert ("DELETE", r"^/groups/([^/]+)$") in paths
    assert ("POST", r"^/groups/([^/]+)/members$") in paths


def test_broken_file_through_the_resident(state, tmp_path):
    root = tmp_path / "recordings"
    _rec(root, "2026-10-01_10-00")
    (root / groups.FILE).write_text("{битый", encoding="utf-8")
    info = state.groups()
    assert info["broken"] is True and "broken_copy" not in info and info["groups"] == []
    seen = []
    state.bus.subscribe(lambda e: seen.append(e.data) if e.kind == tray_control.GROUPS_CHANGED else None)
    got = state.create_group({"name": "Альфа"})
    assert got["moved_broken"] and (root / groups.FILE).exists()
    assert seen[-1]["moved_broken"] == got["moved_broken"]
    assert state.groups()["broken"] is False
    (root / groups.FILE).write_text("{снова", encoding="utf-8")
    assert state.groups()["broken_copy"] == got["moved_broken"]


def test_busy_file_through_the_resident_is_503(state, tmp_path, monkeypatch):
    _rec(tmp_path / "recordings", "2026-10-01_10-00")
    state.create_group({"name": "Альфа"})

    def busy(*a, **k):
        raise groups.Busy(groups.BUSY)

    monkeypatch.setattr(groups, "_read", busy)
    with pytest.raises(control.Unavailable):
        state.create_group({"name": "Бета"})
    with pytest.raises(control.Unavailable):
        state.groups()


def test_undo_keeps_every_field_of_the_group(state, tmp_path):
    root = tmp_path / "recordings"
    _rec(root, "2026-10-01_10-00")
    (root / groups.FILE).write_text(json.dumps({"version": 1, "groups": [
        {"id": "g-1", "name": "Альфа", "color": "#123456", "created_at": "2025-01-01T00:00:00",
         "parent": "g-0", "pinned": True}]}), encoding="utf-8")
    gone = state.delete_group("g-1")
    assert gone["group"]["parent"] == "g-0"
    back = state.create_group({**gone["group"], "index": gone["index"]})
    assert back == gone["group"]  # и будущие поля (parent у дерева групп) — как были
    assert _file(root)["groups"] == [gone["group"]]
    # новая группа чужих полей из тела не берёт
    fresh = state.create_group({"name": "Бета", "parent": "g-1"})
    assert "parent" not in fresh
