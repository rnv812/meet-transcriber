import asyncio
import json

from meet.llm import check, detect
from meet.llm.base import AgentReply


def _codex_env(monkeypatch, *, path="C:/codex.exe", logged=(True, None),
               reply=AgentReply(text="Да")):
    from meet.llm import codex

    monkeypatch.setattr(detect, "find_codex", lambda: path)
    monkeypatch.setattr(detect, "logged_in", lambda name, p: logged)
    calls = []

    async def fake_run(prompt, **kw):
        calls.append((prompt, kw))
        return reply

    monkeypatch.setattr(codex, "run", fake_run)
    return calls


def test_codex_not_logged_in(monkeypatch):
    calls = _codex_env(monkeypatch, logged=(False, "Not logged in"))
    res = asyncio.run(check.check("codex"))
    assert res == {"ok": False, "error": "не авторизован: Not logged in",
                   "provider": "codex"}
    assert calls == []


def test_codex_ok(monkeypatch):
    calls = _codex_env(monkeypatch)
    res = asyncio.run(check.check("codex"))
    assert res == {"ok": True, "error": None, "provider": "codex"}
    assert calls[0][0] == "Ответь одним словом: да"
    assert calls[0][1]["max_turns"] == 1


def test_codex_call_error(monkeypatch):
    _codex_env(monkeypatch, reply=AgentReply(text="", error="usage limit"))
    res = asyncio.run(check.check("codex"))
    assert res == {"ok": False, "error": "usage limit", "provider": "codex"}


def test_codex_not_found(monkeypatch):
    _codex_env(monkeypatch, path=None)
    res = asyncio.run(check.check("codex"))
    assert res["ok"] is False and "не найден" in res["error"]


def test_claude_not_logged_in(monkeypatch):
    # Проверка Claude снимает ANTHROPIC_API_KEY с окружения процесса —
    # настоящий ключ pytest-процесса трогать нельзя.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(detect, "find_claude", lambda: "C:/claude.exe")
    monkeypatch.setattr(detect, "logged_in", lambda name, p: (False, "loggedIn: false"))
    res = asyncio.run(check.check("claude-code"))
    assert res == {"ok": False, "error": "не авторизован: loggedIn: false",
                   "provider": "claude-code"}


def test_claude_ok_via_check_auth(monkeypatch):
    from meet.llm import claude

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(detect, "find_claude", lambda: "C:/claude.exe")
    monkeypatch.setattr(detect, "logged_in", lambda name, p: (True, None))

    async def fake_check_auth(proxy=None, model=None):
        return None

    monkeypatch.setattr(claude, "check_auth", fake_check_auth)
    assert asyncio.run(check.check("claude-code")) == {
        "ok": True, "error": None, "provider": "claude-code"}


def test_unknown_provider():
    res = asyncio.run(check.check("gpt"))
    assert res["ok"] is False and res["provider"] == "gpt"


def test_main_prints_json(monkeypatch, capsys):
    async def fake(provider):
        return {"ok": False, "error": "не авторизован: x", "provider": provider}

    monkeypatch.setattr(check, "check", fake)
    code = check.main(["codex"])
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": False, "error": "не авторизован: x", "provider": "codex"}
    assert code == 1


def test_codex_check_uses_proxy_setting(monkeypatch, tmp_path):
    from meet import settings

    settings.patch({"llm": {"proxy": "none"}})
    calls = _codex_env(monkeypatch)
    asyncio.run(check.check("codex"))
    assert calls[0][1]["proxy"] == "none"


def test_claude_check_uses_proxy_setting(monkeypatch):
    from meet import settings
    from meet.llm import claude

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings.patch({"llm": {"proxy": "http://10.1.1.1:3128", "model": "opus"}})
    monkeypatch.setattr(detect, "find_claude", lambda: "C:/claude.exe")
    monkeypatch.setattr(detect, "logged_in", lambda name, p: (True, None))
    seen = {}

    async def fake_check_auth(**kw):
        seen.update(kw)

    monkeypatch.setattr(claude, "check_auth", fake_check_auth)
    assert asyncio.run(check.check("claude-code"))["ok"] is True
    assert seen == {"proxy": "http://10.1.1.1:3128", "model": "opus"}  # «Проверить» — моделью из настроек


# --- OpenCode: проверка без вызова модели ----------------------------------------


def _opencode_env(monkeypatch, *, path="C:/oc/opencode.exe", logged=(True, None), listed=(True, None)):
    from meet.llm import opencode

    monkeypatch.setattr(detect, "find_opencode", lambda: path)
    seen = {"logged": [], "listed": []}

    def fake_logged(name, p):
        seen["logged"].append((name, p))
        return logged

    def fake_listed(p, model, proxy=None):
        seen["listed"].append((p, model, proxy))
        return listed

    async def no_call(*a, **k):
        raise AssertionError("«Проверить» OpenCode не зовёт модель")

    monkeypatch.setattr(detect, "logged_in", fake_logged)
    monkeypatch.setattr(detect, "opencode_auth_present", lambda provider=None: True)
    monkeypatch.setattr(detect, "opencode_model_listed", fake_listed)
    monkeypatch.setattr(opencode, "run", no_call)
    return seen


def test_opencode_not_found(monkeypatch):
    _opencode_env(monkeypatch, path=None)
    res = asyncio.run(check.check("opencode"))
    assert res["ok"] is False and "opencode.ai" in res["error"]


def test_opencode_without_model_checks_the_login(monkeypatch):
    seen = _opencode_env(monkeypatch)
    assert asyncio.run(check.check("opencode")) == {"ok": True, "error": None, "provider": "opencode"}
    assert seen["logged"] == [("opencode", "C:/oc/opencode.exe")] and seen["listed"] == []


def test_opencode_not_logged_in(monkeypatch):
    _opencode_env(monkeypatch, logged=(False, "нет входа"))
    res = asyncio.run(check.check("opencode"))
    assert res == {"ok": False, "error": "не авторизован: нет входа", "provider": "opencode"}


def test_opencode_with_model_checks_that_the_model_is_available(monkeypatch):
    from meet import settings

    settings.patch({"llm": {"opencode_model": "openai/gpt-5", "proxy": "none"}})
    seen = _opencode_env(monkeypatch, listed=(False, "провайдер openai не подключён"))
    res = asyncio.run(check.check("opencode"))
    assert res == {"ok": False, "error": "провайдер openai не подключён", "provider": "opencode"}
    assert seen["listed"] == [("C:/oc/opencode.exe", "openai/gpt-5", "none")]
    _opencode_env(monkeypatch)
    assert asyncio.run(check.check("opencode"))["ok"] is True


def test_opencode_model_missing_names_the_providers_login(monkeypatch):
    from meet import settings

    settings.patch({"llm": {"opencode_model": "openrouter/meta-llama/llama-3.3-70b"}})
    _opencode_env(monkeypatch, listed=(False, "у OpenCode нет модели"))
    seen = []

    def present(provider=None):
        seen.append(provider)
        return False

    monkeypatch.setattr(detect, "opencode_auth_present", present)
    res = asyncio.run(check.check("opencode"))
    assert seen == ["openrouter"]
    assert "входа для openrouter" in res["error"] and "OPENROUTER_API_KEY" in res["error"]
    monkeypatch.setattr(detect, "opencode_auth_present", lambda provider=None: True)
    res = asyncio.run(check.check("opencode"))
    assert res["error"] == "у OpenCode нет модели"  # вход есть — только причина


# --- локальная модель: список моделей сервера, затем короткий вызов ------------------


def _local_env(monkeypatch, *, model=None, listed=None, reply=AgentReply(text="Да")):
    from dataclasses import replace

    from meet import settings
    from meet.llm import local_models, openai_compat

    cfg = settings.Settings()
    cfg = replace(cfg, llm=replace(cfg.llm, provider="openai-compatible", local_model=model,
                                   base_url="http://127.0.0.1:1234/v1"))
    monkeypatch.setattr(settings, "load", lambda *a, **k: cfg)
    seen = {"listed": [], "calls": []}

    def fake_list(base_url, model=None, timeout=None):
        seen["listed"].append((base_url, model))
        return listed

    async def fake_run(prompt, **kw):
        seen["calls"].append(kw)
        return reply

    monkeypatch.setattr(local_models, "list_models", fake_list)
    monkeypatch.setattr(openai_compat, "run", fake_run)
    return seen


def _listed(*ids, missing=False):
    return {"ok": True, "models": [{"id": i, "size": None, "params": None, "context": None} for i in ids],
            "missing": missing, "warning": "Модели «x» на сервере нет — выберите другую из списка" if missing else None,
            "error": None, "reason": None}


def test_local_ok_when_the_model_is_listed_and_answers(monkeypatch):
    seen = _local_env(monkeypatch, model="qwen3:8b", listed=_listed("qwen3:8b", "gemma3:4b"))
    res = asyncio.run(check.check("openai-compatible"))
    assert res["ok"] is True
    assert seen["listed"] == [("http://127.0.0.1:1234/v1", "qwen3:8b")]
    assert seen["calls"][0]["local_model"] == "qwen3:8b"  # короткий вызов остаётся


def test_local_model_no_longer_listed(monkeypatch):
    seen = _local_env(monkeypatch, model="llama3", listed=_listed("qwen3:8b", missing=True))
    res = asyncio.run(check.check("openai-compatible"))
    assert res["ok"] is False and "нет" in res["error"] and "qwen3:8b" in res["error"]
    assert seen["calls"] == []


def test_local_server_down_is_said_plainly(monkeypatch):
    down = {"ok": False, "reason": "unreachable", "error": "Сервер не отвечает: http://127.0.0.1:1234/v1",
            "models": [], "missing": False, "warning": None}
    seen = _local_env(monkeypatch, model="qwen3:8b", listed=down)
    res = asyncio.run(check.check("openai-compatible"))
    assert res["error"].startswith("Сервер не отвечает") and seen["calls"] == []


def test_local_without_model_list_still_tries_the_call(monkeypatch):
    # Сервер без /models (не всякий OpenAI-совместимый его отдаёт) — решает сам вызов.
    odd = {"ok": False, "reason": "not_openai", "error": "списка моделей нет", "models": [],
           "missing": False, "warning": None}
    seen = _local_env(monkeypatch, model="m", listed=odd)
    assert asyncio.run(check.check("openai-compatible"))["ok"] is True
    assert len(seen["calls"]) == 1


def test_local_without_a_chosen_model_among_several(monkeypatch):
    seen = _local_env(monkeypatch, model=None, listed=_listed("qwen3:8b", "gemma3:4b"))
    res = asyncio.run(check.check("openai-compatible"))
    assert res["ok"] is False and "не выбрана" in res["error"] and seen["calls"] == []
