"""Списки моделей и уровней рассуждений из самих CLI (0.6.2, `meet.llm.cli_models`):
Codex — `codex app-server` `model/list` (запасной путь `codex debug models`),
Antigravity CLI — ответ `session/new` по ACP (`models`, `configOptions`),
Claude Code — постоянный список, OpenCode — `opencode models`; кэш и маршрут
резидента `GET /assistant/models`.

Собеседники — поддельные серверы `fake_codex_app_server.py` и `fake_agy_acp.py`
(Python, модель не зовётся)."""

import json
import sys
from pathlib import Path

import jsonschema
import pytest

from meet import settings
from meet.llm import antigravity as agy
from meet.llm import cli_models, codex, codex_app

FAKE_CODEX = Path(__file__).with_name("fake_codex_app_server.py")
FAKE_AGY = Path(__file__).with_name("fake_agy_acp.py")
SCHEMA = json.loads((Path(__file__).parent / "fixtures" / "codex_app_server_schema.json").read_text(encoding="utf-8"))

# Выдержка `codex debug models` (codex-cli 0.160.0, лишние поля убраны).
DEBUG_MODELS = {"models": [
    {"slug": "gpt-6.1-sol", "display_name": "GPT-6.1-Sol", "description": "Сильная", "visibility": "list",
     "priority": 3, "default_reasoning_level": "low",
     "supported_reasoning_levels": [{"effort": "low", "description": "Fast"}, {"effort": "max", "description": ""}]},
    {"slug": "gpt-reserve", "display_name": "Reserve", "visibility": "hide", "priority": 2,
     "default_reasoning_level": "medium", "supported_reasoning_levels": [{"effort": "medium"}]},
    {"slug": "gpt-6.1-terra", "display_name": "GPT-6.1 Terra", "visibility": "list", "priority": 1,
     "default_reasoning_level": "medium",
     "supported_reasoning_levels": [{"effort": "medium"}, {"effort": "Bad Effort"}]},
]}


@pytest.fixture(autouse=True)
def _clean_cache():
    cli_models.clear_cache()
    yield
    cli_models.clear_cache()


def _codex(monkeypatch, tmp_path):
    log = tmp_path / "codex.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.setattr(codex, "find_codex", lambda: "C:/codex.exe")
    monkeypatch.setattr(codex_app, "command", lambda exe, flags: [sys.executable, str(FAKE_CODEX), *flags])
    return log


def _received(log: Path) -> list[dict]:
    return [json.loads(line)["in"] for line in log.read_text(encoding="utf-8").splitlines()
            if "in" in json.loads(line)]


# --- Codex ---------------------------------------------------------------------------------


def test_codex_list_comes_from_app_server_without_hidden_models(monkeypatch, tmp_path):
    log = _codex(monkeypatch, tmp_path)
    got = cli_models.codex_models(None)
    assert "error" not in got
    assert got["models"] == [{
        "id": "gpt-6.1-terra", "label": "GPT-6.1 Terra", "default": True, "description": "Быстрая",
        "efforts": [{"id": "low", "description": "Fast"}, {"id": "high", "description": "Deep"},
                    {"id": "ultra", "description": "Deepest"}],
        "default_effort": "low"}]
    methods = [m.get("method") for m in _received(log)]
    assert methods == ["initialize", "initialized", "model/list"]       # без сеанса и без модели


def test_codex_model_list_follows_pages_and_matches_the_schema(monkeypatch, tmp_path):
    log = _codex(monkeypatch, tmp_path)
    page = lambda mid: {"id": mid, "model": mid, "displayName": mid.upper(), "description": "", "hidden": False,  # noqa: E731
                        "isDefault": False, "defaultReasoningEffort": "medium",
                        "supportedReasoningEfforts": [{"reasoningEffort": "medium", "description": ""}]}
    pages = [[page("gpt-a")], [page("gpt-b")], [page("gpt-c")]]
    monkeypatch.setenv("FAKE_CODEX_MODELS", json.dumps(pages))
    raw = codex_app.list_models(codex_app.command("codex", []), env=None)
    assert [m["id"] for m in raw] == ["gpt-a", "gpt-b", "gpt-c"]
    sent = [m for m in _received(log) if m.get("method") == "model/list"]
    assert [m["params"] for m in sent] == [{}, {"cursor": "p1"}, {"cursor": "p2"}]
    for msg in sent:
        jsonschema.validate(msg, SCHEMA["client_request"])
    # Ответ поддельного сервера — по схеме Codex 0.160.0.
    for index in range(3):
        jsonschema.validate({"data": pages[index], "nextCursor": None}, SCHEMA["model_list_response"])


def test_codex_falls_back_to_debug_models(monkeypatch, tmp_path):
    _codex(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_CODEX_MODELS", "error")
    calls = []

    def fake_run(cmd, timeout, env):
        calls.append(cmd)
        return 0, json.dumps(DEBUG_MODELS).encode("utf-8"), b""

    monkeypatch.setattr(cli_models, "_run", fake_run)
    got = cli_models.codex_models(None)
    assert calls == [["C:/codex.exe", "debug", "models"]]
    assert [m["id"] for m in got["models"]] == ["gpt-6.1-terra", "gpt-6.1-sol"]        # по priority, без hide
    terra, sol = got["models"]
    assert terra["efforts"] == [{"id": "medium"}] and terra["default_effort"] == "medium"
    assert sol["efforts"] == [{"id": "low", "description": "Fast"}, {"id": "max"}] and sol["default"] is False


def test_codex_errors_are_text_not_exceptions(monkeypatch, tmp_path):
    _codex(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_CODEX_MODELS", "error")
    monkeypatch.setattr(cli_models, "_run", lambda cmd, timeout, env: (1, b"", b"boom"))
    got = cli_models.codex_models(None)
    assert got["models"] == [] and "model/list" in got["error"] and "boom" in got["error"]
    monkeypatch.setattr(codex, "find_codex", lambda: None)
    assert cli_models.codex_models(None) == {"models": [], "error": "не найден Codex CLI (codex)"}


def test_codex_parse_skips_hidden_and_unsafe_names():
    data = [{"model": "ok-1", "displayName": "OK", "hidden": False, "isDefault": False,
             "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "a b"}]},
            {"model": "bad&name", "hidden": False}, {"model": "hid", "hidden": True}]
    got = cli_models.parse_codex_model_list(data)
    assert got == [{"id": "ok-1", "label": "OK", "default": False, "efforts": [{"id": "low"}]}]


# --- Antigravity CLI -----------------------------------------------------------------------


def _agy(monkeypatch, tmp_path):
    log = tmp_path / "agy.jsonl"
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    monkeypatch.setattr(agy, "home", lambda: tmp_path / "agy-home")
    monkeypatch.setattr(agy, "command", lambda exe, flags: [sys.executable, str(FAKE_AGY), *flags])
    return log


@pytest.mark.parametrize("variant", ["acp", "config", "both"])
def test_agy_models_come_from_session_new(monkeypatch, tmp_path, variant):
    log = _agy(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_AGY_MODELS", variant)
    got = agy.list_models("C:/agy.exe")
    assert "error" not in got
    ids = [m["id"] for m in got["models"]]
    assert ids[:2] == ["gemini-3-pro", "gemini-3-flash"] and "bad name&" not in ids
    if variant != "acp":
        assert "claude-x" in ids                       # значения опции модели — и из групп
    pro = got["models"][0]
    assert pro["default"] is True and pro["label"] == "Gemini 3 Pro"
    if variant == "acp":
        assert pro["efforts"] == [] and "default_effort" not in pro
    else:
        assert pro["efforts"] == [{"id": "low", "label": "Low"}, {"id": "high", "label": "High", "description": "дольше"}]
        assert pro["default_effort"] == "high"
    methods = [json.loads(line).get("in", {}).get("method")
               for line in log.read_text(encoding="utf-8").splitlines()]
    assert "session/prompt" not in methods and "session/close" in methods


def test_agy_without_login_names_the_login_command(monkeypatch, tmp_path):
    _agy(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_AGY_AUTH", "1")
    got = agy.list_models("C:/agy.exe")
    assert got["models"] == [] and "вход" in got["error"]
    assert got["login"] == f'"C:/agy.exe" acp --login --gemini-dir "{tmp_path / "agy-home"}"'
    assert got["login"] in got["error"]


def test_agy_without_model_info_asks_for_a_name(monkeypatch, tmp_path):
    _agy(monkeypatch, tmp_path)
    got = agy.list_models("C:/agy.exe")
    assert got["models"] == [] and "вручную" in got["error"] and "login" not in got


def test_agy_session_models_parses_both_shapes_defensively():
    assert agy.session_models({}) == {"models": []}
    assert agy.session_models({"configOptions": "x", "models": []}) == {"models": []}
    got = agy.session_models({"configOptions": [
        {"id": "thinking_level", "type": "select", "currentValue": "max",
         "options": [{"value": "max", "name": "Max"}]},
        {"id": "model", "type": "select", "currentValue": "m1", "options": [{"value": "m1", "name": "M1"}]}]})
    assert got == {"models": [{"id": "m1", "label": "M1", "default": True,
                               "efforts": [{"id": "max", "label": "Max"}], "default_effort": "max"}]}


# --- Claude Code, OpenCode, кэш, маршрут -----------------------------------------------------


def test_claude_list_is_fixed_with_the_configured_model():
    got = cli_models.claude_models("claude-opus-5-5")
    assert [m["id"] for m in got["models"]] == ["sonnet", "opus", "haiku", "claude-opus-5-5"]
    assert got["models"][0]["default"] is True
    assert [e["id"] for e in got["models"][0]["efforts"]] == list(settings.CLAUDE_EFFORTS)
    assert [m["id"] for m in cli_models.claude_models("opus")["models"]] == ["sonnet", "opus", "haiku"]


def test_opencode_lines_become_models():
    out = "\x1b[1manthropic/claude-sonnet-4-5\x1b[0m\nollama/qwen3:8b\nмусор\nanthropic/claude-sonnet-4-5\n"
    assert [m["id"] for m in cli_models.parse_opencode(out)] == ["anthropic/claude-sonnet-4-5", "ollama/qwen3:8b"]


def test_list_is_cached_and_refresh_asks_again(monkeypatch):
    calls = []

    def fetch(provider, cfg):
        calls.append(provider)
        return {"models": [{"id": f"m{len(calls)}"}]}

    monkeypatch.setattr(cli_models, "_fetch", fetch)
    cfg = settings.Settings.from_raw({})
    first = cli_models.list_for("codex", cfg)
    assert first == {"models": [{"id": "m1"}]}
    assert cli_models.list_for("codex", cfg)["cached"] is True and calls == ["codex"]
    assert cli_models.list_for("codex", cfg, refresh=True) == {"models": [{"id": "m2"}]}
    # Другой прокси — другой ключ кэша.
    other = settings.Settings.from_raw({"llm": {"proxy": "none"}})
    cli_models.list_for("codex", other)
    assert calls == ["codex", "codex", "codex"]
    with pytest.raises(ValueError):
        cli_models.list_for("gpt", cfg)


def test_errors_are_cached_briefly(monkeypatch):
    monkeypatch.setattr(cli_models, "_fetch", lambda p, c: {"models": [], "error": "нет входа"})
    clock = [100.0]
    monkeypatch.setattr(cli_models.time, "monotonic", lambda: clock[0])
    cfg = settings.Settings.from_raw({})
    assert cli_models.list_for("agy", cfg)["error"] == "нет входа"
    assert cli_models.list_for("agy", cfg).get("cached") is True
    clock[0] += cli_models.ERROR_TTL_S + 1
    assert "cached" not in cli_models.list_for("agy", cfg)


def test_fetch_failure_is_an_error_in_the_answer(monkeypatch):
    def boom(provider, cfg):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(cli_models, "_fetch", boom)
    got = cli_models.list_for("opencode", settings.Settings.from_raw({}))
    assert got == {"models": [], "error": "RuntimeError: сломалось"}
