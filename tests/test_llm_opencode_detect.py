"""Поиск OpenCode на машине и проверка входа без вызова модели (meet.llm.detect)."""

import subprocess
from pathlib import Path

from meet.llm import detect


class _Done:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def _win(monkeypatch, tmp_path, found=None):
    """Windows: PATH подменён, домашняя папка, APPDATA и ProgramData — во временной папке."""
    monkeypatch.setattr(detect, "_WINDOWS", True)
    found = found or {}
    monkeypatch.setattr(detect.shutil, "which", lambda name: found.get(name))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "programdata"))


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _unix_cli(home: Path, rel: str, name: str) -> Path:
    exe = home.joinpath(*rel.split("/")) / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"#!/bin/sh\n")
    exe.chmod(0o755)
    return exe


def test_prefers_exe_in_path(monkeypatch, tmp_path):
    _win(monkeypatch, tmp_path, {"opencode.exe": "C:/scoop/shims/opencode.exe",
                                 "opencode.cmd": "C:/npm/opencode.cmd"})
    assert detect.find_opencode() == "C:/scoop/shims/opencode.exe"


def test_takes_the_native_binary_behind_the_npm_shim(monkeypatch, tmp_path):
    # npm -g кладёт в PATH сценарий opencode.cmd, а в node_modules рядом —
    # настоящую программу (её копирует postinstall пакета opencode-ai): берём её.
    shim = _file(tmp_path / "npm" / "opencode.cmd")
    exe = _file(tmp_path / "npm" / "node_modules" / "opencode-ai" / "bin" / "opencode.exe")
    _win(monkeypatch, tmp_path, {"opencode.cmd": str(shim)})
    assert detect.find_opencode() == str(exe)


def test_platform_package_when_postinstall_did_not_run(monkeypatch, tmp_path):
    shim = _file(tmp_path / "npm" / "opencode.cmd")
    exe = _file(tmp_path / "npm" / "node_modules" / "opencode-ai" / "node_modules"
                / "opencode-windows-x64" / "bin" / "opencode.exe")
    _win(monkeypatch, tmp_path, {"opencode.cmd": str(shim)})
    assert detect.find_opencode() == str(exe)


def test_npm_shim_alone_is_still_found(monkeypatch, tmp_path):
    # Фоновым задачам годится и сценарий; вкладка «Агент» его не запускает (pty.rs).
    shim = _file(tmp_path / "npm" / "opencode.cmd")
    _win(monkeypatch, tmp_path, {"opencode.cmd": str(shim)})
    assert detect.find_opencode() == str(shim)


def test_install_dirs_outside_path_on_windows(monkeypatch, tmp_path):
    _win(monkeypatch, tmp_path)
    exe = _file(tmp_path / "home" / ".opencode" / "bin" / "opencode.exe")
    assert detect.find_opencode() == str(exe)
    exe.unlink()
    npm = _file(tmp_path / "appdata" / "npm" / "node_modules" / "opencode-ai" / "bin" / "opencode.exe")
    assert detect.find_opencode() == str(npm)
    npm.unlink()
    scoop = _file(tmp_path / "home" / "scoop" / "shims" / "opencode.exe")
    assert detect.find_opencode() == str(scoop)
    scoop.unlink()
    choco = _file(tmp_path / "programdata" / "chocolatey" / "bin" / "opencode.exe")
    assert detect.find_opencode() == str(choco)


def test_none_on_windows(monkeypatch, tmp_path):
    _win(monkeypatch, tmp_path)
    assert detect.find_opencode() is None


def test_posix_path_then_install_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(detect, "_WINDOWS", False)
    monkeypatch.setattr(detect.shutil, "which",
                        lambda name: "/usr/bin/opencode" if name == "opencode" else None)
    assert detect.find_opencode() == "/usr/bin/opencode"
    # Приложение из Finder: PATH без ~/.opencode/bin — находим установку скриптом.
    exe = _unix_cli(tmp_path, ".opencode/bin", "opencode")
    monkeypatch.setattr(detect.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(detect, "_UNIX_DIRS",
                        tuple(d for d in detect._UNIX_DIRS if d.startswith("~")))
    assert detect.find_opencode() == str(exe)


def test_unix_dirs_cover_the_opencode_installer():
    dirs = [str(d).replace("\\", "/") for d in detect._unix_dirs("/Users/u")]
    assert "/Users/u/.opencode/bin" in dirs


def test_available_lists_opencode(monkeypatch):
    monkeypatch.setattr(detect, "find_claude", lambda: None)
    monkeypatch.setattr(detect, "find_codex", lambda: None)
    monkeypatch.setattr(detect, "find_opencode", lambda: "C:/oc/opencode.exe")
    av = detect.available(probe_local=False)
    assert av["opencode"] == {"found": True, "path": "C:/oc/opencode.exe"}


def test_not_found_text_names_the_install_page():
    assert "opencode.ai" in detect.OPENCODE_NOT_FOUND


# --- вход -------------------------------------------------------------------------


def test_auth_file_follows_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(detect.Path, "home", lambda: tmp_path)
    assert detect.opencode_auth_file() == tmp_path / ".local" / "share" / "opencode" / "auth.json"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert detect.opencode_auth_file() == tmp_path / "data" / "opencode" / "auth.json"


def _auth(tmp_path, monkeypatch, text=None):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCODE_AUTH_CONTENT", raising=False)
    auth = tmp_path / "opencode" / "auth.json"
    if text is not None:
        auth.parent.mkdir(parents=True, exist_ok=True)
        auth.write_text(text, encoding="utf-8")
    return auth


def test_logged_in_reads_the_auth_file_without_starting_opencode(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("вход проверяется по файлу, OpenCode не запускается")

    monkeypatch.setattr(detect.subprocess, "run", boom)
    monkeypatch.setattr(detect.subprocess, "Popen", boom)
    _auth(tmp_path, monkeypatch)
    ok, why = detect.logged_in("opencode", "C:/oc/opencode.exe")
    assert ok is False and "opencode auth login" in why and "auth.json" in why
    _auth(tmp_path, monkeypatch, '{"anthropic": {"type": "oauth", "refresh": "r", "access": "a", "expires": 1}}')
    assert detect.logged_in("opencode", "C:/oc/opencode.exe") == (True, None)


def test_random_provider_keys_in_the_environment_are_not_a_login(monkeypatch, tmp_path):
    # HF_TOKEN (Hugging Face для разделения на спикеров), GITHUB_TOKEN, AWS_* —
    # `opencode auth list` посчитал бы их; нам они ничего не говорят.
    _auth(tmp_path, monkeypatch)
    for name in ("HF_TOKEN", "GITHUB_TOKEN", "AWS_PROFILE", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "x")
    assert detect.logged_in("opencode", "opencode")[0] is False
    assert detect.opencode_auth_present() is False


def test_auth_present_for_the_configured_provider(monkeypatch, tmp_path):
    _auth(tmp_path, monkeypatch, '{"anthropic": {"type": "api", "key": "sk"}}')
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert detect.opencode_auth_present("anthropic") is True
    assert detect.opencode_auth_present("openai") is False
    monkeypatch.setenv("HF_TOKEN", "x")
    assert detect.opencode_auth_present("openai") is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    assert detect.opencode_auth_present("openai") is True
    assert detect.opencode_provider_env("openrouter") == ("OPENROUTER_API_KEY",)
    assert detect.opencode_provider_env("google-vertex") == ("GOOGLE_VERTEX_API_KEY",)
    assert "GEMINI_API_KEY" in detect.opencode_provider_env("google")


def test_auth_file_check(monkeypatch, tmp_path):
    auth = _auth(tmp_path, monkeypatch)
    assert detect.opencode_auth_present() is False
    auth.parent.mkdir(parents=True)
    for text in ("{}", "не json", "[1]", '{"x": "y"}'):
        auth.write_text(text, encoding="utf-8")
        assert detect.opencode_auth_present() is False, text
    auth.write_text('{"openai": {"type": "api", "key": "sk"}}', encoding="utf-8")
    assert detect.opencode_auth_present() is True
    auth.unlink()
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", '{"openai": {"type": "api", "key": "sk"}}')
    assert detect.opencode_auth_present() is True


# --- модель из настроек есть у OpenCode (`opencode models <провайдер>`) ---------------


def _run_tree(monkeypatch, fn):
    from meet.llm import base

    monkeypatch.setattr(base, "run_tree", fn)


def test_model_listed(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["timeout"] = cmd, kw.get("timeout")
        return 0, b"openai/gpt-5\nopenai/gpt-5-mini\n", b""

    _run_tree(monkeypatch, run)
    assert detect.opencode_model_listed("C:/oc/opencode.exe", "openai/gpt-5", "none") == (True, None)
    assert seen["cmd"] == ["C:/oc/opencode.exe", "models", "openai"]
    ok, why = detect.opencode_model_listed("C:/oc/opencode.exe", "openai/gpt-6", "none")
    assert ok is False and "openai/gpt-6" in why and "opencode models openai" in why


def test_model_provider_not_connected(monkeypatch):
    _run_tree(monkeypatch, lambda *a, **k: (1, b"", b"Error: Provider not found: anthropic"))
    ok, why = detect.opencode_model_listed("opencode", "anthropic/claude-sonnet-4-5", None)
    assert ok is False and "anthropic" in why and "opencode auth login" in why


def test_model_listing_times_out(monkeypatch):
    def boom(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 60)

    _run_tree(monkeypatch, boom)
    ok, why = detect.opencode_model_listed("opencode", "openai/gpt-5", None)
    assert ok is False and "TimeoutExpired" in why


def test_run_tree_kills_the_whole_tree_on_timeout(monkeypatch):
    from meet.llm import base

    killed = []

    class Slow:
        pid = 77

        def __init__(self, cmd, **kw):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("x", timeout)
            return b"", b""

    monkeypatch.setattr(base.subprocess, "Popen", Slow)
    monkeypatch.setattr(base, "kill_tree", lambda proc: killed.append(proc.pid))
    try:
        base.run_tree(["opencode-fake"], timeout=0.01)
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("ждали TimeoutExpired")
    assert killed == [77]
