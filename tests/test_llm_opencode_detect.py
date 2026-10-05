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


AUTH_LIST = (
    "\n┌  Credentials ~/.local/share/opencode/auth.json\n│\n"
    "●  Anthropic oauth\n│\n└  {n} credentials\n"
)


def test_logged_in_by_auth_list(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env")
        return _Done(0, AUTH_LIST.format(n=1))

    monkeypatch.setattr(detect.subprocess, "run", run)
    assert detect.logged_in("opencode", "C:/oc/opencode.exe") == (True, None)
    assert seen["cmd"] == ["C:/oc/opencode.exe", "auth", "list"]
    # Список — из кэша моделей, без похода на models.dev; без цвета.
    assert seen["env"]["OPENCODE_DISABLE_MODELS_FETCH"] == "1"
    assert seen["env"]["NO_COLOR"] == "1"


def test_key_in_environment_counts_as_login(monkeypatch):
    out = (AUTH_LIST.format(n=0)
           + "\n┌  Environment\n│\n●  OpenAI OPENAI_API_KEY\n│\n└  1 environment variable\n")
    monkeypatch.setattr(detect.subprocess, "run", lambda *a, **k: _Done(0, out))
    assert detect.logged_in("opencode", "opencode") == (True, None)


def test_no_credentials(monkeypatch):
    out = "\x1b[2m" + AUTH_LIST.format(n=0) + "\x1b[0m"
    monkeypatch.setattr(detect.subprocess, "run", lambda *a, **k: _Done(0, out))
    ok, why = detect.logged_in("opencode", "opencode")
    assert ok is False and "opencode auth login" in why


def test_unknown_auth_list_output_falls_back_to_the_auth_file(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCODE_AUTH_CONTENT", raising=False)
    monkeypatch.setattr(detect.subprocess, "run", lambda *a, **k: _Done(0, "что-то новое"))
    ok, why = detect.logged_in("opencode", "opencode")
    assert ok is False and "auth.json" in why
    auth = tmp_path / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text('{"anthropic": {"type": "oauth", "refresh": "r", "access": "a", "expires": 1}}',
                    encoding="utf-8")
    assert detect.logged_in("opencode", "opencode") == (True, None)


def test_auth_list_failure_falls_back_to_the_auth_file(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCODE_AUTH_CONTENT", raising=False)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("opencode", 20)

    monkeypatch.setattr(detect.subprocess, "run", boom)
    ok, why = detect.logged_in("opencode", "opencode")
    assert ok is False and why
    auth = tmp_path / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text('{"openai": {"type": "api", "key": "sk"}}', encoding="utf-8")
    assert detect.logged_in("opencode", "opencode") == (True, None)


def test_auth_file_check(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("OPENCODE_AUTH_CONTENT", raising=False)
    auth = tmp_path / "opencode" / "auth.json"
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


def test_model_listed(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env")
        return _Done(0, "openai/gpt-5\nopenai/gpt-5-mini\n")

    monkeypatch.setattr(detect.subprocess, "run", run)
    assert detect.opencode_model_listed("C:/oc/opencode.exe", "openai/gpt-5", "none") == (True, None)
    assert seen["cmd"] == ["C:/oc/opencode.exe", "models", "openai"]
    ok, why = detect.opencode_model_listed("C:/oc/opencode.exe", "openai/gpt-6", "none")
    assert ok is False and "openai/gpt-6" in why and "opencode models openai" in why


def test_model_provider_not_connected(monkeypatch):
    monkeypatch.setattr(detect.subprocess, "run",
                        lambda *a, **k: _Done(1, "", "Error: Provider not found: anthropic"))
    ok, why = detect.opencode_model_listed("opencode", "anthropic/claude-sonnet-4-5", None)
    assert ok is False and "anthropic" in why and "opencode auth login" in why


def test_model_listing_fails(monkeypatch):
    def boom(*a, **k):
        raise OSError("нет файла")

    monkeypatch.setattr(detect.subprocess, "run", boom)
    ok, why = detect.opencode_model_listed("opencode", "openai/gpt-5", None)
    assert ok is False and why
