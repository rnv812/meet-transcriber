import subprocess
import sys

import meet.llm as llm
from meet.llm import detect
from meet.settings import Settings


def _cfg(provider="auto"):
    return Settings.from_raw({"llm": {"provider": provider}})


def _env(monkeypatch, *, claude=None, codex=None, opencode=None, local=False, logged=None):
    logged = logged or {}
    monkeypatch.setattr(detect, "find_claude", lambda: claude)
    monkeypatch.setattr(detect, "find_codex", lambda: codex)
    monkeypatch.setattr(detect, "find_opencode", lambda: opencode)
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


def _real_find_claude(monkeypatch, tmp_path, found):
    """Настоящий find_claude (Windows) поверх подменённого PATH."""
    monkeypatch.setattr(detect, "_WINDOWS", True)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(detect.shutil, "which", lambda name: found.get(name))
    monkeypatch.setattr(detect, "find_codex", lambda: "C:/codex.exe")
    monkeypatch.setattr(detect, "local_reachable", lambda url, timeout=0.5: False)
    seen = []

    def fake_logged_in(name, path):
        seen.append((name, path))
        return True, None

    monkeypatch.setattr(detect, "logged_in", fake_logged_in)
    return seen


def test_auto_claude_cmd_only_falls_through_to_codex(monkeypatch, tmp_path):
    # В PATH только npm-шим claude.cmd, который SDK не запустит; даже если
    # вход в Claude «выполнен», auto берёт Codex.
    _real_find_claude(monkeypatch, tmp_path,
                      {"claude.cmd": "C:/npm/claude.cmd", "claude": "C:/npm/claude.cmd"})
    assert llm.resolve(_cfg())[0] == "codex"


def test_auto_claude_exe_is_used(monkeypatch, tmp_path):
    seen = _real_find_claude(
        monkeypatch, tmp_path,
        {"claude.cmd": "C:/npm/claude.cmd", "claude.exe": "C:/bin/claude.exe"})
    assert llm.resolve(_cfg())[0] == "claude-code"
    assert seen == [("claude-code", "C:/bin/claude.exe")]


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


def test_runner_for_passes_proxy_setting(monkeypatch):
    from meet.llm import claude, codex

    seen = []

    async def fake(prompt, **kw):
        seen.append(kw.get("proxy"))

    monkeypatch.setattr(claude, "run", fake)
    monkeypatch.setattr(codex, "run", fake)
    cfg = Settings.from_raw({"llm": {"proxy": "http://10.1.1.1:3128"}})
    import asyncio
    asyncio.run(llm.runner_for("claude-code", cfg)("q", system_prompt="s"))
    asyncio.run(llm.runner_for("codex", cfg)("q", system_prompt="s"))
    assert seen == ["http://10.1.1.1:3128", "http://10.1.1.1:3128"]


def test_tier_kwargs_fast_per_provider():
    from meet.llm import tier_kwargs

    assert tier_kwargs("claude-code", "fast") == {"model": "haiku", "thinking": "disabled"}
    assert tier_kwargs("codex", "fast") == {"effort": "low"}
    assert tier_kwargs("openai-compatible", "fast") == {}
    for provider in ("claude-code", "codex", "openai-compatible", None):
        assert tier_kwargs(provider, "agent") == {}  # модель не задана — без добавок


def test_tier_kwargs_agent_takes_the_configured_model():
    from meet.llm import tier_kwargs

    assert tier_kwargs("claude-code", "agent", "opus") == {"model": "opus"}
    assert tier_kwargs("claude-code", "fast", "opus") == {"model": "haiku", "thinking": "disabled"}
    # Codex — модель из своего конфига, локальная — llm.local_model.
    assert tier_kwargs("codex", "agent", "opus") == {}
    assert tier_kwargs("openai-compatible", "agent", "opus") == {}


def test_runner_for_claude_passes_the_configured_model(monkeypatch):
    import asyncio

    from meet.llm import claude

    seen = []

    async def fake(prompt, **kw):
        seen.append(kw.get("model"))

    monkeypatch.setattr(claude, "run", fake)
    cfg = Settings.from_raw({"llm": {"model": "opus"}})
    runner = llm.runner_for("claude-code", cfg)
    asyncio.run(runner("q", system_prompt="s"))
    asyncio.run(runner("q", system_prompt="s", model="haiku"))  # «Быстрее» перекрывает
    assert seen == ["opus", "haiku"]
    assert llm.agent_model("claude-code", cfg) == "opus"
    assert llm.agent_model("codex", cfg) is None


# --- OpenCode ---------------------------------------------------------------------


def test_auto_order_puts_opencode_after_codex_and_before_local(monkeypatch):
    assert llm.PROVIDERS == ("claude-code", "codex", "opencode", "openai-compatible")
    _env(monkeypatch, codex="C:/codex.exe", opencode="C:/oc/opencode.exe", local=True)
    assert llm.resolve(_cfg())[0] == "codex"
    _env(monkeypatch, opencode="C:/oc/opencode.exe", local=True)
    assert llm.resolve(_cfg())[0] == "opencode"


def test_auto_skips_opencode_without_login(monkeypatch):
    _env(monkeypatch, opencode="C:/oc/opencode.exe", local=True, logged={"opencode": False})
    assert llm.resolve(_cfg())[0] == "openai-compatible"


def test_explicit_opencode(monkeypatch):
    # Явный выбор — без проверки входа: её делает «Проверить».
    _env(monkeypatch, opencode="C:/oc/opencode.exe", logged={"opencode": False})
    name, runner = llm.resolve(_cfg("opencode"))
    assert name == "opencode" and callable(runner)
    _env(monkeypatch)
    assert llm.resolve(_cfg("opencode")) == (None, None)


def test_runner_for_opencode_passes_its_own_model_and_proxy(monkeypatch):
    import asyncio

    from meet.llm import opencode

    seen = []

    async def fake(prompt, **kw):
        seen.append((kw.get("model"), kw.get("proxy")))

    monkeypatch.setattr(opencode, "run", fake)
    cfg = Settings.from_raw({"llm": {"model": "opus", "opencode_model": "openai/gpt-5",
                                     "proxy": "none"}})
    asyncio.run(llm.runner_for("opencode", cfg)("q", system_prompt="s"))
    # Модель Claude Code («opus») OpenCode не получает.
    assert seen == [("openai/gpt-5", "none")]
    asyncio.run(llm.runner_for("opencode", Settings.from_raw({}))("q", system_prompt="s"))
    assert seen[-1][0] is None  # пусто — модель из конфига OpenCode
    assert llm.agent_model("opencode", cfg) is None


def test_tier_kwargs_opencode_adds_nothing():
    from meet.llm import tier_kwargs

    assert tier_kwargs("opencode", "fast") == {}
    assert tier_kwargs("opencode", "agent", "opus") == {}
