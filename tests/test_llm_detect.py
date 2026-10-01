import socket
from pathlib import Path

from meet.llm import detect


def test_find_codex_prefers_path(monkeypatch):
    monkeypatch.setattr(
        detect.shutil, "which",
        lambda name: "C:/npm/codex.cmd" if name == "codex.cmd" else None)
    assert detect.find_codex() == "C:/npm/codex.cmd"


def test_find_codex_prefers_exe_over_cmd(monkeypatch):
    found = {"codex.cmd": "C:/npm/codex.cmd", "codex.exe": "C:/bin/codex.exe"}
    monkeypatch.setattr(detect.shutil, "which", lambda name: found.get(name))
    assert detect.find_codex() == "C:/bin/codex.exe"


def test_find_codex_falls_back_to_localappdata(monkeypatch, tmp_path):
    exe = tmp_path / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert detect.find_codex() == str(exe)


def test_find_codex_none(monkeypatch, tmp_path):
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert detect.find_codex() is None


def test_find_claude_order(monkeypatch, tmp_path):
    monkeypatch.setattr(detect, "_WINDOWS", True)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    found = {"claude.cmd": "C:/npm/claude.cmd", "claude.exe": "C:/x/claude.exe",
             "claude": "C:/x/claude"}
    monkeypatch.setattr(detect.shutil, "which", lambda name: found.get(name))
    assert detect.find_claude() == "C:/x/claude.exe"


def test_find_claude_cmd_only_is_not_found(monkeypatch, tmp_path):
    # SDK отказывается запускать .cmd-шим на Windows — это «не найден».
    monkeypatch.setattr(detect, "_WINDOWS", True)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    found = {"claude.cmd": "C:/npm/claude.cmd", "claude": "C:/npm/claude.cmd"}
    monkeypatch.setattr(detect.shutil, "which", lambda name: found.get(name))
    assert detect.find_claude() is None


def test_find_claude_native_install_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(detect, "_WINDOWS", True)
    exe = tmp_path / ".local" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(
        detect.shutil, "which",
        lambda name: "C:/npm/claude.cmd" if name == "claude.cmd" else None)
    assert detect.find_claude() == str(exe)


def test_find_claude_posix(monkeypatch):
    monkeypatch.setattr(detect, "_WINDOWS", False)
    monkeypatch.setattr(detect.shutil, "which",
                        lambda name: "/usr/bin/claude" if name == "claude" else None)
    assert detect.find_claude() == "/usr/bin/claude"


def test_local_reachable_true_and_false():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert detect.local_reachable(f"http://127.0.0.1:{port}/v1") is True
    finally:
        srv.close()
    assert detect.local_reachable(f"http://127.0.0.1:{port}/v1") is False
    assert detect.local_reachable("not a url") is False


def test_available_shape(monkeypatch):
    monkeypatch.setattr(detect, "find_claude", lambda: None)
    monkeypatch.setattr(detect, "find_codex", lambda: "C:/codex.exe")
    monkeypatch.setattr(detect, "local_reachable", lambda url, timeout=0.5: False)
    av = detect.available(base_url="http://127.0.0.1:1234/v1")
    assert av == {
        "claude-code": {"found": False, "path": None},
        "codex": {"found": True, "path": "C:/codex.exe"},
        "openai-compatible": {"found": False, "base_url": "http://127.0.0.1:1234/v1"},
    }


class _Done:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_codex_logged_in(monkeypatch):
    monkeypatch.setattr(detect.subprocess, "run",
                        lambda *a, **k: _Done(0, "Logged in using ChatGPT"))
    assert detect.logged_in("codex", "codex.exe") == (True, None)


def test_codex_not_logged_in(monkeypatch):
    monkeypatch.setattr(detect.subprocess, "run",
                        lambda *a, **k: _Done(1, "", "Not logged in"))
    ok, why = detect.logged_in("codex", "codex.exe")
    assert ok is False and "Not logged in" in why


def test_claude_logged_in_drops_api_key(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        seen["env"] = kw.get("env")
        return _Done(0, '{"loggedIn": true, "authMethod": "claude.ai"}')

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(detect.subprocess, "run", run)
    assert detect.logged_in("claude-code", "claude.exe") == (True, None)
    assert seen["cmd"][1:3] == ["auth", "status"]
    assert "ANTHROPIC_API_KEY" not in seen["env"]


def test_claude_not_logged_in(monkeypatch):
    monkeypatch.setattr(detect.subprocess, "run",
                        lambda *a, **k: _Done(1, '{"loggedIn": false}'))
    ok, why = detect.logged_in("claude-code", "claude.exe")
    assert ok is False and why


def test_logged_in_cli_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("нет файла")

    monkeypatch.setattr(detect.subprocess, "run", boom)
    ok, why = detect.logged_in("codex", "codex.exe")
    assert ok is False and why


def test_available_can_skip_the_local_model_probe(monkeypatch):
    """Запуск агента во вкладке «Агент» не ждёт сетевой проверки локальной модели."""
    monkeypatch.setattr(detect, "find_claude", lambda: "C:/claude.exe")
    monkeypatch.setattr(detect, "find_codex", lambda: None)
    monkeypatch.setattr(detect, "local_reachable",
                        lambda url, timeout=0.5: (_ for _ in ()).throw(AssertionError("проверка")))
    av = detect.available(base_url="http://127.0.0.1:1234/v1", probe_local=False)
    assert av["claude-code"] == {"found": True, "path": "C:/claude.exe"}
    assert av["openai-compatible"] == {"found": None, "base_url": "http://127.0.0.1:1234/v1"}
