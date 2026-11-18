import asyncio
import json
import subprocess

import pytest

from meet.llm import codex


class FakePopen:
    calls = []
    answer = "ответ"
    code = 0
    stderr = b""
    stdout = b""
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
        return FakePopen.stdout, FakePopen.stderr

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def _setup(monkeypatch, **attrs):
    FakePopen.calls = []
    FakePopen.answer, FakePopen.code = "ответ", 0
    FakePopen.stderr, FakePopen.timeout, FakePopen.stdout = b"", False, b""
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


def _png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def _images(tmp_path, *names):
    out = []
    for name in names:
        p = tmp_path / name
        p.write_bytes(_png())
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
    bmp.write_bytes(b"BM")
    reply = _run(cwd=tmp_path, images=[good, comma, bmp, tmp_path / "нет.png"])
    images = [x for x in FakePopen.calls[0].cmd if x.startswith("--image")]
    assert images == [f"--image={good}"]
    # Пропущенное видно: окну — dropped_images и notes, модели — пометка в тексте.
    assert set(reply.dropped_images) == {str(comma), str(bmp), str(tmp_path / "нет.png")}
    assert len(reply.notes) == 3 and all("не отправлено" in n for n in reply.notes)
    stdin = FakePopen.calls[0].input.decode("utf-8")
    assert "«c.bmp» не отправлено" in stdin and "запятая" in stdin


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


# --- Fix round 1 ---------------------------------------------------------------------

THREAD = f'{{"type":"thread.started","thread_id":"{SID}"}}\n{{"type":"turn.started"}}\n'.encode()


def test_saved_sessions_use_json_events_and_a_fixed_neutral_folder(monkeypatch, tmp_path):
    """I6: -C сохраняемого сеанса — своя постоянная папка, не папка встречи и
    не база знаний вызывающего; id — из события thread.started (--json)."""
    _setup(monkeypatch, stdout=THREAD)
    monkeypatch.setattr(codex.tempdirs, "system_temp", lambda: tmp_path)
    rec, kb = tmp_path / "rec", tmp_path / "kb"
    rec.mkdir()
    kb.mkdir()
    reply = _run(cwd=rec, allowed_dirs=(rec, kb), keep_session=True)
    again = _run(cwd=rec, allowed_dirs=(rec, kb), resume=SID)
    keep = str(tmp_path / codex.KEEP_WORKDIR)
    for call in FakePopen.calls:
        assert call.cmd[call.cmd.index("-C") + 1] == keep
        assert "--json" in call.cmd
    assert reply.session_id == SID and again.session_id == SID and not again.resume_failed
    # Без сеанса — как раньше: папка базы знаний, без --json.
    _run(cwd=rec, allowed_dirs=(rec, kb))
    plain = FakePopen.calls[-1].cmd
    assert plain[plain.index("-C") + 1] == str(kb) and "--json" not in plain


def test_session_id_falls_back_to_the_banner(monkeypatch, tmp_path):
    _setup(monkeypatch, stderr=BANNER)
    assert _run(cwd=tmp_path, keep_session=True).session_id == SID


def test_resume_that_lands_in_another_session_is_a_resume_failure(monkeypatch, tmp_path):
    """I2: Codex сообщил другой id — сеанс начат заново (без системного
    промпта): ответ и чужой id не используются."""
    other = "01a112eb-0000-7000-8000-000000000000"
    _setup(monkeypatch, stdout=f'{{"type":"thread.started","thread_id":"{other}"}}\n'.encode())
    reply = _run(cwd=tmp_path, resume=SID)
    assert reply.resume_failed and reply.text == "" and reply.session_id is None
    assert other in reply.error


def test_errors_come_from_json_events_when_stderr_is_empty(monkeypatch, tmp_path):
    _setup(monkeypatch, code=1, answer=None, stdout=THREAD + (
        b'{"type":"error","message":"Reconnecting... 1/5"}\n'
        b'{"type":"turn.failed","error":{"message":"unexpected status 401 Unauthorized"}}\n'))
    reply = _run(cwd=tmp_path, keep_session=True)
    assert "401 Unauthorized" in reply.error and not reply.resume_failed


def test_deny_paths_are_a_prompt_rule_every_turn(monkeypatch, tmp_path):
    """I5: у Codex запрета по путям нет — правило в тексте и при продолжении."""
    _setup(monkeypatch, stdout=THREAD)
    private = tmp_path / "kb" / "Личное"
    _run(cwd=tmp_path, keep_session=True, deny_paths=[private])
    _run(cwd=tmp_path, resume=SID, deny_paths=[private])
    for call in FakePopen.calls:
        text = call.input.decode("utf-8")
        assert "закрыты" in text and str(private) in text
    from meet import llm

    assert llm.deny_enforced("codex") is False


def test_forget_session_deletes_only_that_rollout(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(codex, "find_codex", lambda: None)  # старый CLI: команды нет
    day = tmp_path / "sessions" / "2026" / "10" / "07"
    day.mkdir(parents=True)
    mine = day / f"rollout-2026-10-07T10-00-00-{SID}.jsonl"
    other = day / "rollout-2026-10-07T10-00-00-01a112eb-0000-7000-8000-000000000000.jsonl"
    mine.write_text("{}")
    other.write_text("{}")
    assert codex.forget_session(SID) == 1
    assert not mine.exists() and other.exists()
    assert codex.forget_session("*") == 0 and other.exists()


def test_forget_session_uses_the_official_delete_command(monkeypatch, tmp_path):
    """`codex delete --force <uuid>` убирает поток и из sqlite Codex; аргументы —
    списком, без шелла, только UUID (путь или «*» до CLI не доходят)."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(codex, "find_codex", lambda: "codex.exe")
    calls = []

    def run(cmd, *, timeout, **kw):
        calls.append(cmd)
        return 0, b"Deleted session", b""

    assert codex.forget_session(SID, run=run) == 1
    assert calls == [["codex.exe", "delete", "--force", SID]]
    for bad in ("*", "../../x", SID + chr(10), "-rf", "C:/Windows"):
        assert codex.forget_session(bad, run=run) == 0
    assert len(calls) == 1

    def failing(cmd, *, timeout, **kw):
        return 1, b"", b"Error: failed to delete session"

    assert codex.forget_session(SID, run=failing) == 0


# --- 0.3.7 (A1, fix round 1): свобода по согласию — только чтение файлов ----------------

ACCESS_SID = "019a1b2c-3d4e-7f00-8a9b-0c1d2e3f4a5b"
FEATURES = ["apps", "browser_use", "computer_use", "in_app_browser"]


def _access_setup(monkeypatch, tmp_path, servers):
    _setup(monkeypatch)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.setattr(codex.tempdirs, "system_temp", lambda: tmp_path / "tmp")
    listed = []

    def fake_list(exe=None, env=None, *, refresh=False):
        listed.append(exe)
        return servers

    monkeypatch.setattr(codex, "mcp_servers", fake_list)
    return listed


def _access_cmd(tmp_path, access, **kw):
    meeting = tmp_path / "meeting"
    meeting.mkdir(exist_ok=True)
    reply = _run(allowed_dirs=(meeting, tmp_path / "kb"), access=access, keep_session=True, **kw)
    assert reply.error is None
    return FakePopen.calls[-1].cmd, meeting


SERVERS = [{"name": "team-jira", "enabled": True, "transport": "stdio"},
           {"name": "team-gitlab", "enabled": True, "transport": "stdio"},
           {"name": "off", "enabled": False, "transport": "stdio"}]


@pytest.mark.parametrize("access", ["none", "read"])
def test_every_free_turn_is_read_only_without_mcp_apps_or_web(monkeypatch, tmp_path, access):
    _access_setup(monkeypatch, tmp_path, SERVERS)
    cmd, meeting = _access_cmd(tmp_path, access)
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    overrides = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
    assert 'mcp_servers.team-jira={enabled=false,command="__meet_disabled_mcp__"}' in overrides
    assert 'mcp_servers.team-gitlab={enabled=false,command="__meet_disabled_mcp__"}' in overrides
    assert not any("off" in o for o in overrides)
    assert "tools.web_search=false" in overrides
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--disable"] == FEATURES
    assert "--ignore-user-config" not in cmd
    assert (cmd[cmd.index("-C") + 1] == str(meeting)) is (access == "none")


@pytest.mark.parametrize("servers", [None, [{"name": "my.server", "enabled": True, "transport": "stdio"}],
                                     [{"name": "имя с пробелом", "enabled": True, "transport": "stdio"}],
                                     [{"name": "ws", "enabled": True, "transport": "websocket"}],
                                     [{"name": "nt", "enabled": True}]])
def test_unreadable_mcp_list_or_unaddressable_name_fails_closed(monkeypatch, tmp_path, servers):
    """Список не прочитался или имя не выключить ключом — без config.toml целиком."""
    _access_setup(monkeypatch, tmp_path, servers)
    cmd, _meeting = _access_cmd(tmp_path, "read")
    assert "--ignore-user-config" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert not any(a.startswith("mcp_servers.") for a in cmd)


def test_http_server_is_disabled_by_url_not_command(monkeypatch, tmp_path):
    """Сервер с url (streamable_http) выключаем заглушкой url: команда в его таблице — «url is not supported for stdio»."""
    servers = [{"name": "web", "enabled": True, "transport": "streamable_http"},
               {"name": "local", "enabled": True, "transport": "stdio"}]
    _access_setup(monkeypatch, tmp_path, servers)
    cmd, _meeting = _access_cmd(tmp_path, "read")
    overrides = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
    assert 'mcp_servers.web={enabled=false,url="http://127.0.0.1:9/__meet_disabled_mcp__"}' in overrides
    assert 'mcp_servers.local={enabled=false,command="__meet_disabled_mcp__"}' in overrides
    assert "--ignore-user-config" not in cmd


def test_mcp_list_keeps_transport_type_for_plugin_servers(monkeypatch):
    """Серверы плагинов не записаны в config.toml: вид транспорта берём из `codex mcp list`."""
    raw = json.dumps([{"name": "plug", "enabled": True, "transport": {"type": "streamable_http", "url": "https://x"}},
                      {"name": "loc", "enabled": True, "transport": {"type": "stdio", "command": "c"}},
                      {"name": "bare", "enabled": True}])
    monkeypatch.setattr(codex, "_mcp_list_json", lambda exe, env: raw)
    monkeypatch.setattr(codex, "_mcp_cache", {})
    assert codex.mcp_servers("C:/codex.exe") == [
        {"name": "plug", "enabled": True, "transport": "streamable_http"},
        {"name": "loc", "enabled": True, "transport": "stdio"},
        {"name": "bare", "enabled": True, "transport": None}]
    args = codex.access_args("read", codex.mcp_servers("C:/codex.exe")[:2])
    assert 'mcp_servers.plug={enabled=false,url="http://127.0.0.1:9/__meet_disabled_mcp__"}' in args
    assert "--ignore-user-config" in codex.access_args("read", codex.mcp_servers("C:/codex.exe"))


def test_access_flags_go_before_resume(monkeypatch, tmp_path):
    _access_setup(monkeypatch, tmp_path, None)
    cmd, meeting = _access_cmd(tmp_path, "none", resume=ACCESS_SID)
    i = cmd.index("resume")
    for flag in ("--sandbox", "-C", "--ignore-user-config"):
        assert cmd.index(flag) < i
    assert all(j < i for j, a in enumerate(cmd) if a == "--disable")
    assert cmd[cmd.index("-C") + 1] == str(meeting)
    assert cmd[-2:] == [ACCESS_SID, "-"]


def test_without_access_the_command_is_as_in_0_3_6():
    assert codex.build_command("codex", "D:/w", "o.txt") == [
        "codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "-C", "D:/w",
        "--output-last-message", "o.txt", "-"]
    assert codex.access_args(None) == ["--sandbox", "read-only"]


def test_mcp_list_is_parsed_cached_and_failure_is_none(monkeypatch):
    calls = []

    def listed(exe, env):
        calls.append(exe)
        return json.dumps([{"name": "team-jira", "enabled": True}, {"name": "x", "enabled": False},
                           {"oops": 1}])

    monkeypatch.setattr(codex, "_mcp_list_json", listed)
    monkeypatch.setattr(codex, "_mcp_cache", {})
    first = codex.mcp_servers("C:/codex.exe")
    assert first == [{"name": "team-jira", "enabled": True, "transport": None},
                     {"name": "x", "enabled": False, "transport": None}]
    assert codex.mcp_servers("C:/codex.exe") == first and calls == ["C:/codex.exe"]
    monkeypatch.setattr(codex, "_mcp_list_json", lambda exe, env: "not json")
    assert codex.mcp_servers("C:/other.exe") is None

    def boom(exe, env):
        raise OSError("нет")

    monkeypatch.setattr(codex, "_mcp_list_json", boom)
    assert codex.mcp_servers("C:/third.exe") is None


# --- 0.4: ход по просьбе (`user`) — автопроверка Codex, правка рабочих папок -------------------


def test_a_user_turn_writes_working_folders_with_auto_review(monkeypatch, tmp_path):
    """USER (0.4): `--approve-for-me` (автопроверка Codex),
    рабочие папки — `--add-dir`, веб-поиск не выключается; MCP пользователя — выключены
    (изменение через MCP не остановить), приложения и браузер — тоже."""
    _access_setup(monkeypatch, tmp_path, SERVERS)
    kb = tmp_path / "kb"
    kb.mkdir()
    meeting = tmp_path / "meeting"
    meeting.mkdir()
    cmd, _m = _access_cmd(tmp_path, "user", work_dirs=(meeting, kb))
    assert "--approve-for-me" in cmd and "--sandbox" not in cmd
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--add-dir"] == [str(meeting), str(kb)]
    overrides = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
    assert 'mcp_servers.team-jira={enabled=false,command="__meet_disabled_mcp__"}' in overrides and "tools.web_search=false" not in overrides
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "--disable"] == FEATURES
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd


def test_user_flags_go_before_resume_and_missing_folders_are_skipped(monkeypatch, tmp_path):
    _access_setup(monkeypatch, tmp_path, SERVERS)
    cmd, meeting = _access_cmd(tmp_path, "user", resume=ACCESS_SID, work_dirs=(tmp_path / "meeting",
                                                                                tmp_path / "нет"))
    i = cmd.index("resume")
    assert "--sandbox" not in cmd
    for flag in ("--approve-for-me", "--add-dir", "-C"):
        assert cmd.index(flag) < i, flag
    assert [cmd[k + 1] for k, a in enumerate(cmd) if a == "--add-dir"] == [str(meeting)]


@pytest.mark.parametrize("access", ["none", "read"])
def test_none_and_read_stay_read_only_even_with_working_folders(monkeypatch, tmp_path, access):
    _access_setup(monkeypatch, tmp_path, SERVERS)
    cmd, _m = _access_cmd(tmp_path, access, work_dirs=(tmp_path / "meeting",))
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--approve-for-me" not in cmd and "--add-dir" not in cmd
    assert codex.access_args(access, SERVERS, work_dirs=("C:/x",)) == codex.access_args(access, SERVERS)


# --- модель (`/model`) и песочница Windows ----------------------------------------------


def test_model_goes_as_m_flag_also_after_resume(monkeypatch, tmp_path):
    """`/model имя` у Codex: `-m имя` в команде, при продолжении — после `resume`
    (флаг есть у обеих подкоманд, сверено с `--help` 0.160.0)."""
    _setup(monkeypatch, stdout=THREAD)
    monkeypatch.setattr(codex.tempdirs, "system_temp", lambda: tmp_path)
    _run(cwd=tmp_path, model="gpt-5.5")
    cmd = FakePopen.calls[-1].cmd
    assert cmd[cmd.index("-m") + 1] == "gpt-5.5" and cmd[-1] == "-"
    _run(cwd=tmp_path, model="gpt-5.5", resume=SID)
    cmd = FakePopen.calls[-1].cmd
    assert cmd.index("-m") > cmd.index("resume") and cmd[cmd.index("-m") + 1] == "gpt-5.5"
    _run(cwd=tmp_path)
    assert "-m" not in FakePopen.calls[-1].cmd           # без модели — как в конфиге Codex


@pytest.mark.parametrize("name", ["gpt&calc", "gpt%PATH%", "a|b", "a b", "-x", 'a"b', "a^b", "a>b", ""])
def test_bad_model_name_is_refused(name):
    if name == "":
        assert codex.model_error(name) is None            # пусто — модель из конфига Codex
        return
    assert "недопустимые символы" in codex.model_error(name)


def test_good_model_names_pass():
    for name in ("gpt-5.5", "gpt-5.1-codex-max", "o3", "openai/gpt-oss-20b", "model:latest"):
        assert codex.model_error(name) is None, name


def test_bad_model_name_never_reaches_a_process(monkeypatch, tmp_path):
    _setup(monkeypatch)
    reply = _run(cwd=tmp_path, model="gpt&calc")
    assert reply.error and "недопустимые символы" in reply.error and FakePopen.calls == []


def _codex_home(monkeypatch, tmp_path, text):
    home = tmp_path / "codex-home"
    home.mkdir(exist_ok=True)
    if text is not None:
        (home / "config.toml").write_text(text, encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


@pytest.mark.parametrize("text, want", [
    ('model = "gpt-5.5"\n[windows]\nsandbox = "elevated"\n', "elevated"),
    ('[windows]\nsandbox = "unelevated"\n', "unelevated"),
    ('model = "gpt-5.5"\n', None),
    ("[windows]\nsandbox = 1\n", None),
    ("[windows\nbroken", None),
    (None, None),
])
def test_windows_sandbox_is_the_only_key_read(monkeypatch, tmp_path, text, want):
    _codex_home(monkeypatch, tmp_path, text)
    assert codex.windows_sandbox() == want


def test_sandbox_missing_only_on_windows(monkeypatch, tmp_path):
    _codex_home(monkeypatch, tmp_path, 'model = "gpt-5.5"\n')
    monkeypatch.setattr(codex, "_ON_WINDOWS", True)
    assert codex.sandbox_missing() is True
    monkeypatch.setattr(codex, "_ON_WINDOWS", False)
    assert codex.sandbox_missing() is False
    _codex_home(monkeypatch, tmp_path, '[windows]\nsandbox = "elevated"\n')
    monkeypatch.setattr(codex, "_ON_WINDOWS", True)
    assert codex.sandbox_missing() is False


@pytest.mark.parametrize("access", ["user", "read"])
def test_ignore_user_config_keeps_the_users_windows_sandbox(monkeypatch, tmp_path, access):
    """Запасной путь без config.toml пользователя: его `[windows] sandbox`
    передаётся явно (`-c`), иначе на Windows ход только читает."""
    _access_setup(monkeypatch, tmp_path, None)
    _codex_home(monkeypatch, tmp_path, '[windows]\nsandbox = "elevated"\n')
    cmd, _m = _access_cmd(tmp_path, access, resume=ACCESS_SID)
    assert "--ignore-user-config" in cmd
    overrides = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
    assert 'windows.sandbox="elevated"' in overrides
    k = [i for i, a in enumerate(cmd) if a == "-c" and cmd[i + 1].startswith("windows.")][0]
    assert k < cmd.index("resume")
    if access == "user":
        assert "--approve-for-me" in cmd and "--sandbox" not in cmd


def test_users_own_config_needs_no_sandbox_override(monkeypatch, tmp_path):
    _access_setup(monkeypatch, tmp_path, SERVERS)
    _codex_home(monkeypatch, tmp_path, '[windows]\nsandbox = "elevated"\n')
    cmd, _m = _access_cmd(tmp_path, "user")
    assert "--ignore-user-config" not in cmd
    assert not any(a.startswith("windows.") for a in cmd)


def test_odd_sandbox_value_is_not_passed(monkeypatch, tmp_path):
    _access_setup(monkeypatch, tmp_path, None)
    _codex_home(monkeypatch, tmp_path, '[windows]\nsandbox = "a\\" b"\n')
    cmd, _m = _access_cmd(tmp_path, "user")
    assert "--ignore-user-config" in cmd and not any(a.startswith("windows.") for a in cmd)


def test_user_turn_is_approve_for_me_never_sandbox():
    """`--approve-for-me` сам ставит workspace-write; с `--sandbox` Codex
    отвергает флаг при разборе — у USER его нет ни в каком виде."""
    for servers in (SERVERS, None):
        for sandbox in (None, "elevated"):
            args = codex.access_args("user", servers, windows_sandbox=sandbox)
            assert "--approve-for-me" in args
            assert not any(a in ("--sandbox", "-s") or a.startswith("--sandbox=") for a in args)


# --- строки инструментов по ходу (`on_event`) ---------------------------------------------


def _line(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


CMD_STARTED = {"type": "item.started", "item": {"id": "item_1", "type": "command_execution",
                                                "command": "npm test", "status": "in_progress"}}
CMD_DONE = {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution",
                                               "command": "npm test", "aggregated_output": "ok\n",
                                               "exit_code": 0, "status": "completed"}}


class StreamPopen:
    """Codex, который пишет события по строке: шаг `"wait"` ждёт `release`,
    `"hang"` — пока процесс не убьют. stdin пишется и закрывается."""

    calls = []
    script = []
    release = None
    answer = "ответ"

    def __init__(self, cmd, **kw):
        import io
        import threading

        self.cmd, self.kw, self.pid, self.returncode = cmd, kw, 5151, None
        self.killed = threading.Event()
        self.done = threading.Event()
        self.stdin = io.BytesIO()
        self.stdin_closed = False
        self.stdin.close = self._close_stdin
        self.stdout = self._out()
        self.stderr = iter([b"warn\n"])
        StreamPopen.calls.append(self)

    def _close_stdin(self):
        self.stdin_closed = True

    def _out(self):
        try:
            for step in StreamPopen.script:
                if step == "wait":
                    while not StreamPopen.release.wait(0.01):
                        if self.killed.is_set():
                            return
                    continue
                if step == "hang":
                    self.killed.wait(10)
                    return
                if self.killed.is_set():
                    return
                yield _line(step)
            out = self.cmd[self.cmd.index("--output-last-message") + 1]
            with open(out, "w", encoding="utf-8") as f:
                f.write(StreamPopen.answer)
        finally:
            self.done.set()

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        if self.returncode is None:
            self.returncode = -9 if self.killed.is_set() else 0
        return self.returncode

    def communicate(self, input=None, timeout=None):
        raise AssertionError("потоковый вызов не ждёт communicate")

    def kill(self):
        self.killed.set()


def _stream_setup(monkeypatch, script):
    import threading

    StreamPopen.calls, StreamPopen.script = [], list(script)
    StreamPopen.release = threading.Event()
    monkeypatch.setattr(codex, "find_codex", lambda: "C:/codex.exe")
    monkeypatch.setattr(codex.subprocess, "Popen", StreamPopen)
    killed = []

    def fake_kill(proc):
        killed.append(proc.pid)
        proc.kill()

    monkeypatch.setattr(codex, "_kill_tree", fake_kill)
    return killed


async def _until(cond, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        assert loop.time() < end, "не дождались"
        await asyncio.sleep(0.005)


def test_tool_row_arrives_while_codex_still_runs(monkeypatch, tmp_path):
    """`item.started` → вызов, `item.completed` → результат, в цикл событий по
    ходу; ход ещё идёт, а строка уже есть. Вызов — с `--json`, stdin записан
    и закрыт; `reply.tools` — как прежде."""
    _stream_setup(monkeypatch, [{"type": "turn.started"}, CMD_STARTED, "wait", CMD_DONE])
    events = []

    async def scenario():
        loop = asyncio.get_running_loop()

        def on_event(ev):
            assert asyncio.get_running_loop() is loop       # в цикле событий, не в потоке чтения
            events.append(ev)

        task = asyncio.create_task(codex.run("Вопрос?", system_prompt="s", cwd=tmp_path, on_event=on_event))
        await _until(lambda: events)
        assert not task.done()
        assert events == [{"type": "tool_use", "id": "item_1", "name": "Bash", "server": None, "tool": "Bash",
                           "input": {"command": "npm test"}, "parent": None}]
        StreamPopen.release.set()
        return await task

    reply = asyncio.run(scenario())
    assert reply.text == "ответ" and reply.error is None
    assert [e["type"] for e in events] == ["tool_use", "tool_result"]
    assert events[1]["ok"] is True and events[1]["output"] == "ok\n"
    call = StreamPopen.calls[0]
    assert "--json" in call.cmd and call.stdin_closed
    assert call.stdin.getvalue().decode("utf-8") == "s\n\nВопрос?"
    assert [e["type"] for e in reply.tools] == ["tool_use", "tool_result"]


def test_cancel_mid_stream_kills_the_tree_and_closes_open_rows(monkeypatch, tmp_path):
    killed = _stream_setup(monkeypatch, [CMD_STARTED, "hang"])
    events = []

    async def scenario():
        task = asyncio.create_task(codex.run("x", system_prompt="s", cwd=tmp_path, on_event=events.append))
        await _until(lambda: events)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _until(lambda: len(events) == 2)

    asyncio.run(scenario())
    assert killed == [5151]
    assert events[1] == {"type": "tool_result", "id": "item_1", "ok": False, "output": "прервано",
                         "truncated": False, "duration_ms": None, "parent": None}


def test_timeout_mid_stream_kills_the_tree(monkeypatch, tmp_path):
    killed = _stream_setup(monkeypatch, [CMD_STARTED, "hang"])
    events = []

    async def scenario():
        reply = await codex.run("x", system_prompt="s", cwd=tmp_path, timeout_s=0.3, on_event=events.append)
        await _until(lambda: len(events) == 2)
        return reply

    reply = asyncio.run(scenario())
    assert reply.error == "таймаут вызова модели" and killed == [5151]
    assert events[1]["ok"] is False and events[1]["output"] == "прервано"


def test_streamed_items_of_every_kind(monkeypatch, tmp_path):
    """Веб-поиск без запроса в начале — строка при завершении (запрос уже
    есть); завершённый без начала — вызов и результат сразу; чужие элементы
    (рассуждения, сообщения) — не строки."""
    _stream_setup(monkeypatch, [
        {"type": "item.started", "item": {"id": "w1", "type": "web_search", "query": ""}},
        {"type": "item.completed", "item": {"id": "r1", "type": "reasoning", "text": "думаю"}},
        {"type": "item.completed", "item": {"id": "w1", "type": "web_search", "query": "codex json"}},
        {"type": "item.started", "item": {"id": "m1", "type": "mcp_tool_call", "server": "jira", "tool": "get",
                                          "arguments": {"key": "A-1"}, "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "m1", "type": "mcp_tool_call", "server": "jira", "tool": "get",
                                            "arguments": {"key": "A-1"}, "status": "failed",
                                            "error": {"message": "нет доступа"}}},
        {"type": "item.completed", "item": {"id": "f1", "type": "file_change", "status": "completed",
                                            "changes": [{"path": "a.md", "kind": "update"}]}},
        {"type": "item.completed", "item": {"id": "a1", "type": "agent_message", "text": "{}"}},
    ])
    events = []
    reply = asyncio.run(codex.run("x", system_prompt="s", cwd=tmp_path, on_event=events.append))
    assert reply.error is None
    assert [(e["type"], e["id"]) for e in events] == [
        ("tool_use", "w1"), ("tool_result", "w1"), ("tool_use", "m1"), ("tool_result", "m1"),
        ("tool_use", "f1"), ("tool_result", "f1")]
    assert events[0]["input"] == {"query": "codex json"}
    assert events[2]["name"] == "mcp__jira__get" and events[3]["ok"] is False and events[3]["output"] == "нет доступа"
    assert events[4]["name"] == "apply_patch" and events[5]["output"] == "update: a.md"


def test_on_event_on_a_closed_loop_is_ignored(monkeypatch, tmp_path):
    """Цикл событий закрыт (ассистент выходит) — события молча пропадают."""
    import threading

    _stream_setup(monkeypatch, [CMD_STARTED, CMD_DONE])
    loop = asyncio.new_event_loop()
    loop.close()
    out = []
    t = threading.Thread(target=lambda: out.append(codex._exec(
        "C:/codex.exe", str(tmp_path), "x", 30, on_event=lambda e: None, loop=loop)))
    t.start()
    t.join(10)
    assert out and out[0].text == "ответ"


def test_without_on_event_the_call_is_as_before(monkeypatch, tmp_path):
    _setup(monkeypatch)
    _run(cwd=tmp_path)
    assert "--json" not in FakePopen.calls[0].cmd
