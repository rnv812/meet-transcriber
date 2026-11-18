"""Дерево процессов CLI-агентов на POSIX: своя группа при запуске, killpg при гибели."""
import signal
import subprocess

from meet.llm import base, claude_stream, codex, detect, opencode


_REAL_MCP_LIST = codex._mcp_list_json  # conftest подменяет его на каждый тест


class _Proc:
    pid = 777

    def __init__(self):
        self.killed = 0

    def kill(self):
        self.killed += 1


def _posix(monkeypatch):
    monkeypatch.setattr(base, "_WINDOWS", False)
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)


def _killpg_log(monkeypatch, pgid):
    calls = []
    monkeypatch.setattr(base.os, "killpg", lambda pid, sig: calls.append((pid, sig)), raising=False)
    monkeypatch.setattr(base.os, "getpgid", lambda pid: pgid(pid), raising=False)
    return calls


def test_popen_kwargs_posix_new_session(monkeypatch):
    _posix(monkeypatch)
    assert base.popen_kwargs() == {"start_new_session": True}


def test_popen_kwargs_windows_no_window(monkeypatch):
    monkeypatch.setattr(base, "_WINDOWS", True)
    assert base.popen_kwargs() == {"creationflags": base._NO_WINDOW}


def test_kill_tree_posix_kills_own_group(monkeypatch):
    _posix(monkeypatch)
    calls = _killpg_log(monkeypatch, lambda pid: pid)
    proc = _Proc()
    base.kill_tree(proc)
    assert calls == [(777, 9)] and proc.killed == 1


def test_kill_tree_posix_foreign_group_only_kills_process(monkeypatch):
    _posix(monkeypatch)
    calls = _killpg_log(monkeypatch, lambda pid: 1)
    proc = _Proc()
    base.kill_tree(proc)
    assert calls == [] and proc.killed == 1


def test_kill_tree_posix_finished_process_does_not_raise(monkeypatch):
    _posix(monkeypatch)

    def gone(pid):
        raise ProcessLookupError

    _killpg_log(monkeypatch, gone)
    proc = _Proc()
    base.kill_tree(proc)
    assert proc.killed == 1


def test_kill_tree_posix_permission_error_swallowed(monkeypatch):
    _posix(monkeypatch)
    monkeypatch.setattr(base.os, "getpgid", lambda pid: pid, raising=False)

    def denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(base.os, "killpg", denied, raising=False)
    proc = _Proc()
    base.kill_tree(proc)
    assert proc.killed == 1


def test_kill_tree_windows_taskkill(monkeypatch):
    monkeypatch.setattr(base, "_WINDOWS", True)
    ran = []
    monkeypatch.setattr(base.subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    calls = _killpg_log(monkeypatch, lambda pid: pid)
    proc = _Proc()
    base.kill_tree(proc)
    assert ran == [["taskkill", "/PID", "777", "/T", "/F"]] and calls == [] and proc.killed == 1


def test_run_tree_starts_new_session_on_posix(monkeypatch):
    _posix(monkeypatch)
    seen = {}

    class P:
        returncode = 0

        def __init__(self, cmd, **kw):
            seen.update(kw)

        def communicate(self, timeout=None):
            return b"o", b"e"

    monkeypatch.setattr(base.subprocess, "Popen", P)
    assert base.run_tree(["x"], timeout=1) == (0, b"o", b"e")
    assert seen["start_new_session"] is True and "creationflags" not in seen


def test_run_tree_timeout_kills_tree(monkeypatch):
    _posix(monkeypatch)
    killed = []

    class P:
        returncode = None

        def __init__(self, cmd, **kw):
            pass

        def communicate(self, timeout=None):
            if not killed:
                raise subprocess.TimeoutExpired("x", timeout)
            return b"", b""

    monkeypatch.setattr(base.subprocess, "Popen", P)
    monkeypatch.setattr(base, "kill_tree", lambda p: killed.append(p))
    try:
        base.run_tree(["x"], timeout=1)
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("TimeoutExpired expected")
    assert len(killed) == 1


def test_codex_mcp_list_goes_through_run_tree(monkeypatch):
    seen = {}

    def fake(cmd, *, timeout, **kw):
        seen.update(cmd=cmd, timeout=timeout, kw=kw)
        return 0, "список".encode(), b""

    monkeypatch.setattr(codex, "run_tree", fake)
    assert _REAL_MCP_LIST("codex", {"A": "1"}) == "список"
    assert seen["cmd"] == ["codex", "mcp", "list", "--json"] and seen["timeout"] == 30
    assert seen["kw"]["env"] == {"A": "1"}


def test_login_status_goes_through_run_tree(monkeypatch):
    seen = []

    def fake(cmd, *, timeout, **kw):
        seen.append((cmd, timeout))
        return 0, b"Logged in", b""

    monkeypatch.setattr(base, "run_tree", fake)
    monkeypatch.setattr(detect.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert detect.logged_in("codex", "codex.exe") == (True, None)
    assert seen == [(["codex.exe", "login", "status"], 20)]


def test_claude_stream_uses_procjob_group_and_kill():
    import inspect
    src = inspect.getsource(claude_stream)
    assert "**procjob.popen_kwargs()" in src and "procjob.kill_tree(proc)" in src
