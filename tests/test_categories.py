"""Категории встреч: категория от модели по правилам, выбор человека, API,
CLI `meet category`, категория во frontmatter выгрузки.

Модель не вызывается; данные выдуманы.
"""

import json
import threading
from pathlib import Path

import pytest

import meet.llm as llm
from meet import analysis, categories, control, export, jobs, kb_export, library, merge, settings, tray, tray_control
from meet.llm import detect

RID = "2026-10-01_09-30"


# --- правила: meet.categories --------------------------------------------------------


def _cfg(**analysis_flags):
    cfg = settings.Settings()
    if analysis_flags:
        from dataclasses import replace

        cfg = replace(cfg, analysis=replace(cfg.analysis, **analysis_flags))
    return cfg


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / RID
    path.mkdir()
    (path / "sys.opus").write_bytes(b"x")
    return path


def _doc(cid="daily", confidence=0.8):
    return {"category": {"id": cid, "confidence": confidence}}


def test_ai_category_is_set_from_a_confident_known_id(folder):
    assert categories.apply_ai(folder, _doc(), _cfg()) is True
    assert library.read_meta(folder)["category"] == {"id": "daily", "source": "ai", "confidence": 0.8}
    assert library.describe(folder).category == {"id": "daily", "source": "ai"}


@pytest.mark.parametrize("doc", [
    _doc(confidence=0.49), _doc(cid="unknown"), {"category": None},
    {"category": {"id": "daily", "confidence": "много"}},
])
def test_low_confidence_or_unknown_id_is_no_category(folder, doc):
    assert categories.apply_ai(folder, doc, _cfg()) is False
    assert "category" not in library.read_meta(folder)
    assert library.describe(folder).category is None


def test_threshold_is_inclusive(folder):
    assert categories.apply_ai(folder, _doc(confidence=0.5), _cfg()) is True


def test_new_analysis_refines_or_clears_the_ai_category(folder):
    categories.apply_ai(folder, _doc(), _cfg())
    assert categories.apply_ai(folder, _doc("planning", 0.9), _cfg()) is True
    assert library.read_meta(folder)["category"]["id"] == "planning"
    assert categories.apply_ai(folder, _doc("planning", 0.2), _cfg()) is True
    assert "category" not in library.read_meta(folder)


def test_analysis_without_an_answer_keeps_the_ai_category(folder):
    """`category: null` — модель не ответила (сбой итогового вызова): прежняя
    категория остаётся, как название."""
    categories.apply_ai(folder, _doc(), _cfg())
    assert categories.apply_ai(folder, {"category": None}, _cfg()) is False
    assert library.read_meta(folder)["category"]["id"] == "daily"


def test_missing_confidence_is_stored_as_half_and_assigned(folder):
    """Уверенность, которую модель не назвала, анализ пишет как 0.5 (порог
    включительный) — категория ставится."""
    found = analysis._category({"id": "daily"}, {"daily"})
    assert found == {"id": "daily", "confidence": 0.5}
    assert categories.apply_ai(folder, {"category": found}, _cfg()) is True


def test_reserved_name_is_not_a_category():
    got = settings.as_categories([{"id": "x", "name": "Без  категории"}, {"id": "y", "name": "Летучка"}])
    assert [c.id for c in got] == ["y"]


def test_analysis_without_category_changes_nothing(folder):
    categories.apply_ai(folder, _doc(), _cfg())
    assert categories.apply_ai(folder, {"chapters": []}, _cfg()) is False
    assert categories.apply_ai(folder, None, _cfg()) is False
    assert library.read_meta(folder)["category"]["id"] == "daily"


def test_toggle_off_assigns_nothing_but_manual_works(folder):
    assert categories.apply_ai(folder, _doc(), _cfg(category=False)) is False
    assert "category" not in library.read_meta(folder)
    assert categories.set_user(folder, "retro") is True
    assert library.describe(folder).category == {"id": "retro", "source": "user"}


@pytest.mark.parametrize("chosen", ["retro", None])
def test_user_choice_is_never_overwritten(folder, chosen):
    categories.set_user(folder, chosen)
    assert categories.apply_ai(folder, _doc("planning", 0.99), _cfg()) is False
    assert categories.apply_ai(folder, _doc(confidence=0.1), _cfg()) is False
    assert library.read_meta(folder)["category"] == {"id": chosen, "source": "user"}


def test_user_choice_made_during_the_write_wins(folder, monkeypatch):
    """Человек выбрал категорию между решением модели и записью: проверка —
    под замком meta.json, как у названия."""
    real = library.update_meta

    def racing(path, change):
        if not racing.done:
            racing.done = True
            categories.set_user(path, "retro")
        return real(path, change)

    racing.done = False
    monkeypatch.setattr(library, "update_meta", racing)
    assert categories.apply_ai(folder, _doc(), _cfg()) is False
    assert library.read_meta(folder)["category"] == {"id": "retro", "source": "user"}


def test_manual_none_is_kept_as_user_choice(folder):
    categories.set_user(folder, None)
    assert library.describe(folder).category == {"id": None, "source": "user"}
    assert categories.set_user(folder, None) is False


@pytest.mark.parametrize("raw", [None, "daily", {"id": "daily"}, {"id": "daily", "source": "кто-то"},
                                 {"id": None, "source": "ai"}, {"id": "", "source": "ai"}])
def test_broken_meta_is_no_category(raw):
    assert categories.of({"category": raw}) is None


def test_resolve_by_id_or_name():
    cfg = _cfg()
    assert categories.resolve(cfg, "client") == "client"
    assert categories.resolve(cfg, "  встреча   С КЛИЕНТОМ ") == "client"
    assert categories.resolve(cfg, "Дейлик") == "daily"
    assert categories.resolve(cfg, "Летучка") is None
    assert categories.resolve(cfg, " ") is None


def test_deleted_category_reads_as_none(folder):
    from dataclasses import replace

    categories.set_user(folder, "retro")
    cfg = _cfg()
    assert categories.display_name(folder, cfg) == "Ретроспектива"
    without = replace(cfg, categories=tuple(c for c in cfg.categories if c.id != "retro"))
    assert categories.display_name(folder, without) is None
    assert library.read_meta(folder)["category"]["id"] == "retro"  # «Сбросить к стандартным» вернёт её


def test_counts(tmp_path):
    for n, cid in enumerate(["daily", "daily", "retro", None]):
        path = tmp_path / f"2026-10-0{n + 1}_10-00"
        path.mkdir()
        (path / "sys.opus").write_bytes(b"x")
        if cid:
            categories.set_user(path, cid)
    (tmp_path / "2026-10-09_10-00").mkdir()
    (tmp_path / "2026-10-09_10-00" / "sys.opus").write_bytes(b"x")
    assert categories.counts(tmp_path, _cfg()) == {"counts": {"daily": 2, "retro": 1}, "none": 2, "scope": "library"}


def test_merged_meeting_keeps_a_user_category(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    parts = []
    for name, cid in (("2026-10-01_10-00", None), ("2026-10-01_10-30", "client")):
        path = tmp_path / name
        path.mkdir()
        (path / "sys.opus").write_bytes(b"x")
        library.write_meta(path, {"source": "record"})
        if cid:
            categories.set_user(path, cid)
        parts.append(path)
    categories.apply_ai(parts[0], _doc("planning"), _cfg())
    order = {parts[0].name: 0, parts[1].name: 1}
    base = datetime(2026, 10, 1, 10, 0)
    monkeypatch.setattr(merge, "part_start", lambda f: base + timedelta(minutes=30 * order[Path(f).name]))
    target = merge.create(tmp_path, parts, keep_originals=True)
    assert library.read_meta(target)["category"] == {"id": "client", "source": "user"}


# --- выгрузка: категория во frontmatter ---------------------------------------------------


def _transcript(folder: Path, end: float = 900.0) -> None:
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 5.0, "speaker": "Ольга", "text": "Начнём с беты."},
        {"start": 5.0, "end": end, "speaker": "SPEAKER_01", "text": "Решили выпустить бету в пятницу."}]})


def test_markdown_frontmatter_has_the_category_name(folder):
    _transcript(folder)
    data = library.read_transcript(folder)
    md = export.render({**data, "title": "Бета"}, "md", date="2026-10-01", category="Дейлик")
    head = md.split("---")[1]
    assert "category: Дейлик" in head and head.index("type: transcript") < head.index("category:")
    plain = export.render({**data, "title": "Бета"}, "md", date="2026-10-01")
    assert "category" not in plain
    assert "Дейлик" not in export.render({**data, "title": "Бета"}, "txt", category="Дейлик")


def test_frontmatter_quotes_yaml_special_names():
    from meet import output

    assert output.yaml_text("Встреча с клиентом") == "Встреча с клиентом"
    assert output.yaml_text("Q&A: вопросы") == '"Q&A: вопросы"'
    assert output.yaml_text("- список") == '"- список"'
    for reserved in ("null", "Null", "~", "true", "yes", "No", "on", "123", "2024", "1.5", "-7",
                     "2024-01-01", ".inf", "0x1F"):
        assert output.yaml_text(reserved) == json.dumps(reserved), reserved
    assert output.yaml_text("Sales Q3") == "Sales Q3"


def test_kb_export_note_has_the_category(folder, tmp_path):
    from dataclasses import replace

    _transcript(folder)
    categories.set_user(folder, "client")
    cfg = _cfg()
    cfg = replace(cfg, export=replace(cfg.export, meetings_dir=str(tmp_path / "kb")))
    (tmp_path / "kb").mkdir()
    got = kb_export.export_recording(folder, cfg)
    note = next(Path(got["path"]).glob("*.md"))
    assert "category: Встреча с клиентом" in note.read_text(encoding="utf-8")


# --- резидент: API и анализ ------------------------------------------------------------


def _write_config(tmp_path, **sections) -> None:
    path = tmp_path / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(tmp_path / "recordings"),
                      "voices_dir": str(tmp_path / "voices")},
        "llm": {"provider": "auto"},
        "assistant": {"knowledge_dir": None, "notes_dir": None},
        "export": {"meetings_dir": None},
    }
    for name, value in sections.items():
        data[name] = {**data.get(name, {}), **value}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    _write_config(tmp_path)
    rec = tmp_path / "recordings" / RID
    rec.mkdir(parents=True)
    (rec / "sys.opus").write_bytes(b"x")
    _transcript(rec)
    app = tray.TrayApp()
    release = threading.Event()

    def spawn(job, on_line):
        release.wait(timeout=5)
        return 0

    queue = jobs.JobQueue(app.bus, spawn=spawn)
    llm_queue = jobs.JobQueue(app.bus, spawn=spawn)
    st = tray_control.TrayControl(app, queue=queue, llm_queue=llm_queue)
    st._background = lambda fn, name=None: fn()
    monkeypatch.setattr(detect, "available", lambda base_url=None, probe_local=True, via_proxy=False: {
        name: {"found": name == "claude-code"} for name in llm.PROVIDERS})
    try:
        yield st
    finally:
        release.set()
        queue.stop()
        llm_queue.stop()


def _rec(tmp_path) -> Path:
    return tmp_path / "recordings" / RID


def _write_analysis(folder: Path, category=None, fresh=True):
    data = library.read_transcript(folder)
    fp = analysis.fingerprint(data) if fresh else "старый"
    return analysis.write(folder, {"version": 1, "model": "fake", "created_at": 1.0, "fingerprint": fp,
                                   "features": ["category"], "category": category})


def test_finished_analysis_sets_the_category(state, tmp_path):
    seen = []
    state.bus.subscribe(lambda e: seen.append(e.data) if e.kind == tray_control.RECORDING_UPDATED else None)
    _write_analysis(_rec(tmp_path), {"id": "planning", "confidence": 0.7})
    state._analysis_finished(_rec(tmp_path), jobs.DONE)
    assert state.recording(RID)["category"] == {"id": "planning", "source": "ai"}
    assert {"id": RID} in seen


def test_stale_or_failed_analysis_sets_no_category(state, tmp_path):
    _write_analysis(_rec(tmp_path), {"id": "planning", "confidence": 0.7}, fresh=False)
    state._analysis_finished(_rec(tmp_path), jobs.DONE)
    assert state.recording(RID)["category"] is None
    _write_analysis(_rec(tmp_path), {"id": "planning", "confidence": 0.7})
    state._analysis_finished(_rec(tmp_path), jobs.FAILED)
    assert state.recording(RID)["category"] is None


def test_finished_analysis_respects_the_toggle_and_the_user(state, tmp_path):
    _write_config(tmp_path, analysis={"category": False})
    _write_analysis(_rec(tmp_path), {"id": "planning", "confidence": 0.7})
    state._analysis_finished(_rec(tmp_path), jobs.DONE)
    assert state.recording(RID)["category"] is None
    _write_config(tmp_path)
    state.set_category(RID, {"id": None})
    state._analysis_finished(_rec(tmp_path), jobs.DONE)
    assert state.recording(RID)["category"] == {"id": None, "source": "user"}


def test_set_and_clear_category_through_the_api(state, tmp_path):
    got = state.set_category(RID, {"id": "retro"})
    assert got["category"] == {"id": "retro", "source": "user"}
    assert state.recordings()["items"][0]["category"] == {"id": "retro", "source": "user"}
    got = state.set_category(RID, {"id": None})
    assert got["category"] == {"id": None, "source": "user"}
    with pytest.raises(control.BadRequest, match="такой категории нет"):
        state.set_category(RID, {"id": "летучка"})
    with pytest.raises(control.BadRequest):
        state.set_category(RID, {})
    assert state.set_category("../..", {"id": "retro"}) == {"error": "записи нет"}
    info = state.categories()
    assert info["counts"] == {} and info["none"] == 1
    assert [c["id"] for c in info["defaults"]][:2] == ["daily", "planning"]
    assert info["categories"] == info["defaults"]


def test_user_category_reexports_an_exported_meeting(state, tmp_path, monkeypatch):
    exported = []
    monkeypatch.setattr(kb_export, "previously_exported", lambda f: True)
    monkeypatch.setattr(state, "_auto_kb_export", lambda f: exported.append(Path(f).name))
    state.set_category(RID, {"id": "client"})
    state.set_category(RID, {"id": "client"})  # то же — выгружать нечего
    assert exported == [RID]


def test_window_export_has_the_category(state, tmp_path):
    state.set_category(RID, {"id": "daily"})
    assert "category: Дейлик" in state.export(RID, "md")["content"]


def test_category_routes_exist():
    paths = [(m, p.pattern) for m, p, _ in control._PATTERNS]
    assert ("PUT", r"^/recordings/([^/]+)/category$") in paths
    assert ("GET", "/categories") in control._ROUTES


def _many(tmp_path, n):
    """n записей по дням от 2025-01-01; самая старая — «Ретроспектива» вручную."""
    from datetime import date, timedelta

    root = tmp_path / "recordings"
    folders = []
    for i in range(n):
        path = root / f"{date(2025, 1, 1) + timedelta(days=i):%Y-%m-%d}_10-00"
        path.mkdir(parents=True, exist_ok=True)
        (path / "sys.opus").write_bytes(b"x")
        folders.append(path)
    categories.set_user(folders[0], "retro")
    return folders


def test_filter_finds_an_old_meeting_beyond_the_list_limit(state, tmp_path):
    folders = _many(tmp_path, 250)
    assert len(state.recordings()["items"]) == 200
    got = state.recordings(categories="retro")["items"]
    assert [r["id"] for r in got] == [folders[0].name]
    nothing = state.recordings(categories="_none")["items"]
    assert len(nothing) == 200 and folders[0].name not in {r["id"] for r in nothing}
    assert state.recordings(categories="gone")["items"] == []
    info = state.categories()
    assert info["counts"] == {"retro": 1} and info["none"] == 250 and info["scope"] == "library"


def test_filter_and_counts_with_a_search(state, tmp_path):
    folders = _many(tmp_path, 3)
    _transcript(folders[0])
    _transcript(folders[2])
    categories.set_user(folders[2], "daily")
    got = state.search("бету", categories="retro")["items"]
    assert [r["id"] for r in got] == [folders[0].name]
    assert [r["id"] for r in state.recordings(q="бету", categories="daily")["items"]] == [folders[2].name]
    info = state.categories("бету")
    assert info["scope"] == "search" and info["counts"] == {"retro": 1, "daily": 1}
    # Свой RID из фикстуры тоже с текстом про бету — «без категории».
    assert info["none"] == 1


# --- CLI: meet category ------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    rec = tmp_path / "rec"
    rec.mkdir()
    (data / "config.json").write_text(json.dumps({
        "version": 4, "recording": {"out_dir": str(rec), "voices_dir": str(tmp_path / "voices")},
        "llm": {"provider": "auto"},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    folder = rec / RID
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    _transcript(folder)
    return folder


def _main(argv):
    from meet import cli

    return cli.main(argv)


def test_cli_prints_sets_and_clears(env, capsys):
    assert _main(["category", RID]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Без категории\n") and "Дейлик" in out
    assert _main(["category", RID, "встреча с клиентом"]) == 0
    assert capsys.readouterr().out == "Категория: Встреча с клиентом\n"
    assert library.read_meta(env)["category"] == {"id": "client", "source": "user"}
    assert _main(["category", RID, "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["category"] == {"id": "client", "name": "Встреча с клиентом", "source": "user"}
    assert _main(["category", RID, "retro"]) == 0
    assert library.read_meta(env)["category"]["id"] == "retro"
    assert _main(["category", RID, "--clear"]) == 0
    assert capsys.readouterr().out.endswith("Категория: Без категории\n")
    assert library.read_meta(env)["category"] == {"id": None, "source": "user"}


def test_cli_unknown_category_is_exit_1(env, capsys):
    assert _main(["category", RID, "Летучка"]) == 1
    err = capsys.readouterr().err
    assert "Нет категории «Летучка»" in err and "Ретроспектива" in err
    assert _main(["category", RID, "retro", "--clear"]) == 1


def test_cli_goes_through_the_running_app(env, capsys, monkeypatch):
    calls = []

    def fake_request(path, method="GET", payload=None, timeout=5.0):
        calls.append((method, path, payload))
        categories.set_user(env, payload["id"])
        return {"id": RID, "category": {"id": payload["id"], "source": "user"}}

    monkeypatch.setattr(control, "alive", lambda *a, **k: True)
    monkeypatch.setattr(control, "request", fake_request)
    assert _main(["category", RID, "Обучение", "--json"]) == 0
    assert calls == [("PUT", f"/recordings/{RID}/category", {"id": "training"})]
    assert json.loads(capsys.readouterr().out)["via_app"] is True


def test_cli_analyze_applies_the_category_inline(env, capsys, monkeypatch):
    def fake_analyze(folder, runner, cfg, provider=None, bus=None):
        _write_analysis(folder, {"id": "planning", "confidence": 0.9})

    monkeypatch.setattr(analysis, "analyze", fake_analyze)
    from meet import cli_library

    monkeypatch.setattr(cli_library, "_model", lambda args, cfg: ("fake", object()))
    assert _main(["analyze", RID]) == 0
    assert library.read_meta(env)["category"]["id"] == "planning"
    assert "Категория встречи: Планирование" in capsys.readouterr().err
