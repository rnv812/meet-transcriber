import socket
import sys
from pathlib import Path

import pytest

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
    monkeypatch.setattr(detect, "_unix_dirs", lambda home: [])
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


def _unix_cli(home, rel, name):
    exe = home.joinpath(*rel.split("/")) / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"#!/bin/sh\n")
    exe.chmod(0o755)
    return exe


def _no_path(monkeypatch, home):
    # PATH launchd: в нём CLI нет; папки установки — только в `home`.
    monkeypatch.setattr(detect, "_WINDOWS", False)
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(detect, "_UNIX_DIRS",
                        tuple(d for d in detect._UNIX_DIRS if d.startswith("~")))


def test_find_claude_posix_native_install_outside_path(monkeypatch, tmp_path):
    # Приложение из Finder: PATH без ~/.local/bin — находим родную установку.
    exe = _unix_cli(tmp_path, ".local/bin", "claude")
    _no_path(monkeypatch, tmp_path)
    assert detect.find_claude() == str(exe)
    # Папку CLI — в PATH процесса: npm-сценарию нужен node рядом.
    assert str(exe.parent) in detect.os.environ["PATH"].split(detect.os.pathsep)


def test_find_claude_posix_order_of_install_dirs(monkeypatch, tmp_path):
    _unix_cli(tmp_path, ".bun/bin", "claude")
    old = _unix_cli(tmp_path, ".claude/local", "claude")
    _no_path(monkeypatch, tmp_path)
    assert detect.find_claude() == str(old)


def test_find_claude_posix_nvm_newest_first(monkeypatch, tmp_path):
    _unix_cli(tmp_path, ".nvm/versions/node/v9.11.2/bin", "claude")
    new = _unix_cli(tmp_path, ".nvm/versions/node/v22.1.0/bin", "claude")
    _no_path(monkeypatch, tmp_path)
    assert detect.find_claude() == str(new)


@pytest.mark.skipif(sys.platform == "win32", reason="бита исполнения на Windows нет: X_OK всегда истина")
def test_find_claude_posix_skips_a_file_without_exec_bit(monkeypatch, tmp_path):
    # Не исполняемый — не CLI: следующая папка, а не «найден» с отказом запуска.
    plain = _unix_cli(tmp_path, ".local/bin", "claude")
    plain.chmod(0o644)
    _no_path(monkeypatch, tmp_path)
    assert detect.find_claude() is None
    runnable = _unix_cli(tmp_path, ".bun/bin", "claude")
    assert detect.find_claude() == str(runnable)


def test_find_claude_posix_skips_folders_and_missing(monkeypatch, tmp_path):
    (tmp_path / ".local" / "bin" / "claude").mkdir(parents=True)  # папка, не файл
    _no_path(monkeypatch, tmp_path)
    assert detect.find_claude() is None


def test_find_claude_posix_path_wins_over_fallback(monkeypatch, tmp_path):
    _unix_cli(tmp_path, ".local/bin", "claude")
    _no_path(monkeypatch, tmp_path)
    monkeypatch.setattr(detect.shutil, "which",
                        lambda name: "/usr/bin/claude" if name == "claude" else None)
    assert detect.find_claude() == "/usr/bin/claude"


def test_find_codex_posix_install_dir(monkeypatch, tmp_path):
    exe = _unix_cli(tmp_path, ".npm-global/bin", "codex")
    _no_path(monkeypatch, tmp_path)
    assert detect.find_codex() == str(exe)


def test_find_codex_windows_has_no_unix_fallback(monkeypatch, tmp_path):
    _unix_cli(tmp_path, ".local/bin", "codex")
    _no_path(monkeypatch, tmp_path)
    monkeypatch.setattr(detect, "_WINDOWS", True)
    assert detect.find_codex() is None


def test_unix_dirs_cover_common_installs():
    dirs = [str(d).replace("\\", "/") for d in detect._unix_dirs("/Users/u")]
    for d in ("/Users/u/.local/bin", "/Users/u/.claude/local", "/opt/homebrew/bin",
              "/usr/local/bin", "/Users/u/.npm-global/bin", "/Users/u/.bun/bin",
              "/Users/u/.volta/bin"):
        assert d in dirs, d


def test_claude_not_found_names_the_platform_program(monkeypatch):
    monkeypatch.setattr(detect, "_WINDOWS", True)
    assert "claude.exe" in detect.claude_not_found()
    assert "claude.cmd" in detect.claude_not_found(detail=True)
    monkeypatch.setattr(detect, "_WINDOWS", False)
    for text in (detect.claude_not_found(), detect.claude_not_found(detail=True)):
        assert ".exe" not in text and ".cmd" not in text
        assert "~/.local/bin" in text and "Homebrew" in text


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
    monkeypatch.setattr(detect, "find_opencode", lambda: None)
    monkeypatch.setattr(detect, "local_reachable", lambda url, timeout=0.5, via_proxy=False: False)
    av = detect.available(base_url="http://127.0.0.1:1234/v1")
    assert av == {
        "claude-code": {"found": False, "path": None},
        "codex": {"found": True, "path": "C:/codex.exe"},
        "opencode": {"found": False, "path": None},
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
    monkeypatch.setattr(detect, "find_opencode", lambda: None)
    monkeypatch.setattr(detect, "local_reachable",
                        lambda url, timeout=0.5, via_proxy=False: (_ for _ in ()).throw(AssertionError("проверка")))
    av = detect.available(base_url="http://127.0.0.1:1234/v1", probe_local=False)
    assert av["claude-code"] == {"found": True, "path": "C:/claude.exe"}
    assert av["openai-compatible"] == {"found": None, "base_url": "http://127.0.0.1:1234/v1"}
