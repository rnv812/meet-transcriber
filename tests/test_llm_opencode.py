"""Провайдер OpenCode: `opencode run --format json` (meet.llm.opencode).

Настоящий OpenCode не запускается: Popen подменён. События в фикстурах —
по формату `opencode run --format json` (packages/opencode/src/cli/cmd/run.ts:
одна строка JSON на событие, {type, timestamp, sessionID, part|error}).
"""

import asyncio
import json
import subprocess

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
    assert got.text == "Ответ: в базе есть."
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
    monkeypatch.setenv("OPENCODE_AUTO_SHARE", "1")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("OPENCODE_CONFIG", "D:/my/opencode.json")
    env = opencode.child_env("none", {"agent": {}})
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"]) == {"agent": {}}
    assert "OPENCODE_AUTO_SHARE" not in env
    assert "CLAUDECODE" not in env  # метки чужого сеанса (llm.base)
    for name in ("OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_DISABLE_CLAUDE_CODE",
                 "OPENCODE_DISABLE_AUTOUPDATE"):
        assert env[name] == "1"
    # Свой конфиг человека (вход, провайдеры) остаётся.
    assert env["OPENCODE_CONFIG"] == "D:/my/opencode.json"


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
def fake(monkeypatch):
    FakePopen.calls = []
    FakePopen.out, FakePopen.err, FakePopen.code, FakePopen.timeout = SIMPLE, "", 0, False
    monkeypatch.setattr(opencode, "find_opencode", lambda: "C:/oc/opencode.exe")
    monkeypatch.setattr(opencode.subprocess, "Popen", FakePopen)
    deleted = []
    monkeypatch.setattr(opencode, "_delete_session",
                        lambda exe, sid, workdir, env: deleted.append(sid))
    killed = []

    def kill(proc):
        killed.append(proc.pid)
        proc.kill()

    monkeypatch.setattr(opencode, "_kill_tree", kill)
    return {"deleted": deleted, "killed": killed}


def _run(**kw):
    kw.setdefault("system_prompt", "Ты помощник.")
    return asyncio.run(opencode.run("Вопрос?", **kw))


def test_run_success(fake, tmp_path):
    reply = _run(allowed_dirs=(tmp_path,), model="openai/gpt-5", max_turns=2)
    assert (reply.text, reply.error) == ("Да", None)
    call = FakePopen.calls[0]
    assert call.cmd[1:4] == ["run", "--format", "json"]
    assert call.cmd[call.cmd.index("-m") + 1] == "openai/gpt-5"
    # Вопрос — через stdin (длинная встреча в командную строку не влезла бы).
    assert call.input.decode("utf-8") == "Вопрос?"
    cfg = json.loads(call.kw["env"]["OPENCODE_CONFIG_CONTENT"])
    agent = cfg["agent"][opencode.AGENT]
    assert agent["prompt"] == "Ты помощник." and agent["steps"] == 2
    assert agent["permission"]["external_directory"][str(tmp_path / "*")] == "allow"
    # Служебная рабочая папка, не папка встречи: сеансы не попадают в её --continue.
    workdir = call.cmd[call.cmd.index("--dir") + 1]
    assert call.kw["cwd"] == workdir and str(tmp_path) != workdir
    # Сеанс с текстом встречи не остаётся в истории OpenCode.
    assert fake["deleted"] == [SID]
    # Свой сеанс у OpenCode не продолжаем: session_id не возвращается.
    assert reply.session_id is None


def test_run_long_system_prompt_goes_to_stdin(fake):
    long = "п" * (opencode.PROMPT_ENV_LIMIT + 1)
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
    assert fake["deleted"] == []


def test_run_nonzero_exit_without_text(fake):
    FakePopen.out, FakePopen.code = "", 3
    assert _run().error == "OpenCode завершился с кодом 3"


def test_run_empty_answer(fake):
    FakePopen.out = step_start("msg_1") + "\n" + step_finish("msg_1") + "\n"
    assert _run().error == EMPTY_ERROR


def test_run_timeout_kills_the_tree(fake):
    FakePopen.timeout = True
    reply = _run(timeout_s=0.01)
    assert reply.error == TIMEOUT_ERROR
    assert fake["killed"] == [4242]


def test_run_proxy_hint_on_connection_errors(fake):
    FakePopen.out = error("APIError", "Unable to connect. Is the computer able to access the url?") + "\n"
    FakePopen.code = 1
    reply = _run()
    assert reply.error.startswith("OpenCode: Unable to connect")


def test_delete_session_is_best_effort(monkeypatch, tmp_path):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["cwd"] = cmd, kw.get("cwd")
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(opencode.subprocess, "run", run)
    opencode._delete_session("C:/oc/opencode.exe", SID, str(tmp_path), {})
    assert seen["cmd"] == ["C:/oc/opencode.exe", "session", "delete", SID]
    assert seen["cwd"] == str(tmp_path)
