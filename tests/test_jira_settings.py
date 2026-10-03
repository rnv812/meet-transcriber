"""Настройки «Ссылки на Jira» 0.3.1: проекты вместо регулярного выражения,
проект по умолчанию, шаблон ключа в «Дополнительно» и миграция `jira_keys`."""

import json

import pytest

from meet import settings


def _raw(path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- миграция прежнего jira_keys ----------------------------------------------------


def test_comma_list_becomes_projects():
    got = settings.Integrations.from_raw({"jira_keys": "SPR, OPS"})
    assert [(p.key, p.aliases) for p in got.jira_projects] == [("SPR", ()), ("OPS", ())]
    assert got.jira_pattern == "" and got.jira_default_project == ""
    assert got.jira_literal() == r"(?:SPR|OPS)-\d+"


def test_custom_regex_moves_to_advanced():
    got = settings.Integrations.from_raw({"jira_keys": r"DEMO-\d{2,4}"})
    assert got.jira_projects == () and got.jira_pattern == r"DEMO-\d{2,4}"
    assert got.jira_literal() == r"DEMO-\d{2,4}"


@pytest.mark.parametrize("keys", [settings.DEFAULT_JIRA_KEYS, "", None, "(", r"(?i)x-\d+"])
def test_default_or_broken_regex_leaves_advanced_empty(keys):
    got = settings.Integrations.from_raw({} if keys is None else {"jira_keys": keys})
    assert got.jira_projects == () and got.jira_pattern == ""
    assert got.jira_literal() == settings.DEFAULT_JIRA_KEYS


def test_migrated_file_keeps_projects_after_save(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": settings.SCHEMA_VERSION, "integrations": {
        "jira_base_url": "https://jira.example.com", "jira_keys": "SPR, OPS"}}), encoding="utf-8")
    # Любая правка из окна сохраняет уже новую схему: проекты, а не jira_keys.
    settings.patch({"integrations": {"jira_default_project": "OPS"}}, path)
    raw = _raw(path)["integrations"]
    assert raw["jira_projects"] == [{"key": "SPR", "aliases": []}, {"key": "OPS", "aliases": []}]
    assert raw["jira_default_project"] == "OPS" and raw["jira_pattern"] == ""
    assert "jira_keys" not in raw
    again = settings.load(path).integrations
    assert [p.key for p in again.jira_projects] == ["SPR", "OPS"] and again.jira_default_project == "OPS"


def test_new_keys_win_over_legacy():
    got = settings.Integrations.from_raw({"jira_keys": "SPR", "jira_projects": [], "jira_pattern": ""})
    assert got.jira_projects == () and got.jira_literal() == settings.DEFAULT_JIRA_KEYS


# --- проекты из файла и из окна ---------------------------------------------------


def test_projects_from_file_are_cleaned():
    got = settings.Integrations.from_raw({"jira_projects": [
        {"key": "orion", "aliases": ["орайон", "  о  ри  он ", "орайон", "x1", "", 5]},
        "SPR", {"key": "SPR"}, {"key": "1AB"}, {"key": "A"}, None,
    ], "jira_default_project": "spr"})
    assert [(p.key, p.aliases) for p in got.jira_projects] == [("ORION", ("орайон", "о ри он")), ("SPR", ())]
    assert got.jira_default_project == "SPR"


def test_default_project_must_be_in_list_from_file():
    got = settings.Integrations.from_raw({"jira_projects": [{"key": "SPR"}], "jira_default_project": "OPS"})
    assert got.jira_default_project == ""


def test_window_saves_projects_aliases_and_default(tmp_path):
    path = tmp_path / "config.json"
    updated = settings.patch({"integrations": {
        "jira_projects": [{"key": "ORION", "aliases": ["орайон"]}, {"key": "SPR", "aliases": []}],
        "jira_default_project": "ORION"}}, path)
    assert updated.integrations.jira_default_project == "ORION"
    assert _raw(path)["integrations"]["jira_projects"][0] == {"key": "ORION", "aliases": ["орайон"]}
    # Убрали проект по умолчанию из списка — проект по умолчанию снимается.
    again = settings.patch({"integrations": {"jira_projects": [{"key": "SPR", "aliases": []}]}}, path)
    assert again.integrations.jira_default_project == ""


@pytest.mark.parametrize("projects", [
    "ORION", [{"key": "orion"}], [{"key": "S"}], [{"key": "SM-DEV"}], [{"key": "SPR"}, {"key": "SPR"}],
    [{"key": "SPR", "aliases": ["эс пи ар 2"]}], [{"key": "SPR", "aliases": "эс пи ар"}],
    [{"key": "SPR", "aliases": ["ab"]}], [{"key": "SPR", "aliases": ["с/п/р"]}],
    [{"key": f"P{n:02d}"} for n in range(31)],
])
def test_bad_projects_rejected_from_window(tmp_path, projects):
    with pytest.raises(ValueError):
        settings.patch({"integrations": {"jira_projects": projects}}, tmp_path / "config.json")


def test_default_project_not_in_list_rejected(tmp_path):
    path = tmp_path / "config.json"
    with pytest.raises(ValueError, match="OPS"):
        settings.patch({"integrations": {"jira_default_project": "OPS"}}, path)
    settings.patch({"integrations": {"jira_projects": [{"key": "OPS", "aliases": []}]}}, path)
    assert settings.patch({"integrations": {"jira_default_project": "OPS"}},
                          path).integrations.jira_default_project == "OPS"
    with pytest.raises(ValueError):
        settings.patch({"integrations": {"jira_projects": [{"key": "SPR", "aliases": []}],
                                         "jira_default_project": "OPS"}}, path)


def test_advanced_pattern_wins_over_projects():
    got = settings.Integrations.from_raw({"jira_projects": [{"key": "SPR"}], "jira_pattern": "OPS, DEMO"})
    assert got.jira_literal() == r"(?:OPS|DEMO)-\d+"
