import subprocess
import sys

import meet.llm as llm
from meet.llm import detect
from meet.settings import Settings


def _cfg(provider="auto"):
    return Settings.from_raw({"llm": {"provider": provider}})


def _env(monkeypatch, *, claude=None, codex=None, local=False, logged=None):
    logged = logged or {}
    monkeypatch.setattr(detect, "find_claude", lambda: claude)
    monkeypatch.setattr(detect, "find_codex", lambda: codex)
    monkeypatch.setattr(detect, "local_reachable", lambda url, timeout=0.5: local)

    def fake_logged_in(name, path):
        ok = logged.get(name, True)
        return ok, None if ok else "нет"

    monkeypatch.setattr(detect, "logged_in", fake_logged_in)


def test_auto_only_codex(monkeypatch):
    _env(monkeypatch, codex="C:/codex.exe")
    name, runner = llm.resolve(_cfg())
    assert name == "codex"
    assert callable(runner)


def test_auto_prefers_claude(monkeypatch):
    _env(monkeypatch, claude="C:/claude.exe", codex="C:/codex.exe")
    assert llm.resolve(_cfg())[0] == "claude-code"


def test_auto_skips_unauthorized_claude(monkeypatch):
    _env(monkeypatch, claude="C:/claude.exe", codex="C:/codex.exe",
         logged={"claude-code": False})
    assert llm.resolve(_cfg())[0] == "codex"


def test_auto_falls_back_to_local(monkeypatch):
    _env(monkeypatch, local=True)
    name, runner = llm.resolve(_cfg())
    assert name == "openai-compatible" and callable(runner)


def test_auto_nothing(monkeypatch):
    _env(monkeypatch)
    assert llm.resolve(_cfg()) == (None, None)


def test_explicit_claude_without_cli(monkeypatch):
    _env(monkeypatch, codex="C:/codex.exe")
    assert llm.resolve(_cfg("claude-code")) == (None, None)


def test_explicit_codex(monkeypatch):
    _env(monkeypatch, claude="C:/claude.exe", codex="C:/codex.exe")
    assert llm.resolve(_cfg("codex"))[0] == "codex"


def test_explicit_local_unreachable(monkeypatch):
    _env(monkeypatch, claude="C:/claude.exe")
    assert llm.resolve(_cfg("openai-compatible")) == (None, None)


def test_import_does_not_load_sdk():
    code = (
        "import sys, meet.llm, meet.llm.detect, meet.llm.base\n"
        "from meet.llm import detect\n"
        "detect.available(base_url='http://127.0.0.1:9/v1')\n"
        "bad = [m for m in ('claude_agent_sdk', 'aiohttp') if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ""
