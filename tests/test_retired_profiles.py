"""Уборка после профилей людей (убраны в 0.3.2): заметки — в один файл,
сгенерированное и служебное — удалить, голоса не трогать, повтор безвреден.
Папка данных — синтетическая; люди и тексты выдуманы."""

import json
import os
import time

from meet import retired_profiles, settings

PID_A = "0123456789abcdef"
PID_B = "fedcba9876543210"
PID_C = "aaaaaaaaaaaaaaaa"


def _voice(voices, name, pid=None):
    data = {"samples": [{"embedding": [0.1, 0.2], "source": "x", "id": "s1"}]}
    if pid:
        data["id"] = pid
    path = voices / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _world(tmp_path):
    data, voices = tmp_path / "data", tmp_path / "voices"
    voices.mkdir(parents=True)
    profiles = data / "profiles"
    (profiles / "_index").mkdir(parents=True)
    _voice(voices, "Вера Соколова", PID_A)
    _voice(voices, "Вадим", PID_B)
    _voice(voices, "Без профиля")
    (profiles / f"{PID_A}.json").write_text('{"version": 1, "summary": "сгенерировано"}', encoding="utf-8")
    (profiles / f"{PID_A}.notes.md").write_text("Любит повестку заранее.\n", encoding="utf-8")
    (profiles / f"{PID_A}.state.json").write_text('{"pending": {"at": 1, "manual": false}}', encoding="utf-8")
    (profiles / f"{PID_B}.json").write_text('{"version": 1}', encoding="utf-8")
    (profiles / f"{PID_B}.notes.md").write_text("  \n", encoding="utf-8")  # пустые — не заметки
    (profiles / f"{PID_C}.notes.md").write_text("Человека уже нет в базе.", encoding="utf-8")
    (profiles / "_index" / "2026-09-30_16-04.json").write_text("{}", encoding="utf-8")
    (profiles / "pcm-eval.json").write_text("{}", encoding="utf-8")
    when = time.mktime((2026, 10, 2, 12, 0, 0, 0, 0, -1))
    os.utime(profiles / f"{PID_A}.notes.md", (when, when))
    config = data / "config.json"
    config.write_text(json.dumps({"llm": {"provider": "codex"},
                                  "profiles": {"enabled": True, "pcm": True}}), encoding="utf-8")
    (data / "llm_stats.json").write_text(json.dumps({
        "profile:claude-code": [{"in": 1, "s": 2, "out": 3}],
        "profile-check:claude-code": [{"in": 1, "s": 2, "out": 3}],
        "analyze:claude-code": [{"in": 1, "s": 2, "out": 3}]}), encoding="utf-8")
    return data, voices, config


def _snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_notes_extracted_everything_else_removed(tmp_path):
    data, voices, config = _world(tmp_path)
    before = _snapshot(voices)

    got = retired_profiles.run(data, voices, config=config)

    notes = data / retired_profiles.NOTES_NAME
    assert got == {"notes": str(notes), "folder": str(data)}
    text = notes.read_text(encoding="utf-8")
    assert text.startswith("# Заметки о людях (из профилей)\n")
    assert "## Вера Соколова\n\n_Заметка от 02.10.2026_\n\nЛюбит повестку заранее." in text
    assert f"## Без имени ({PID_C[:6]})" in text and "Человека уже нет в базе." in text
    assert "Вадим" not in text  # пустая заметка не переносится
    assert "сгенерировано" not in text
    assert text.index("Без имени") < text.index("Вера Соколова")
    # Папка профилей — целиком, с индексом, отметками pending и служебным.
    assert not (data / "profiles").exists()
    # Голоса не тронуты (и id в них остаётся).
    assert _snapshot(voices) == before
    # Секция настроек и замеры задач профилей убраны, остальное на месте.
    assert json.loads(config.read_text(encoding="utf-8")) == {"llm": {"provider": "codex"}}
    assert list(json.loads((data / "llm_stats.json").read_text(encoding="utf-8"))) == ["analyze:claude-code"]
    assert retired_profiles.notice(data) == got


def test_without_notes_no_file_but_notice(tmp_path):
    data = tmp_path / "data"
    (data / "profiles" / "_index").mkdir(parents=True)
    (data / "profiles" / f"{PID_A}.json").write_text("{}", encoding="utf-8")
    got = retired_profiles.run(data, tmp_path / "нет-голосов", config=data / "config.json")
    assert got == {"notes": None, "folder": str(data)}
    assert not (data / "profiles").exists()
    assert not list(data.glob("*.md"))
    assert not (data / "config.json").exists()  # файла настроек не было — и не появился


def test_idempotent_and_dismiss(tmp_path):
    data, voices, config = _world(tmp_path)
    first = retired_profiles.run(data, voices, config=config)
    notes = data / retired_profiles.NOTES_NAME
    written = notes.read_bytes()
    # Второй запуск: папки нет — ничего не делает, файл заметок и отметка те же.
    assert retired_profiles.run(data, voices, config=config) is None
    assert notes.read_bytes() == written
    assert retired_profiles.notice(data) == first
    retired_profiles.dismiss(data)
    assert retired_profiles.notice(data) is None
    assert retired_profiles.run(data, voices, config=config) is None
    assert retired_profiles.notice(data) is None
    assert notes.read_bytes() == written


def test_no_profiles_folder_touches_nothing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    config = data / "config.json"
    config.write_text('{"profiles": {"enabled": true}}', encoding="utf-8")
    assert retired_profiles.run(data, None, config=config) is None
    assert config.read_text(encoding="utf-8") == '{"profiles": {"enabled": true}}'
    assert retired_profiles.notice(data) is None


def test_existing_notes_file_not_overwritten(tmp_path):
    data, voices, config = _world(tmp_path)
    mine = data / retired_profiles.NOTES_NAME
    mine.write_text("свой файл человека", encoding="utf-8")
    got = retired_profiles.run(data, voices, config=config)
    assert mine.read_text(encoding="utf-8") == "свой файл человека"
    assert got["notes"] == str(data / "Заметки о людях (из профилей) (2).md")
    assert "Любит повестку заранее." in (data / "Заметки о людях (из профилей) (2).md").read_text(encoding="utf-8")


def test_partial_previous_run_reuses_same_notes_file(tmp_path):
    """Прошлый запуск записал заметки, но папку удалить не успел: тот же файл,
    без второго."""
    data, voices, config = _world(tmp_path)
    notes = retired_profiles.collect_notes(data / "profiles", voices)
    (data / retired_profiles.NOTES_NAME).write_text(retired_profiles.notes_text(notes), encoding="utf-8")
    got = retired_profiles.run(data, voices, config=config)
    assert got["notes"] == str(data / retired_profiles.NOTES_NAME)
    assert sorted(p.name for p in data.glob("*.md")) == [retired_profiles.NOTES_NAME]


def test_notes_kept_in_place_when_they_cannot_be_written(tmp_path, monkeypatch):
    data, voices, config = _world(tmp_path)
    lines = []

    def broken(path, text):
        raise OSError("диск полон")

    with monkeypatch.context() as mp:
        mp.setattr(retired_profiles, "_write_text", broken)
        got = retired_profiles.run(data, voices, config=config, log=lines.append)
    assert got is None  # и отметку не записать
    # Сгенерированное удалено, непустые заметки остались до следующего запуска.
    assert sorted(p.name for p in (data / "profiles").iterdir()) == sorted(
        [f"{PID_A}.notes.md", f"{PID_C}.notes.md"])
    assert any("заметки не перенесены" in line for line in lines)
    got = retired_profiles.run(data, voices, config=config)
    assert got["notes"] == str(data / retired_profiles.NOTES_NAME)
    assert not (data / "profiles").exists()


def test_old_config_with_profiles_section_loads_and_is_dropped_on_save(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"profiles": {"enabled": True, "pcm": True}, "my_key": 1}), encoding="utf-8")
    cfg = settings.load(path)
    assert not hasattr(cfg, "profiles")
    assert "profiles" not in cfg.to_raw()
    settings.save(cfg, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "profiles" not in raw and raw["my_key"] == 1


def test_patch_ignores_profiles_section(tmp_path):
    path = tmp_path / "config.json"
    updated = settings.patch({"profiles": {"enabled": True}}, path)
    assert "profiles" not in updated.to_raw()
    assert "profiles" not in json.loads(path.read_text(encoding="utf-8"))


def test_resident_api_notice_and_dismiss(tmp_path, monkeypatch):
    from meet import tray_control

    data, voices, config = _world(tmp_path)
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    retired_profiles.run(data, voices, config=config)
    state = tray_control.TrayControl.__new__(tray_control.TrayControl)
    assert state.profiles_removed() == {"notice": {"notes": str(data / retired_profiles.NOTES_NAME),
                                                   "folder": str(data)}}
    assert state.dismiss_profiles_removed() == {"ok": True}
    assert state.profiles_removed() == {"notice": None}


def test_resident_start_cleans_up(tmp_path, monkeypatch):
    from meet import tray

    data, voices, config = _world(tmp_path)
    raw = json.loads(config.read_text(encoding="utf-8"))
    raw["recording"] = {"voices_dir": str(voices)}
    config.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    lines = []
    app = tray.TrayApp()
    monkeypatch.setattr(app, "log", lines.append)
    app._retire_profiles()
    assert not (data / "profiles").exists()
    assert "## Вера Соколова" in (data / retired_profiles.NOTES_NAME).read_text(encoding="utf-8")
    assert "profiles" not in json.loads(config.read_text(encoding="utf-8"))
    assert any("профили людей убраны" in line for line in lines)
