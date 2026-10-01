import asyncio
import subprocess

from meet.llm import codex


class FakePopen:
    calls = []
    answer = "ответ"
    code = 0
    stderr = b""
    timeout = False

    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.kw = kw
        self.pid = 4242
        self.returncode = None
        self.input = None
        FakePopen.calls.append(self)

    def communicate(self, input=None, timeout=None):
        if input is not None:
            self.input = input
        if FakePopen.timeout and self.returncode is None:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        out = self.cmd[self.cmd.index("--output-last-message") + 1]
        if FakePopen.answer is not None and self.returncode is None:
            with open(out, "w", encoding="utf-8") as f:
                f.write(FakePopen.answer)
        if self.returncode is None:
            self.returncode = FakePopen.code
        return b"", FakePopen.stderr

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def _setup(monkeypatch, **attrs):
    FakePopen.calls = []
    FakePopen.answer, FakePopen.code = "ответ", 0
    FakePopen.stderr, FakePopen.timeout = b"", False
    for k, v in attrs.items():
        setattr(FakePopen, k, v)
    monkeypatch.setattr(codex, "find_codex", lambda: "C:/codex.exe")
    monkeypatch.setattr(codex.subprocess, "Popen", FakePopen)
    killed = []

    def fake_kill(proc):
        killed.append(proc.pid)
        proc.kill()

    monkeypatch.setattr(codex, "_kill_tree", fake_kill)
    return killed


def _run(**kw):
    kw.setdefault("system_prompt", "Ты помощник.")
    return asyncio.run(codex.run("Вопрос?", **kw))


def test_success(monkeypatch, tmp_path):
    _setup(monkeypatch)
    reply = _run(cwd=tmp_path, allowed_dirs=(tmp_path,))
    assert reply.text == "ответ" and reply.error is None
    call = FakePopen.calls[0]
    cmd = call.cmd
    assert cmd[0] == "C:/codex.exe" and cmd[1] == "exec"
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in cmd
    assert cmd[-1] == "-"
    assert call.input.decode("utf-8") == "Ты помощник.\n\nВопрос?"


def test_workdir_is_knowledge_dir(monkeypatch, tmp_path):
    _setup(monkeypatch)
    rec, kb = tmp_path / "rec", tmp_path / "kb"
    rec.mkdir()
    kb.mkdir()
    _run(cwd=rec, allowed_dirs=(rec, kb))
    cmd = FakePopen.calls[0].cmd
    assert cmd[cmd.index("-C") + 1] == str(kb)


def test_missing_knowledge_dir_uses_cwd(monkeypatch, tmp_path):
    _setup(monkeypatch)
    rec = tmp_path / "rec"
    rec.mkdir()
    _run(cwd=rec, allowed_dirs=(rec, tmp_path / "нет такой"))
    cmd = FakePopen.calls[0].cmd
    assert cmd[cmd.index("-C") + 1] == str(rec)


def test_nonzero_exit_reports_stderr_tail(monkeypatch, tmp_path):
    _setup(monkeypatch, code=1, answer=None,
           stderr=("x" * 1000 + "ошибка входа").encode("utf-8"))
    reply = _run(cwd=tmp_path)
    assert reply.text == ""
    assert reply.error.endswith("ошибка входа")
    assert len(reply.error) <= 500


def test_empty_answer_is_error(monkeypatch, tmp_path):
    _setup(monkeypatch, answer="  \n")
    reply = _run(cwd=tmp_path)
    assert reply.text == "" and reply.error


def test_timeout_kills_tree(monkeypatch, tmp_path):
    killed = _setup(monkeypatch, timeout=True)
    reply = _run(cwd=tmp_path, timeout_s=30)
    assert reply.error == "таймаут вызова модели"
    assert reply.text == ""
    assert killed == [4242]


def test_codex_not_found(monkeypatch, tmp_path):
    _setup(monkeypatch)
    monkeypatch.setattr(codex, "find_codex", lambda: None)
    reply = _run(cwd=tmp_path)
    assert reply.text == "" and "Codex" in reply.error
    assert FakePopen.calls == []
