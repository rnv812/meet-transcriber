"""Договор настроек (0.5, пункт 23): окно ↔ движок ↔ файл — по каждой настройке.

1. Каждая пара `раздел.ключ`, которую окно пишет через `set(...)` в
   `app/src`, есть в ответе `GET /settings` (окно не пишет в пустоту).
2. Каждый ключ схемы кто-то читает: движок вне `settings.py`, окно или оболочка
   (мёртвых настроек нет); исключения — списком с причиной.
3. Каждый ключ проходит круг «PATCH → config.json → чтение» на значении,
   отличном от умолчания, и возвращается назад.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from meet import settings

ROOT = Path(__file__).resolve().parents[1]
WINDOW = ROOT / "app" / "src"
SHELL = ROOT / "app" / "src-tauri" / "src"
ENGINE = ROOT / "src" / "meet"

SET_CALL = re.compile(r"""\bset\(\s*["']([a-z_]+)["']\s*,\s*["']([a-z_0-9]+)["']""")

# Ключи, которые читаются только внутри settings.py — и почему это не мёртвые настройки.
READ_IN_SETTINGS = {
    ("asr", "cpu_backend"): "движок на процессоре выбирает Asr.backend_for(device)",
    ("assistant", "notes_dir"): "прежняя папка заметок — из неё выводится export.meetings_dir",
    ("assistant", "notes_subdir"): "подпапка прежней папки заметок — туда же",
}

# Ведёт сам движок — через PATCH не задаётся (проверено отдельно).
ENGINE_OWNED = {
    ("recording", "former_speaker_names"): "прежние имена владельца копит сам при смене имени",
}

# Значение «не по умолчанию» для перечислений, путей и проверяемых полей (остальные — по типу).
VALUES = {
    ("recording", "out_dir"): str(Path("D:/meet-test/records")),
    ("recording", "voices_dir"): str(Path("D:/meet-test/voices")),
    ("recording", "mic_device"): {"name": "Микрофон (USB Audio)"},
    ("recording", "output_device"): {"name": "Динамики (Realtek)"},
    ("assist", "vault"): str(Path("D:/meet-test/vault")),
    ("assistant", "knowledge_dir"): str(Path("D:/meet-test/kb")),
    ("assistant", "notes_dir"): str(Path("D:/meet-test/notes")),
    ("export", "meetings_dir"): str(Path("D:/meet-test/meetings")),
    ("integrations", "gpu_marker_path"): str(Path("D:/meet-test/gpu.lock")),
    ("hooks", "recurring_window"): ["11:00", "12:00"],
    ("llm", "local_model"): "qwen3-8b",
    ("agent", "launch"): {"claude-code": {"args": "--verbose", "env": []},
                          "codex": {"args": "", "env": []}, "opencode": {"args": "", "env": []}},
    ("asr", "backend"): "gigaam",
    ("asr", "device"): "cpu",
    ("asr", "cpu_backend"): "faster-whisper",
    ("asr", "gigaam_model"): "v3_e2e_ctc",
    ("asr", "replacements"): [{"from": "кафка", "to": "Kafka"}],
    ("llm", "provider"): "codex",
    ("llm", "proxy"): "http://127.0.0.1:8080",
    ("llm", "opencode_model"): "anthropic/claude-sonnet",
    ("llm", "effort"): "high",
    ("llm", "codex_effort"): "high",
    ("assist", "activity"): "active",
    ("assist", "hints_model"): "fast",
    ("assist", "max_hints"): 5,
    ("assist", "live_asr"): "whisper",
    ("assist", "frequency"): "less",
    ("assist", "profile"): "personal",
    ("assist", "agent_mode"): "confirm",
    ("integrations", "jira_base_url"): "https://jira.example.com",
    ("integrations", "jira_projects"): [{"key": "ABC", "aliases": ["эй би си"]}],
    ("ui", "notifications"): "important",
    ("ui", "theme"): "light",
    ("ui", "aurora"): "green",
    ("ui", "aurora_style"): "waves",
    ("transcript_view", "curve"): "always",
}
# Ключ, который принимается только вместе с другим (проект по умолчанию — из списка).
TOGETHER = {
    ("integrations", "jira_default_project"): (
        "ABC", {"jira_projects": [{"key": "ABC", "aliases": ["эй би си"]}]}),
}


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path


def _pairs(raw: dict) -> list[tuple[str, str]]:
    return sorted((g, k) for g, section in raw.items() if isinstance(section, dict) for k in section)


def _sources(root: Path, globs: tuple[str, ...], skip=lambda p: False) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for g in globs for p in root.rglob(g) if not skip(p))


def _other(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return round(value * 0.9 + 0.01, 3)
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, list):
        return value[:-1] if value else ["x"]
    return value


def test_window_writes_only_keys_the_engine_knows(data):
    known = set(_pairs(settings.load().to_raw()))
    window = {}
    for path in [*WINDOW.rglob("*.ts"), *WINDOW.rglob("*.tsx")]:
        if ".test." in path.name:
            continue
        for m in SET_CALL.finditer(path.read_text(encoding="utf-8")):
            window.setdefault((m.group(1), m.group(2)), path.name)
    assert len(window) > 40  # страж сам не ослеп: окно пишет десятки настроек
    assert {pair: name for pair, name in window.items() if pair not in known} == {}


def test_every_setting_has_a_reader(data):
    engine = _sources(ENGINE, ("*.py",), skip=lambda p: p.name == "settings.py")
    others = _sources(WINDOW, ("*.ts", "*.tsx"), skip=lambda p: ".test." in p.name) + _sources(SHELL, ("*.rs",))
    unread = []
    for group, key in _pairs(settings.load().to_raw()):
        rx = re.compile(rf"""(\.{key}\b|["']{key}["'])""")
        if (group, key) in READ_IN_SETTINGS or rx.search(engine) or rx.search(others):
            continue
        unread.append(f"{group}.{key}")
    assert unread == []


def test_exceptions_are_still_real(data):
    pairs = set(_pairs(settings.load().to_raw()))
    for table in (READ_IN_SETTINGS, ENGINE_OWNED, VALUES, TOGETHER):
        assert set(table) <= pairs


def test_former_names_follow_a_rename(data):
    settings.patch({"recording": {"speaker_name": "Андрей"}})
    settings.patch({"recording": {"speaker_name": "Андрей С."}})
    assert settings.load().to_raw()["recording"]["former_speaker_names"] == ["Андрей"]


def _cases():
    import os
    import tempfile

    old = os.environ.get("MEET_DATA_DIR")
    os.environ["MEET_DATA_DIR"] = tempfile.mkdtemp()
    try:
        return _pairs(settings.load().to_raw())
    finally:
        if old is None:
            os.environ.pop("MEET_DATA_DIR", None)
        else:
            os.environ["MEET_DATA_DIR"] = old


@pytest.mark.parametrize("group,key", _cases(), ids=lambda x: x)
def test_setting_round_trips_through_patch_and_file(data, group, key):
    if (group, key) in ENGINE_OWNED:
        pytest.skip(ENGINE_OWNED[(group, key)])
    before = settings.load().to_raw()[group][key]
    if (group, key) in TOGETHER:
        new, extra = TOGETHER[(group, key)]
    else:
        new, extra = VALUES.get((group, key), _other(before)), {}
    assert new is not None and new != before, f"{group}.{key}: задайте значение не по умолчанию в VALUES"
    settings.patch({group: {**extra, key: new}})
    on_disk = json.loads((data / "config.json").read_text(encoding="utf-8"))
    assert on_disk.get(group, {}).get(key) == new, f"{group}.{key} не записан в config.json"
    assert settings.load().to_raw()[group][key] == new
    settings.patch({group: {key: before}})
    assert settings.load().to_raw()[group][key] == before
