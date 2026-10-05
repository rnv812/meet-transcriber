"""Провайдер OpenCode: `opencode run --format json` (meet.llm.opencode).

Настоящий OpenCode не запускается: Popen подменён. События в фикстурах —
по формату `opencode run --format json` (packages/opencode/src/cli/cmd/run.ts:
одна строка JSON на событие, {type, timestamp, sessionID, part|error}).
"""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from meet.llm import opencode
from meet.llm.base import EMPTY_ERROR, TIMEOUT_ERROR

SID = "ses_494719016ffe85dkDMj0FPRbHK"


def ev(type_, **fields) -> str:
    return json.dumps({"type": type_, "timestamp": 1767036059338, "sessionID": SID, **fields})


def step_start(msg):
    return ev("step_start", part={"id": "prt_1", "sessionID": SID, "messageID": msg,
                                  "type": "step-start", "snapshot": "71db24a7"})


def text(msg, value):
    return ev("text", part={"id": f"prt_t_{msg}", "sessionID": SID, "messageID": msg,
                            "type": "text", "text": value, "time": {"start": 1, "end": 2}})


def tool(msg, name="read", status="completed"):
    return ev("tool_use", part={"id": "prt_tool", "sessionID": SID, "messageID": msg, "type": "tool",
                                "callID": "c1", "tool": name,
                                "state": {"status": status, "input": {"filePath": "D:/kb/a.md"},
                                          "output": "…", "title": "a.md", "metadata": {},
                                          "time": {"start": 1, "end": 2}}})


def step_finish(msg, reason="stop"):
    return ev("step_finish", part={"id": "prt_f", "sessionID": SID, "messageID": msg,
                                   "type": "step-finish", "reason": reason, "cost": 0.001,
                                   "tokens": {"input": 671, "output": 8, "reasoning": 0,
                                              "cache": {"read": 0, "write": 0}}})


def error(name, message=None, **data):
    body = {"name": name, "data": {**({"message": message} if message else {}), **data}}
    return ev("error", error=body)


SIMPLE = "\n".join([step_start("msg_1"), text("msg_1", "Да"), step_finish("msg_1")]) + "\n"
# Модель сначала пояснила и прочла файл (шаг с tool-calls), ответ — в следующем сообщении.
WITH_TOOL = "\n".join([
    step_start("msg_1"), text("msg_1", "Сейчас посмотрю базу знаний."), tool("msg_1"),
    step_finish("msg_1", "tool-calls"),
    step_start("msg_2"), text("msg_2", "Ответ:"), text("msg_2", " в базе есть."), step_finish("msg_2"),
]) + "\n"


# --- разбор событий ---------------------------------------------------------------


def test_parse_simple_answer():
    got = opencode.parse_events(SIMPLE)
    assert (got.text, got.session_id, got.error) == ("Да", SID, None)


def test_parse_takes_the_last_message_not_the_preamble():
    got = opencode.parse_events(WITH_TOOL)
    # Куски одного сообщения (между ними бывают инструменты) — абзацами, не склеенными.
    assert got.text == "Ответ:\n\nв базе есть."
    assert got.tools == 1


def test_parse_ignores_noise_and_broken_lines():
    out = "\n".join(["Обновление доступно", "{не json", "[1, 2]", "", SIMPLE, '{"type": "reasoning"}'])
    got = opencode.parse_events(out)
    assert got.text == "Да" and got.error is None


def test_parse_error_event_message():
    out = step_start("msg_1") + "\n" + error("APIError", "Rate limit exceeded", statusCode=429,
                                             isRetryable=True) + "\n"
    got = opencode.parse_events(out)
    assert got.error == "OpenCode: Rate limit exceeded"
    assert got.auth is False


@pytest.mark.parametrize("line", [
    error("ProviderAuthError", "No API key found for provider anthropic", providerID="anthropic"),
    error("APIError", "invalid x-api-key", statusCode=401),
    error("UnknownError", "Unauthorized: token expired"),
])
def test_parse_auth_failures(line):
    got = opencode.parse_events(line + "\n")
    assert got.auth is True
    assert "opencode auth login" in got.error


def test_parse_model_not_found():
    line = error("ProviderModelNotFoundError", providerID="anthropic", modelID="claude-x",
                 suggestions=["claude-sonnet-4-5"])
    got = opencode.parse_events(line + "\n")
    assert "anthropic/claude-x" in got.error and "opencode models" in got.error


def test_parse_error_without_data_uses_its_name():
    got = opencode.parse_events(ev("error", error={"name": "MessageAbortedError"}) + "\n")
    assert got.error == "OpenCode: MessageAbortedError"


def test_parse_several_errors_are_joined():
    out = error("APIError", "first") + "\n" + error("APIError", "second") + "\n"
    assert opencode.parse_events(out).error == "OpenCode: first\nsecond"


# --- права и конфиг фонового вызова ------------------------------------------------


def test_no_dirs_means_no_tools_at_all():
    assert opencode.readonly_permission(()) == {"*": "deny"}


def test_dirs_are_read_only(tmp_path):
    kb = tmp_path / "kb"
    perm = opencode.readonly_permission((kb,))
    # Всё, чего нет в списке, запрещено: правка, команды, сеть, подагенты.
    assert list(perm)[0] == "*" and perm["*"] == "deny"
    for name in ("grep", "glob", "list"):
        assert perm[name] == "allow"
    assert perm["read"]["*"] == "allow" and perm["read"]["*.env"] == "deny"
    for name in ("edit", "bash", "webfetch", "task", "write"):
        assert name not in perm
    ext = perm["external_directory"]
    assert list(ext)[0] == "*" and ext["*"] == "deny"
    assert ext[str(kb / "*")] == "allow"


def test_config_content_defines_a_hidden_primary_read_only_agent(tmp_path):
    cfg = opencode.config_content("Ты помощник.", (tmp_path,), max_turns=3)
    agent = cfg["agent"][opencode.AGENT]
    # Подагент `opencode run --agent` не берёт — нужен primary.
    assert agent["mode"] == "primary"
    assert agent["prompt"] == "Ты помощник."
    assert agent["steps"] == 3
    assert agent["permission"] == opencode.readonly_permission((tmp_path,))
    # Текст встречи никуда не публикуется, снимки файлов не делаются, без обновлений.
    assert cfg["share"] == "disabled"
    assert cfg["snapshot"] is False and cfg["autoupdate"] is False


def test_command_line(tmp_path):
    cmd = opencode.build_command("C:/oc/opencode.exe", str(tmp_path), "anthropic/claude-sonnet-4-5")
    assert cmd[:4] == ["C:/oc/opencode.exe", "run", "--format", "json"]
    assert cmd[cmd.index("--agent") + 1] == opencode.AGENT
    assert cmd[cmd.index("--dir") + 1] == str(tmp_path)
    assert cmd[cmd.index("-m") + 1] == "anthropic/claude-sonnet-4-5"
    assert "--title" in cmd
    # Ни авто-одобрения прав, ни публикации.
    for flag in ("--auto", "--share", "--dangerously-skip-permissions", "--continue", "--session"):
        assert flag not in cmd
    # Без модели — модель из конфига OpenCode; «модель» Claude не передаётся.
    assert "-m" not in opencode.build_command("opencode", str(tmp_path), None)
    assert "-m" not in opencode.build_command("opencode", str(tmp_path), "sonnet")


def test_child_env(monkeypatch):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"permission": {"*": "allow"}}')
    monkeypatch.setenv("OPENCODE_PERMISSION", '{"*": "allow"}')
    monkeypatch.setenv("OPENCODE_AUTO_SHARE", "1")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("OPENCODE_CONFIG", "D:/my/opencode.json")
    config = opencode.config_content("Ты помощник.", (), 1)
    env = opencode.child_env("none", config)
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"]) == config
    # Те же права — и общими: агент не нашёлся, build тоже только читает.
    assert json.loads(env["OPENCODE_PERMISSION"]) == {"*": "deny"}
    assert "Ты помощник." in env["OPENCODE_CONFIG_CONTENT"]  # кириллица как есть, без \uXXXX
    assert "OPENCODE_AUTO_SHARE" not in env
    assert "CLAUDECODE" not in env  # метки чужого сеанса (llm.base)
    for name in ("OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_DISABLE_CLAUDE_CODE",
                 "OPENCODE_DISABLE_AUTOUPDATE"):
        assert env[name] == "1"
    # Свой конфиг человека (вход, провайдеры) остаётся.
    assert env["OPENCODE_CONFIG"] == "D:/my/opencode.json"


def test_prompt_placement_measures_the_env_value():
    # Кириллица: 20 000 знаков укладываются в переменную (без \uXXXX), 31 000 — уже нет.
    fits = "п" * 20_000
    config, stdin = opencode.prompt_placement(fits, "Вопрос?", (), 1)
    assert config["agent"][opencode.AGENT]["prompt"] == fits and stdin == "Вопрос?"
    assert len(opencode.child_env(None, config)["OPENCODE_CONFIG_CONTENT"]) <= opencode.ENV_LIMIT
    long = "п" * (opencode.ENV_LIMIT + 1)
    config, stdin = opencode.prompt_placement(long, "Вопрос?", (), 1)
    assert config["agent"][opencode.AGENT]["prompt"] == opencode.SHORT_SYSTEM
    assert stdin == f"{long}\n\nВопрос?"


# --- уборка сеансов -------------------------------------------------------------------


def test_stale_sessions_only_from_service_folders(tmp_path):
    root = tmp_path / "meet-opencode"
    live_other = root / "4242-aaaa"
    live_other.mkdir(parents=True)
    dead = root / "1-bbbb"
    dead.mkdir()
    mine_running = root / f"{opencode.os.getpid()}-cccc"
    mine_running.mkdir()
    sessions = [
        {"id": "ses_user", "directory": str(tmp_path / "project")},       # сеанс человека
        {"id": "ses_user_inside", "directory": str(tmp_path)},            # тоже чужой
        {"id": "ses_root", "directory": str(root)},                       # прежняя общая папка
        {"id": "ses_live", "directory": str(live_other)},                 # идёт в другом процессе
        {"id": "ses_dead", "directory": str(dead)},                       # процесс умер
        {"id": "ses_gone", "directory": str(root / "4242-dddd")},         # папки уже нет
        {"id": "ses_running", "directory": str(mine_running)},            # идёт в этом процессе
        {"id": "ses_mine_gone", "directory": str(root / f"{opencode.os.getpid()}-eeee")},
    ]
    got = opencode.stale(sessions, root, alive=lambda pid: pid == 4242)
    assert got == ["ses_root", "ses_dead", "ses_gone", "ses_mine_gone"]


def test_ours_in_matches_the_folder_strictly(tmp_path):
    call = tmp_path / "123-abc"
    sessions = [{"id": "a", "directory": str(call)}, {"id": "b", "directory": str(call) + "x"},
                {"id": "c", "directory": str(call / "sub")}, {"id": "d", "directory": str(tmp_path)}]
    assert opencode.ours_in(sessions, call) == ["a"]


def test_list_sessions_parses_json_and_survives_garbage(monkeypatch, tmp_path):
    out = json.dumps([{"id": "ses_1", "title": "meet", "directory": "D:/x"}, {"id": 5}, "x"])
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"], seen["cwd"] = cmd, kw.get("cwd")
        return 0, out.encode(), b""

    monkeypatch.setattr(opencode, "run_tree", fake)
    assert opencode.list_sessions("oc", str(tmp_path), {}) == [
        {"id": "ses_1", "title": "meet", "directory": "D:/x"}]
    assert seen["cmd"] == ["oc", "session", "list", "--format", "json"]
    for result in ((0, b"", b""), (0, b"not json", b""), (1, b"[]", b"boom")):
        monkeypatch.setattr(opencode, "run_tree", lambda cmd, r=result, **kw: r)
        assert opencode.list_sessions("oc", str(tmp_path), {}) == []

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(opencode, "run_tree", timeout)
    assert opencode.list_sessions("oc", str(tmp_path), {}) == []


def test_delete_session_is_best_effort(monkeypatch, tmp_path):
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"], seen["cwd"] = cmd, kw.get("cwd")
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(opencode, "run_tree", fake)
    opencode.delete_session("C:/oc/opencode.exe", SID, str(tmp_path), {})
    assert seen["cmd"] == ["C:/oc/opencode.exe", "session", "delete", SID]
    assert seen["cwd"] == str(tmp_path)


# --- вызов --------------------------------------------------------------------------


class FakePopen:
    calls: list = []
    out = SIMPLE
    err = ""
    code = 0
    timeout = False

    def __init__(self, cmd, **kw):
        self.cmd, self.kw = cmd, kw
        self.pid = 4242
        self.returncode = None
        self.input = None
        FakePopen.calls.append(self)

    def communicate(self, input=None, timeout=None):
        if input is not None:
            self.input = input
        if FakePopen.timeout and self.returncode is None:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        if self.returncode is None:
            self.returncode = FakePopen.code
        return FakePopen.out.encode("utf-8"), FakePopen.err.encode("utf-8")

    def kill(self):
        self.returncode = -9


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Подделки: Popen вызова, служебные команды (список/удаление сеансов),
    временная папка системы — tmp_path."""
    FakePopen.calls = []
    FakePopen.out, FakePopen.err, FakePopen.code, FakePopen.timeout = SIMPLE, "", 0, False
    monkeypatch.setattr(opencode, "find_opencode", lambda: "C:/oc/opencode.exe")
    monkeypatch.setattr(opencode.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(opencode.tempdirs, "system_temp", lambda: tmp_path)
    monkeypatch.setattr(opencode, "_swept", False)
    state = {"deleted": [], "killed": [], "listed": 0, "sessions": []}

    def run_tree(cmd, **kw):
        if cmd[1:3] == ["session", "list"]:
            state["listed"] += 1
            return 0, json.dumps(state["sessions"]).encode(), b""
        if cmd[1:3] == ["session", "delete"]:
            state["deleted"].append(cmd[3])
            return 0, b"", b""
        raise AssertionError(cmd)

    monkeypatch.setattr(opencode, "run_tree", run_tree)

    def kill(proc):
        state["killed"].append(proc.pid)
        proc.kill()

    monkeypatch.setattr(opencode, "_kill_tree", kill)
    return state


def _run(**kw):
    kw.setdefault("system_prompt", "Ты помощник.")
    return asyncio.run(opencode.run("Вопрос?", **kw))


def _call_dir():
    call = FakePopen.calls[0]
    return call.cmd[call.cmd.index("--dir") + 1]


def test_run_success(fake, tmp_path):
    kb = tmp_path / "kb"
    reply = _run(allowed_dirs=(kb,), model="openai/gpt-5", max_turns=2)
    assert (reply.text, reply.error) == ("Да", None)
    call = FakePopen.calls[0]
    assert call.cmd[1:4] == ["run", "--format", "json"]
    assert call.cmd[call.cmd.index("-m") + 1] == "openai/gpt-5"
    # Вопрос — через stdin (длинная встреча в командную строку не влезла бы).
    assert call.input.decode("utf-8") == "Вопрос?"
    cfg = json.loads(call.kw["env"]["OPENCODE_CONFIG_CONTENT"])
    agent = cfg["agent"][opencode.AGENT]
    assert agent["prompt"] == "Ты помощник." and agent["steps"] == 2
    assert agent["permission"]["external_directory"][str(kb / "*")] == "allow"
    # Своя папка вызова в служебной, не папка встречи; после вызова её нет.
    workdir = Path(_call_dir())
    assert call.kw["cwd"] == str(workdir)
    assert workdir.parent == (tmp_path / opencode.WORKDIR).resolve()
    assert workdir.name.startswith(f"{opencode.os.getpid()}-")
    assert not workdir.exists()
    # Сеанс с текстом встречи не остаётся в истории OpenCode.
    assert fake["deleted"] == [SID]
    # Свой сеанс у OpenCode не продолжаем: session_id не возвращается.
    assert reply.session_id is None


def test_first_call_in_the_process_sweeps_leftovers(fake, tmp_path):
    root = (tmp_path / opencode.WORKDIR).resolve()
    fake["sessions"] = [{"id": "ses_dead", "directory": str(root / "1-old")},
                        {"id": "ses_user", "directory": str(tmp_path / "project")}]
    _run()
    _run()
    assert fake["listed"] == 1  # уборка — один раз за процесс
    assert fake["deleted"] == ["ses_dead", SID, SID]


def test_timeout_without_any_event_still_deletes_the_session(fake, tmp_path):
    # OpenCode записал сеанс с текстом встречи и завис до первого события.
    FakePopen.timeout = True
    FakePopen.out = ""
    root = (tmp_path / opencode.WORKDIR).resolve()

    def sessions_after_start():
        return [{"id": "ses_hung", "directory": _call_dir()},
                {"id": "ses_user", "directory": str(tmp_path / "project")},
                {"id": "ses_live", "directory": str(root / "4242-live")}]

    real = opencode.run_tree

    def run_tree(cmd, **kw):
        if FakePopen.calls:
            fake["sessions"] = sessions_after_start()
        return real(cmd, **kw)

    opencode.run_tree = run_tree
    try:
        reply = _run(timeout_s=0.01)
    finally:
        opencode.run_tree = real
    assert reply.error == TIMEOUT_ERROR
    assert fake["killed"] == [4242]
    assert fake["deleted"] == ["ses_hung"]


def test_timeout_with_partial_output_deletes_the_parsed_session(fake):
    FakePopen.timeout = True
    reply = _run(timeout_s=0.01)
    assert reply.error == TIMEOUT_ERROR
    # Вывод, отданный после kill, разобран: сеанс известен — удалён без списка.
    assert fake["deleted"] == [SID]


def test_cancel_kills_the_process_and_cleans_up(fake, tmp_path):
    started = __import__("threading").Event()
    release = __import__("threading").Event()

    class Slow(FakePopen):
        def communicate(self, input=None, timeout=None):
            started.set()
            release.wait(5)
            self.returncode = -9
            return b"", b""

    opencode.subprocess.Popen = Slow
    deleted_before = []

    async def scenario():
        task = asyncio.create_task(opencode.run("Вопрос?", system_prompt="s"))
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            deleted_before.append(list(fake["deleted"]))
        release.set()

    root = (tmp_path / opencode.WORKDIR).resolve()
    fake["sessions"] = []
    asyncio.run(scenario())
    assert fake["killed"] == [4242]
    # Поток вызова доходит до уборки и после отмены.
    for _ in range(100):
        if not any(root.iterdir()):
            break
        __import__("time").sleep(0.02)
    assert not any(root.iterdir())


def test_run_long_system_prompt_goes_to_stdin(fake):
    long = "п" * (opencode.ENV_LIMIT + 1)
    _run(system_prompt=long)
    call = FakePopen.calls[0]
    assert call.input.decode("utf-8") == f"{long}\n\nВопрос?"
    agent = json.loads(call.kw["env"]["OPENCODE_CONFIG_CONTENT"])["agent"][opencode.AGENT]
    assert agent["prompt"] == opencode.SHORT_SYSTEM


def test_run_not_found(monkeypatch):
    monkeypatch.setattr(opencode, "find_opencode", lambda: None)
    reply = _run()
    assert reply.text == "" and "OpenCode" in reply.error


def test_run_error_event(fake):
    FakePopen.out = step_start("msg_1") + "\n" + error("ProviderAuthError", "No API key") + "\n"
    FakePopen.code = 1
    reply = _run()
    assert reply.text == "" and "opencode auth login" in reply.error
    assert fake["deleted"] == [SID]


def test_run_failure_before_any_event_uses_stderr(fake):
    FakePopen.out, FakePopen.err, FakePopen.code = "", "\x1b[91mError: \x1b[0mModel not found: x/y", 1
    reply = _run()
    assert reply.error == "Error: Model not found: x/y"
    assert fake["deleted"] == []  # в списке сеансов этой папки ничего


def test_run_agent_fallback_is_an_error(fake):
    # Агент не нашёлся — OpenCode взял свой build: ответ не используем.
    FakePopen.err = "! agent \"meet-readonly\" not found. Falling back to default agent"
    reply = _run()
    assert reply.text == "" and reply.error == opencode.AGENT_LOST


def test_npm_shim_with_cmd_metacharacters_is_refused(fake, monkeypatch):
    monkeypatch.setattr(opencode, "find_opencode", lambda: r"C:\Users\A&B\npm\opencode.cmd")
    reply = _run()
    assert reply.error == opencode.SHIM_UNSAFE
    assert FakePopen.calls == []
    monkeypatch.setattr(opencode, "find_opencode", lambda: r"C:\Users\ab\npm\opencode.cmd")
    assert _run().error is None


def test_run_nonzero_exit_without_text(fake):
    FakePopen.out, FakePopen.code = "", 3
    assert _run().error == "OpenCode завершился с кодом 3"


def test_run_empty_answer(fake):
    FakePopen.out = step_start("msg_1") + "\n" + step_finish("msg_1") + "\n"
    assert _run().error == EMPTY_ERROR


def test_run_proxy_hint_on_connection_errors(fake):
    from meet import netproxy

    FakePopen.out = error("APIError", "Unable to connect. Is the computer able to access the url?") + "\n"
    FakePopen.code = 1
    reply = _run()
    assert reply.error.startswith("OpenCode: Unable to connect")
    assert netproxy.HINT in reply.error
