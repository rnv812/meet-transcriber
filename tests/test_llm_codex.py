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
    assert "--ephemeral" in cmd
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


def test_system_proxy_reaches_codex(monkeypatch, tmp_path):
    from meet import netproxy

    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(netproxy, "read_registry",
                        lambda: {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:3067"})
    _setup(monkeypatch)
    _run(cwd=tmp_path)
    env = {k.upper(): v for k, v in FakePopen.calls[0].kw["env"].items()}
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3067"
    assert "PATH" in env  # остальное окружение унаследовано


def test_proxy_none_strips_inherited_for_codex(monkeypatch, tmp_path):
    monkeypatch.setenv("HTTPS_PROXY", "http://10.1.1.1:3128")
    _setup(monkeypatch)
    _run(cwd=tmp_path, proxy="none")
    env = FakePopen.calls[0].kw["env"]
    assert not any(k.upper() == "HTTPS_PROXY" for k in env)


def test_explicit_proxy_for_codex(monkeypatch, tmp_path):
    _setup(monkeypatch)
    _run(cwd=tmp_path, proxy="http://10.1.1.1:3128")
    env = {k.upper(): v for k, v in FakePopen.calls[0].kw["env"].items()}
    assert env["HTTPS_PROXY"] == "http://10.1.1.1:3128"


def test_connection_error_gets_proxy_hint(monkeypatch, tmp_path):
    from meet import netproxy

    _setup(monkeypatch, code=1, answer=None,
           stderr=b"stream error: error sending request for url (https://api.example.com/v1)")
    reply = _run(cwd=tmp_path)
    assert reply.error.endswith(netproxy.HINT)


def test_effort_goes_as_config_override_only_when_set(monkeypatch, tmp_path):
    _setup(monkeypatch)
    _run(cwd=tmp_path, effort="low")
    cmd = FakePopen.calls[0].cmd
    assert cmd[cmd.index("-c") + 1] == 'model_reasoning_effort="low"'
    assert cmd[-1] == "-"
    _run(cwd=tmp_path)
    assert "-c" not in FakePopen.calls[1].cmd


def test_codex_child_env_has_no_session_markers(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CODEX_HOME", "D:/m9-test/codex")
    _run()
    env = FakePopen.calls[0].kw["env"]
    assert "CODEX_SANDBOX_NETWORK_DISABLED" not in env
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert env["CODEX_HOME"] == "D:/m9-test/codex"
    # Свой процесс резидента не трогаем: чистится только окружение Codex.
    import os
    assert os.environ["CODEX_SANDBOX_NETWORK_DISABLED"] == "1"
    # Codex exec — без сохранения сеанса, как и раньше (--ephemeral).
    assert "--ephemeral" in FakePopen.calls[0].cmd


def test_every_background_codex_call_is_ephemeral(monkeypatch):
    """Вкладка «Агент» продолжает Codex через `codex resume --last`: это
    безопасно, только пока фоновые вызовы Codex не сохраняют сеансов. Все они
    (итоги, анализ, названия, профили, живой ассистент, «Проверить») идут
    через codex.run → `codex exec --ephemeral`."""
    from meet.llm import check

    _setup(monkeypatch)
    monkeypatch.setattr(check.detect, "find_codex", lambda: "C:/codex.exe")
    monkeypatch.setattr(check.detect, "logged_in", lambda name, path: (True, None))
    _run()
    asyncio.run(check._check_codex())
    assert len(FakePopen.calls) == 2
    for call in FakePopen.calls:
        assert call.cmd[1] == "exec" and "--ephemeral" in call.cmd


def test_session_kwargs_are_accepted_and_ignored(monkeypatch):
    """Вопросы живого ассистента шлют `session_id`; у Codex сессий нет — id
    не возвращается, и вопросы идут с памятью диалога в промпте."""
    _setup(monkeypatch)
    reply = _run(session_id="0b6f8a52-3c1d-4e2f-9a7b-1c2d3e4f5a6b", resume=None)
    assert reply.text == "ответ" and reply.session_id is None
    assert not any("session" in a for a in FakePopen.calls[0].cmd)


# --- V4: изображения, сохраняемый сеанс, продолжение -------------------------------

SID = "01a112eb-542d-7fd3-bba6-49e7d367cbfb"
BANNER = (f"OpenAI Codex v0.159.0\n--------\nworkdir: C:/x\nsession id: {SID}\n--------\n"
          "user\nВопрос?\n").encode("utf-8")


def _images(tmp_path, *names):
    out = []
    for name in names:
        p = tmp_path / name
        p.write_bytes(b"\x89PNG")
        out.append(p)
    return out


def test_images_go_as_equals_flags_before_stdin_dash(monkeypatch, tmp_path):
    """`-i <FILE>...` съел бы `-` (prompt из stdin) как ещё одно изображение —
    только `--image=<путь>`, по флагу на файл, `-` — последним."""
    _setup(monkeypatch)
    a, b = _images(tmp_path, "a.png", "b.jpg")
    _run(cwd=tmp_path, images=[a, b], effort="low")
    cmd = FakePopen.calls[0].cmd
    assert cmd[-1] == "-"
    images = [x for x in cmd if x.startswith("--image")]
    assert images == [f"--image={a}", f"--image={b}"]
    assert "-i" not in cmd
    assert cmd.index(images[-1]) == len(cmd) - 2       # сразу перед `-`
    assert "--ephemeral" in cmd                          # сеанс не нужен — не сохраняем


def test_unusable_images_are_skipped(monkeypatch, tmp_path):
    _setup(monkeypatch)
    good, comma, bmp = _images(tmp_path, "ok.png", "a,b.png", "c.bmp")
    _run(cwd=tmp_path, images=[good, comma, bmp, tmp_path / "нет.png"])
    images = [x for x in FakePopen.calls[0].cmd if x.startswith("--image")]
    assert images == [f"--image={good}"]


def test_keep_session_drops_ephemeral_and_returns_the_id(monkeypatch, tmp_path):
    _setup(monkeypatch, stderr=BANNER)
    reply = _run(cwd=tmp_path, keep_session=True)
    cmd = FakePopen.calls[0].cmd
    assert "--ephemeral" not in cmd and "resume" not in cmd
    assert reply.text == "ответ" and reply.session_id == SID


def test_resume_command_order(monkeypatch, tmp_path):
    """`--sandbox` и `-C` — до подкоманды (у `exec resume` их нет), id — перед `-`;
    системный промпт уже в сеансе — в stdin только сообщение."""
    _setup(monkeypatch, stderr=BANNER)
    (img,) = _images(tmp_path, "a.png")
    reply = _run(cwd=tmp_path, resume=SID, images=[img], effort="low")
    cmd = FakePopen.calls[0].cmd
    i = cmd.index("resume")
    assert cmd[:2] == ["C:/codex.exe", "exec"]
    assert cmd.index("--sandbox") < i and cmd.index("-C") < i
    assert cmd.index("--output-last-message") > i and cmd.index("-c") > i
    assert cmd[-3:] == [f"--image={img}", SID, "-"]
    assert "--ephemeral" not in cmd
    assert FakePopen.calls[0].input.decode("utf-8") == "Вопрос?"
    assert reply.session_id == SID and not reply.resume_failed


def test_unknown_session_is_a_resume_failure(monkeypatch, tmp_path):
    from meet.llm.base import RESUME_ERROR

    _setup(monkeypatch, code=1, answer=None, stderr=(
        f"Error: thread/resume: thread/resume failed: no rollout found for thread id {SID} "
        "(code -32600)").encode("utf-8"))
    reply = _run(cwd=tmp_path, resume=SID)
    assert reply.resume_failed and reply.error.startswith(RESUME_ERROR)
    assert "no rollout found" in reply.error


def test_other_failure_while_resuming_is_not_a_resume_failure(monkeypatch, tmp_path):
    _setup(monkeypatch, code=1, answer=None, stderr=b"unexpected status 401 Unauthorized")
    reply = _run(cwd=tmp_path, resume=SID)
    assert reply.error and not reply.resume_failed


def test_resume_by_name_is_refused_without_a_process(monkeypatch, tmp_path):
    """Имя вместо UUID Codex молча начал бы новым сеансом (0.159.0) — не зовём."""
    _setup(monkeypatch)
    reply = _run(cwd=tmp_path, resume="meeting-thread")
    assert reply.resume_failed and FakePopen.calls == []


def test_session_id_parsed_from_banner():
    assert codex.session_from("", f"x\nsession id: {SID}\n") == SID
    assert codex.session_from("нет id") is None


def test_cancel_kills_the_codex_tree(monkeypatch, tmp_path):
    """«Стоп» у Codex — отмена задачи: дерево процессов убито (v4-design §3.4)."""
    import threading

    import pytest

    killed = _setup(monkeypatch)
    gate, started = threading.Event(), threading.Event()

    def communicate(self, input=None, timeout=None):
        started.set()
        gate.wait(10)
        self.returncode = -9
        return b"", b""

    monkeypatch.setattr(FakePopen, "communicate", communicate)
    monkeypatch.setattr(FakePopen, "kill", lambda self: gate.set())

    async def scenario():
        task = asyncio.create_task(codex.run("x", system_prompt="s", cwd=tmp_path))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert killed == [4242]
