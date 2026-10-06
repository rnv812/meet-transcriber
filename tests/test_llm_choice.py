"""Несколько моделей сразу (0.3.4, U3): включённые провайдеры, «по умолчанию»
и явный выбор модели для одной задачи — без тихого перехода на другую."""

import pytest

import meet.llm as llm
from meet import settings
from meet.llm import detect
from meet.settings import Settings


def _env(monkeypatch, *, claude=None, codex=None, opencode=None, local=False, logged=None):
    logged = logged or {}
    monkeypatch.setattr(detect, "find_claude", lambda: claude)
    monkeypatch.setattr(detect, "find_codex", lambda: codex)
    monkeypatch.setattr(detect, "find_opencode", lambda: opencode)
    monkeypatch.setattr(detect, "local_reachable", lambda url, timeout=0.5: local)

    def fake_logged_in(name, path):
        ok = logged.get(name, True)
        return ok, None if ok else "нет входа"

    monkeypatch.setattr(detect, "logged_in", fake_logged_in)


# --- настройки: перенос и нормализация -------------------------------------------


def test_constants_match_the_llm_package():
    assert settings.LLM_CONCRETE == llm.PROVIDERS
    assert settings.LLM_AUTO_ORDER == llm.AUTO_PROVIDERS


def test_single_provider_migrates_to_enabled_list_of_one():
    cfg = Settings.from_raw({"version": 2, "llm": {"provider": "codex"}})
    assert cfg.llm.provider == "codex"
    assert cfg.llm.enabled == ("codex",)


def test_legacy_config_without_provider_keeps_claude_only():
    cfg = Settings.from_raw({"version": 2, "llm": {"model": "opus"}})
    assert cfg.llm.provider == "claude-code"
    assert cfg.llm.enabled == ("claude-code",)


def test_auto_migrates_to_its_own_candidates():
    """У «Авто» прежний смысл: первый готовый из Claude Code → Codex → локальная;
    OpenCode в нём не участвует и включённым не становится."""
    cfg = Settings.from_raw({"version": 2, "llm": {"provider": "auto"}})
    assert cfg.llm.provider == "auto"
    assert cfg.llm.enabled == ("claude-code", "codex", "openai-compatible")
    assert Settings.from_raw({}).llm.enabled == ("claude-code", "codex", "openai-compatible")


def test_enabled_is_cleaned_and_the_default_always_enabled():
    cfg = Settings.from_raw({"llm": {"provider": "opencode",
                                     "enabled": ["openai-compatible", "мусор", "codex", "codex", 3]}})
    # Порядок — канонический (как в настройках), дубли и мусор — прочь, модель
    # по умолчанию включена всегда.
    assert cfg.llm.enabled == ("codex", "opencode", "openai-compatible")
    assert Settings.from_raw({"llm": {"provider": "codex", "enabled": "мусор"}}).llm.enabled == ("codex",)


def test_enabled_round_trips_and_direct_construction_is_normalised(tmp_path):
    f = tmp_path / "config.json"
    original = Settings(llm=settings.Llm(provider="opencode", enabled=("openai-compatible",)))
    assert original.llm.enabled == ("opencode", "openai-compatible")
    settings.save(original, f)
    assert settings.load(f).llm == original.llm
    assert settings.load(f).llm.to_raw()["enabled"] == ["opencode", "openai-compatible"]


def test_patch_enables_several_and_keeps_the_default(tmp_path):
    f = tmp_path / "config.json"
    settings.save(Settings.from_raw({"version": 2, "llm": {"provider": "openai-compatible"}}), f)
    got = settings.patch({"llm": {"enabled": ["openai-compatible", "claude-code"]}}, f)
    assert got.llm.provider == "openai-compatible"
    assert got.llm.enabled == ("claude-code", "openai-compatible")
    # Выключить модель по умолчанию нельзя: она снова включена.
    got = settings.patch({"llm": {"enabled": ["claude-code"]}}, f)
    assert got.llm.enabled == ("claude-code", "openai-compatible")


def test_patch_rejects_unknown_enabled_provider(tmp_path):
    f = tmp_path / "config.json"
    with pytest.raises(ValueError, match="неизвестная модель"):
        settings.patch({"llm": {"enabled": ["gpt"]}}, f)
    with pytest.raises(ValueError):
        settings.patch({"llm": {"enabled": "codex"}}, f)


# --- «Авто» и явный выбор ------------------------------------------------------------


def _cfg(provider="auto", enabled=None):
    raw = {"provider": provider}
    if enabled is not None:
        raw["enabled"] = enabled
    return Settings.from_raw({"version": 2, "llm": raw})


def test_auto_only_among_enabled(monkeypatch):
    """Выключенную модель «Авто» не берёт: текст не уйдёт туда, от чего человек отказался."""
    _env(monkeypatch, claude="C:/claude.exe", local=True)
    assert llm.resolve(_cfg("auto", ["openai-compatible"]))[0] == "openai-compatible"
    assert llm.resolve(_cfg("auto", ["codex"])) == (None, None)


def test_explicit_choice_is_used(monkeypatch):
    _env(monkeypatch, claude="C:/claude.exe", codex="C:/codex.exe", local=True)
    cfg = _cfg("openai-compatible", ["claude-code", "openai-compatible"])
    name, runner = llm.resolve(cfg, "claude-code")
    assert name == "claude-code" and callable(runner)
    assert llm.resolve(cfg)[0] == "openai-compatible"


def test_explicit_choice_never_falls_back(monkeypatch):
    """Выбранной модели нет — никого: ни модели по умолчанию, ни «Авто»."""
    _env(monkeypatch, claude="C:/claude.exe", local=True)
    cfg = _cfg("claude-code", ["claude-code", "codex", "openai-compatible"])
    assert llm.resolve(cfg, "codex") == (None, None)
    assert llm.choice_error(cfg, "codex") == "не найден Codex CLI (codex)"


def test_explicit_choice_must_be_enabled(monkeypatch):
    _env(monkeypatch, claude="C:/claude.exe", codex="C:/codex.exe", local=True)
    cfg = _cfg("openai-compatible", ["openai-compatible"])
    assert llm.resolve(cfg, "claude-code") == (None, None)
    assert "не включена" in llm.choice_error(cfg, "claude-code")
    assert "неизвестная модель" in llm.choice_error(cfg, "gpt")
    assert llm.resolve(cfg, "auto") == (None, None)


def test_choice_error_explains_each_provider(monkeypatch):
    _env(monkeypatch)
    cfg = _cfg("auto", ["claude-code", "codex", "opencode", "openai-compatible"])
    assert "Claude Code" in llm.choice_error(cfg, "claude-code")
    assert "OpenCode" in llm.choice_error(cfg, "opencode")
    assert "не отвечает" in llm.choice_error(cfg, "openai-compatible")
    _env(monkeypatch, codex="C:/codex.exe")
    assert llm.choice_error(cfg, "codex") is None


# --- подписи и происхождение ----------------------------------------------------------


def test_describe_and_label():
    cfg = Settings.from_raw({"version": 2, "llm": {
        "provider": "claude-code", "model": "opus", "opencode_model": "anthropic/claude-sonnet-4-5",
        "local_model": "qwen3", "enabled": ["claude-code", "codex", "opencode", "openai-compatible"]}})
    assert llm.describe("claude-code", cfg) == {"provider": "claude-code", "model": "opus"}
    assert llm.describe("codex", cfg) == {"provider": "codex", "model": None}
    assert llm.describe("opencode", cfg) == {"provider": "opencode", "model": "anthropic/claude-sonnet-4-5"}
    assert llm.describe("openai-compatible", cfg) == {"provider": "openai-compatible", "model": "qwen3"}
    assert llm.label({"provider": "claude-code", "model": "opus"}) == "Claude Code (opus)"
    assert llm.label({"provider": "codex", "model": None}) == "Codex"
    assert llm.label({"provider": "openai-compatible", "model": "qwen3"}) == "Локальная модель (qwen3)"
    assert llm.label(None) == "модель"


def test_local_only_for_a_loopback_server():
    def cfg(url):
        return Settings.from_raw({"llm": {"provider": "openai-compatible", "base_url": url}})

    assert llm.is_local("openai-compatible", cfg("http://127.0.0.1:1234/v1"))
    assert llm.is_local("openai-compatible", cfg("http://localhost:11434/v1"))
    assert not llm.is_local("openai-compatible", cfg("http://10.0.0.5:1234/v1"))
    assert not llm.is_local("claude-code", cfg("http://127.0.0.1:1234/v1"))
    assert not llm.is_local("opencode", cfg("http://127.0.0.1:1234/v1"))


def test_models_list_for_the_window(monkeypatch):
    cfg = _cfg("openai-compatible", ["claude-code", "codex", "openai-compatible"])
    found = {"claude-code": {"found": True}, "codex": {"found": False},
             "opencode": {"found": True}, "openai-compatible": {"found": True}}
    got = llm.models(cfg, found)
    assert [m["provider"] for m in got] == ["claude-code", "codex", "openai-compatible"]
    claude, codex, local = got
    assert claude == {"provider": "claude-code", "model": "sonnet", "label": "Claude Code (sonnet)",
                      "default": False, "local": False, "available": True, "reason": None}
    assert codex["available"] is False and codex["reason"] == "не найден Codex CLI (codex)"
    assert local["default"] is True and local["local"] is True
