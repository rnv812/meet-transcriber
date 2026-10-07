"""Модель Claude Code из настроек доходит до каждого вызова (`--model`), и
окно видит, какая модель запустилась на самом деле (v037 model-pick).

Без `--model` claude берёт модель из ~/.claude/settings.json или свою по
умолчанию (самую новую — Fable), а с `--resume` — модель прежнего сеанса.
Поэтому `--model` передаётся всегда: в постоянном диалоге (живой агент,
подсказки, «Продолжить разговор»), в вызовах через SDK (итоги, анализ,
названия, вопросы), при продолжении сеанса. Пустая модель — модель Meet по
умолчанию (sonnet), не модель CLI. Вкладка «Агент» — тесты оболочки
(`pty.rs`) и окна (`agentLaunch.test.ts`).

Модель не зовётся: поддельный CLI (`fake_claude_stream.py`), подмена
`claude_agent_sdk.query`, поддельные диалоги."""

import asyncio
import json
import sys
from pathlib import Path

import claude_agent_sdk
import pytest

from meet import llm
from meet.llm import base, claude, claude_stream
from meet.llm.base import AgentReply
from meet.settings import Settings

FAKE = Path(__file__).with_name("fake_claude_stream.py")
SID = "0b6f8a52-3c1d-4e2f-9a7b-1c2d3e4f5a6b"


def _model_of(argv) -> str:
    """Значение единственного `--model` (`--model x` у SDK, `--model=x` у диалога)."""
    found = [(i, a) for i, a in enumerate(argv) if a == "--model" or a.startswith("--model=")]
    assert len(found) == 1, argv
    i, arg = found[0]
    return argv[i + 1] if arg == "--model" else arg.split("=", 1)[1]


# --- псевдонимы и сравнение ---

@pytest.mark.parametrize("configured, actual, same", [
    ("opus", "claude-opus-5-5", True),
    ("opus", "claude-opus-5-5[1m]", True),
    ("opus", "claude-fable-5-1", False),
    ("sonnet", "claude-fable-5-1", False),
    ("fable", "claude-fable-5-1", True),
    ("claude-opus-5-5", "claude-opus-5-5", True),
    ("claude-opus-5-5", "us.anthropic.claude-opus-5-5-v1:0", True),
    ("claude-opus-5-5", "claude-fable-5-1", False),
    ("Opus", "claude-opus-5-5", True),
    ("opusplan", "claude-sonnet-4-5", True),
    ("default", "claude-fable-5-1", True),
    ("best", "claude-fable-5-1", True),       # лучшая доступная — сравнивать не с чем
    ("best", "claude-opus-5-5", True),
    ("mythos", "claude-mythos-5-1", True),
    ("mythos", "claude-fable-5-1", False),
    ("opus[1m]", "claude-opus-5-5[1m]", True),
    ("fable[1m]", "claude-opus-5-5", False),
    ("opus", None, True),
])
def test_model_matches(configured, actual, same):
    assert base.model_matches(configured, actual) is same


def test_empty_model_is_meets_default_not_the_cli_default():
    assert base.claude_model("opus") == "opus"
    assert base.claude_model("  opus ") == "opus"
    assert base.claude_model("") == base.claude_model(None) == base.claude_model("  ") == "sonnet"
    assert Settings.from_raw({"llm": {"model": "  "}}).llm.model == "sonnet"
    assert Settings().llm.model == base.DEFAULT_CLAUDE_MODEL


# --- llm: runner, уровни, подпись ---

def test_runner_and_tiers_always_carry_the_configured_model(monkeypatch):
    seen = []

    async def fake_run(prompt, **kwargs):
        seen.append(kwargs)
        return AgentReply(text="ок")

    monkeypatch.setattr(claude, "run", fake_run)
    cfg = Settings.from_raw({"llm": {"model": "opus"}})
    runner = llm.runner_for("claude-code", cfg)
    asyncio.run(runner("x", system_prompt="s"))
    assert seen[-1]["model"] == "opus"
    # «Быстрее» — своя модель явно, «как у агента» — из настроек.
    assert llm.tier_kwargs("claude-code", "agent", "opus") == {"model": "opus"}
    assert llm.tier_kwargs("claude-code", "agent", "") == {"model": "sonnet"}
    assert llm.tier_kwargs("claude-code", "fast", "opus")["model"] == "haiku"
    assert llm.agent_model("claude-code", cfg) == "opus"
    assert llm.agent_model("codex", cfg) is None
    assert llm.describe("claude-code", cfg) == {"provider": "claude-code", "model": "opus"}
    # Модель, испорченная руками в объекте настроек, — модель Meet по умолчанию.
    blank = Settings.from_raw({})
    object.__setattr__(blank.llm, "model", "")
    asyncio.run(llm.runner_for("claude-code", blank)("x", system_prompt="s"))
    assert seen[-1]["model"] == "sonnet"
    assert llm.agent_model("claude-code", blank) == "sonnet"


# --- SDK: итоги, анализ, названия, вопросы, «Проверить» ---

def _sdk_command(monkeypatch, init_model=None, **kw):
    from claude_agent_sdk import ResultMessage, SystemMessage
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    seen = {}

    def fake_query(*, prompt, options):
        seen["cmd"] = SubprocessCLITransport(prompt=prompt, options=options)._build_command()

        async def gen():
            if init_model is not None:
                yield SystemMessage(subtype="init", data={"type": "system", "subtype": "init",
                                                          "model": init_model})
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id=kw.get("resume") or "x", result="ответ")
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")
    reply = asyncio.run(claude.run("привет", system_prompt="s", **kw))
    return seen["cmd"], reply


@pytest.mark.parametrize("kw", [{}, {"keep_session": True}, {"resume": SID}, {"session_id": SID}])
def test_sdk_calls_pass_the_model_in_every_session_mode(monkeypatch, kw):
    cmd, _ = _sdk_command(monkeypatch, model="opus", **kw)
    assert _model_of(cmd) == "opus"
    # Настройки пользователя (~/.claude/settings.json с его `model`) не грузятся.
    assert "--setting-sources=" in cmd


@pytest.mark.parametrize("model", [None, "", "  "])
def test_sdk_call_without_a_model_gets_meets_default(monkeypatch, model):
    cmd, _ = _sdk_command(monkeypatch, model=model)
    assert _model_of(cmd) == "sonnet"


def test_sdk_reply_names_the_model_that_ran(monkeypatch, caplog):
    _, reply = _sdk_command(monkeypatch, model="opus", init_model="claude-opus-5-5")
    assert reply.model == "claude-opus-5-5"
    with caplog.at_level("WARNING", logger="meet.llm.claude"):
        _, reply = _sdk_command(monkeypatch, model="opus", init_model="claude-fable-5-1")
    assert reply.model == "claude-fable-5-1"
    assert "claude-fable-5-1" in caplog.text and "opus" in caplog.text


def test_check_auth_uses_the_given_model(monkeypatch):
    seen = {}

    async def fake_run(prompt, **kwargs):
        seen.update(kwargs)
        return AgentReply(text="ок")

    monkeypatch.setattr(claude, "run", fake_run)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")
    assert asyncio.run(claude.check_auth(model="opus")) is None
    assert seen["model"] == "opus"


# --- постоянный диалог (живой агент, подсказки, «Продолжить разговор») ---

@pytest.mark.parametrize("kw", [
    {},
    {"session_id": SID},
    {"resume": SID},
    {"resume": SID, "fork_at": "u1", "drop_turn": "u2"},
    {"responder": True, "add_dirs": ["D:/rec"], "deny_paths": ["D:/kb/secret"], "resume": SID},
])
def test_stream_command_always_has_the_model(kw):
    cmd = claude_stream.build_command(["claude"], system_prompt="s", model="opus", **kw)
    assert _model_of(cmd) == "opus"
    assert cmd[cmd.index("--setting-sources") + 1] == ""


@pytest.mark.parametrize("model", [None, ""])
def test_stream_command_without_a_model_gets_meets_default(model):
    assert _model_of(claude_stream.build_command(["claude"], system_prompt="s", model=model)) == "sonnet"


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    monkeypatch.delenv("FAKE_CLAUDE_INIT_MODEL", raising=False)

    def argvs():
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [r["argv"] for r in rows if "argv" in r]

    return [sys.executable, str(FAKE)], argvs


def _talk(cli, **kw):
    models, logs = [], []

    async def scenario():
        conv = claude_stream.Conversation(cli=cli, system_prompt="s", on_model=models.append,
                                          log=logs.append, **kw)
        try:
            reply = await conv.send("привет")
            return reply, conv.model
        finally:
            conv.close()

    reply, model = asyncio.run(scenario())
    return reply, model, models, logs


def test_conversation_reports_the_model_from_init(fake_cli):
    cli, argvs = fake_cli
    reply, model, models, logs = _talk(cli, model="opus")
    assert reply.error is None
    assert _model_of(argvs()[-1]) == "opus"
    assert reply.model == model == "claude-opus-5-5" and models == ["claude-opus-5-5"]
    assert not any("задана" in line for line in logs)


def test_conversation_resume_keeps_the_configured_model(fake_cli, monkeypatch):
    cli, argvs = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_KNOWN", SID)
    reply, model, _, _ = _talk(cli, model="opus", persist=True, resume=SID, responder=True)
    argv = argvs()[-1]
    assert f"--resume={SID}" in argv and _model_of(argv) == "opus"
    assert reply.error is None and model == "claude-opus-5-5"


def test_conversation_warns_when_the_cli_runs_another_model(fake_cli, monkeypatch):
    cli, _ = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_INIT_MODEL", "claude-fable-5-1")
    reply, model, models, logs = _talk(cli, model="opus")
    assert reply.model == model == "claude-fable-5-1" and models == ["claude-fable-5-1"]
    assert any("claude-fable-5-1" in line and "opus" in line for line in logs)


def test_conversation_without_a_model_does_not_fall_to_the_cli_default(fake_cli):
    cli, argvs = fake_cli
    reply, model, _, _ = _talk(cli)
    assert _model_of(argvs()[-1]) == "sonnet"
    assert model == "claude-sonnet-4-5"   # а не Fable — модель CLI по умолчанию


# --- живые подсказки (`hints_session_for`) ---

def test_live_hints_conversation_gets_the_tier_model(monkeypatch):
    from meet.assist import app

    made = []
    monkeypatch.setattr(claude_stream, "Conversation", lambda **kw: made.append(kw) or kw)
    cfg = Settings.from_raw({"llm": {"model": "opus"}})
    for tier, want in (("agent", "opus"), ("fast", "haiku")):
        kwargs = llm.tier_kwargs("claude-code", tier, cfg.llm.model)
        app.hints_session_for("claude-code", cfg, kwargs)("системный промпт")
        assert made[-1]["model"] == want
    assert app.hints_session_for("codex", cfg, {}) is None


# --- агент-участник и «Продолжить разговор» после встречи ---

class _Conv:
    """Диалог Claude Code без процесса: init сообщает `ran` (модель CLI)."""

    def __init__(self, made, ran=None, **kwargs):
        self.kwargs = kwargs
        self.ran = ran
        self.session_id = None
        self.has_context = False
        made.append(self)

    async def send(self, text, *, images=(), on_text=None, timeout_s=90.0):
        ran = self.ran or {"opus": "claude-opus-5-5"}.get(self.kwargs.get("model"), "claude-sonnet-4-5")
        if self.kwargs.get("on_model"):
            self.kwargs["on_model"](ran)
        self.session_id = SID
        if on_text is not None:
            on_text('{"say": "ок"}')
        return AgentReply(text='{"say": "ок"}', model=ran)

    async def interrupt(self, **_kw):
        return True

    def forget_session(self):
        self.session_id = None

    def close(self):
        pass


def _participant(tmp_path, cfg, made, ran=None):
    from meet.assist import participant as participant_mod
    from meet.assist.bus import TranscriptBus

    folder = tmp_path / "lib" / "2026-10-07_10-00"
    folder.mkdir(parents=True)
    return participant_mod.from_settings(
        cfg, TranscriptBus(), folder, "claude-code", None, log=lambda _m: None,
        conversation=lambda **kw: _Conv(made, ran, **kw))


@pytest.mark.parametrize("ran, mismatch", [(None, False), ("claude-fable-5-1", True)])
def test_participant_uses_the_configured_model_and_shows_the_real_one(tmp_path, ran, mismatch):
    made = []
    cfg = Settings.from_raw({"llm": {"model": "opus"}})
    p = _participant(tmp_path, cfg, made, ran)
    view = p.view()
    assert view["label"] == "Claude Code (opus)" and view["model"] is None
    assert view["model_configured"] == "opus" and view["model_mismatch"] is False

    async def scenario():
        session = await p._ensure_session()
        await session.send("привет")

    asyncio.run(scenario())
    assert made[0].kwargs["model"] == "opus"
    assert made[0].kwargs["on_model"] == p._on_model
    view = p.view()
    real = ran or "claude-opus-5-5"
    assert view["model"] == real and view["label"] == f"Claude Code ({real})"
    assert view["model_mismatch"] is mismatch


def test_after_meeting_chat_job_passes_the_configured_model(tmp_path, monkeypatch):
    from meet import job_worker, library, settings
    from meet.assist.chatlog import ChatLog

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    folder = tmp_path / "recordings" / "2026-10-07_10-00"
    folder.mkdir(parents=True)
    library.write_transcript(folder, {"version": 1, "title": "Планёрка", "segments": [
        {"start": 0.0, "end": 2.0, "speaker": "Демьян", "text": "Релиз в пятницу."}]})
    cfg = Settings.from_raw({"llm": {"model": "opus"}})
    monkeypatch.setattr(settings, "load", lambda *a, **k: cfg)
    ChatLog(folder).append("user", text="Когда релиз?", after_meeting=True)
    made = []
    code = job_worker._chat(str(folder), "m1", provider="claude-code",
                            conversation=lambda **kw: _Conv(made, **kw))
    assert code == 0
    assert made and all(c.kwargs["model"] == "opus" for c in made)

