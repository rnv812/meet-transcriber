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
    back = groups.create(tmp_path, "Б", color=b["color"], gid=b["id"], index=1)
    assert back["id"] == b["id"]
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
    assert [g["id"] for g in groups.reorder(tmp_path, [c, a])] == [c, a, b]  # недостающие — в конец
    assert [g["id"] for g in groups.load(tmp_path)] == [c, a, b]
    with pytest.raises(groups.GroupError):
        groups.reorder(tmp_path, [a, "g-ffffffff"])
    with pytest.raises(groups.GroupError):
        groups.reorder(tmp_path, [a, a])
    with pytest.raises(groups.GroupError):
        groups.reorder(tmp_path, "abc")


def test_broken_file_is_set_aside_not_overwritten(tmp_path):
    (tmp_path / groups.FILE).write_text("{не json", encoding="utf-8")
    assert groups.load(tmp_path) == []
    assert (tmp_path / groups.FILE).read_text(encoding="utf-8") == "{не json"  # чтение не трогает
    groups.create(tmp_path, "А")
    aside = list(tmp_path.glob(groups.FILE + ".broken-*"))
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == "{не json"
    assert [g["name"] for g in groups.load(tmp_path)] == ["А"]


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
    library.write_meta(b, {"title": "Своё", "groups": ["g-other"]})
    gid = groups.create(tmp_path, "Альфа")["id"]
    got = groups.members(tmp_path, gid, add=[a.name, b.name, "2030-01-01_00-00", "../x"])
    assert got["changed"] == [a.name, b.name]
    assert [f["id"] for f in got["failed"]] == ["2030-01-01_00-00", "../x"]
    assert all(f["error"] for f in got["failed"])
    assert library.read_meta(b) == {"title": "Своё", "groups": ["g-other", gid]}
    assert groups.members(tmp_path, gid, add=[a.name])["changed"] == []  # уже в группе
    got = groups.members(tmp_path, gid, remove=[a.name, b.name])
    assert got == {"changed": [a.name, b.name], "failed": []}
    assert library.read_meta(b)["groups"] == ["g-other"]
    assert "groups" not in library.read_meta(a)


def test_members_of_unknown_groups(tmp_path):
    a = _rec(tmp_path, "2026-10-01_10-00")
    library.write_meta(a, {"groups": ["g-lost"]})
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
    assert library.read_meta(a)["groups"] == [gid]


def test_summary_counts_known_and_unknown():
    known = [{"id": "g-a", "name": "А", "color": "#000000", "created_at": "x"},
             {"id": "g-b", "name": "Б", "color": "#000000", "created_at": "x"}]
    cards = [{"groups": ["g-a", "g-x"]}, {"groups": ["g-a"]}, {"groups": ["g-y"]}, {"groups": ["g-x"]}, {}]
    got = groups.summary(known, cards)
    assert got["groups"] == [{"id": "g-a", "name": "А", "color": "#000000", "count": 2},
                             {"id": "g-b", "name": "Б", "color": "#000000", "count": 0}]
    assert got["unknown"] == [{"id": "g-x", "count": 2}, {"id": "g-y", "count": 1}]


def test_file_lock_is_public_and_reentrant_free(tmp_path):
    with library.file_lock(tmp_path / groups.FILE):
        pass
    library.write_meta(_rec(tmp_path, "2026-10-01_10-00"), {"title": "ок"})


# --- объединение ------------------------------------------------------------------------


def test_merge_unions_the_groups_of_its_parts(tmp_path):
    a = _rec(tmp_path, "2026-10-01_10-00")
    b = _rec(tmp_path, "2026-10-01_11-00")
    c = _rec(tmp_path, "2026-10-01_12-00")
    library.write_meta(a, {"groups": ["g-a", "g-b"]})
    library.write_meta(b, {"groups": ["g-b", "g-c"]})
    target = merge.create(tmp_path, [b, a, c], keep_originals=True)
    assert library.read_meta(target)["groups"] == ["g-a", "g-b", "g-c"]
    target = merge.create(tmp_path, [_rec(tmp_path, "2026-10-02_10-00"), c], keep_originals=True)
    assert "groups" not in library.read_meta(target)


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
    assert state.groups() == {"groups": [], "unknown": [], "scope": "library"}
    alpha = state.create_group({"name": "Альфа", "color": "#123456"})
    beta = state.create_group({"name": "Бета"})
    assert _changed(state) == 2
    got = state.group_members(alpha["id"], {"add": [a.name, b.name, "2030-01-01_00-00"]})
    assert got["changed"] == [a.name, b.name] and [f["id"] for f in got["failed"]] == ["2030-01-01_00-00"]
    assert _changed(state) == 1
    assert [s for s in state.seen if s.startswith("recording.")] == []
    info = state.groups()
    assert [(g["id"], g["count"]) for g in info["groups"]] == [(alpha["id"], 2), (beta["id"], 0)]
    assert state.recordings()["items"][0]["groups"] == [alpha["id"]]  # карточка перечитана
    assert state.patch_group(alpha["id"], {"name": "Альфа-2"})["name"] == "Альфа-2"
    assert [g["id"] for g in state.order_groups({"ids": [beta["id"], alpha["id"]]})["groups"]] == [
        beta["id"], alpha["id"]]
    assert _changed(state) == 2
    gone = state.delete_group(alpha["id"])
    assert gone["index"] == 1 and _changed(state) == 1
    info = state.groups()
    assert info["unknown"] == [{"id": alpha["id"], "count": 2}]
    # отмена удаления: тот же id и место
    state.create_group({"id": alpha["id"], "name": "Альфа-2", "color": "#123456", "index": 1})
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
