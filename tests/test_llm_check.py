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

    async def fake_check_auth():
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
