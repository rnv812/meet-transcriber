"""V4 (0.3.6) у постоянного диалога Claude Code: собеседник с инструментами
чтения, изображения в stdin, остановка хода (`control_request` interrupt) и
нативное продолжение сеанса (`--session-id` / `--resume`). Всё — на поддельном
CLI (`fake_claude_stream.py`), настоящую модель не зовём."""

import asyncio
import base64
import json
import re
import sys
import uuid
from pathlib import Path

import pytest

from meet.llm import claude_stream
from meet.llm.base import CANCELLED_ERROR, RESUME_ERROR

FAKE = Path(__file__).with_name("fake_claude_stream.py")
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d4944415478da63f8cfc0f01f0005000201a1d1e0e40000000049454e44ae426082")


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    monkeypatch.delenv("FAKE_CLAUDE_KNOWN", raising=False)

    def records():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    return [sys.executable, str(FAKE)], records


def _conv(cli, **kw):
    kw.setdefault("system_prompt", "системный промпт")
    return claude_stream.Conversation(cli=cli, **kw)


def _starts(records):
    return [r["argv"] for r in records() if "argv" in r]


def _messages(records):
    return [r["message"] for r in records() if "message" in r]


# --- командная строка ---------------------------------------------------------------


def test_responder_command_has_read_tools_dirs_and_six_turns(tmp_path):
    rec = tmp_path / "rec"
    materials = rec / "assistant" / "files"
    cmd = claude_stream.build_command(["claude"], system_prompt="s", responder=True,
                                      add_dirs=(rec, materials, None))
    assert "--restricted" in cmd
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    dirs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--add-dir"]
    assert dirs == [str(rec), str(materials)]          # только переданные: папка встречи и материалы
    assert cmd[cmd.index("--max-turns") + 1] == "6"
    # За --add-dir (флаг со многими значениями) сразу идёт другой флаг.
    last_dir = len(cmd) - 1 - cmd[::-1].index("--add-dir")
    assert cmd[last_dir + 2].startswith("--")
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in cmd


def test_listener_command_is_unchanged():
    cmd = claude_stream.build_command(["claude"], system_prompt="s")
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--max-turns") + 1] == "1"
    assert "--restricted" not in cmd and "--add-dir" not in cmd and "--permission-mode" not in cmd
    assert "--no-session-persistence" in cmd


def test_session_flags_new_and_resume():
    sid = str(uuid.uuid4())
    new = claude_stream.build_command(["claude"], system_prompt="s", session_id=sid)
    assert f"--session-id={sid}" in new and "--no-session-persistence" not in new
    again = claude_stream.build_command(["claude"], system_prompt="s", session_id="x", resume=sid)
    assert f"--resume={sid}" in again
    assert not any(a.startswith("--session-id") for a in again)
    assert "--no-session-persistence" not in again


# --- изображения --------------------------------------------------------------------


def test_image_goes_as_base64_block_before_text(fake_cli, tmp_path):
    cli, records = fake_cli
    img = tmp_path / "скрин.png"
    img.write_bytes(PNG)

    async def scenario():
        conv = _conv(cli)
        try:
            return await conv.send("что на картинке?", images=[img])
        finally:
            conv.close()

    reply = asyncio.run(scenario())
    assert reply.error is None
    (msg,) = _messages(records)
    assert msg["type"] == "user" and msg["message"]["role"] == "user"
    image, text = msg["message"]["content"]
    assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                  "data": base64.b64encode(PNG).decode("ascii")}}
    assert text == {"type": "text", "text": "что на картинке?"}


def test_ready_content_blocks_pass_through(fake_cli):
    cli, records = fake_cli
    blocks = [{"type": "text", "text": "раз"}, {"type": "text", "text": "два"}]

    async def scenario():
        conv = _conv(cli)
        try:
            return await conv.send(content=blocks)
        finally:
            conv.close()

    assert asyncio.run(scenario()).error is None
    assert _messages(records)[0]["message"]["content"] == blocks


def test_bad_image_is_an_error_without_a_process(fake_cli, tmp_path):
    cli, records = fake_cli
    bmp = tmp_path / "a.bmp"
    bmp.write_bytes(b"BM")

    async def scenario():
        conv = _conv(cli)
        try:
            return await conv.send("x", images=[bmp]), await conv.send("x", images=[tmp_path / "нет.png"])
        finally:
            conv.close()

    wrong, missing = asyncio.run(scenario())
    assert wrong.error and "изображение" in wrong.error
    assert missing.error and "изображение" in missing.error
    assert records() == []


# --- остановка хода -----------------------------------------------------------------


async def _interrupt_after_first_text(conv, **kw):
    started = asyncio.Event()

    def on_text(piece):
        if piece:
            started.set()

    turn = asyncio.create_task(conv.send("долгий вопрос", on_text=on_text, timeout_s=20))
    await asyncio.wait_for(started.wait(), 10)
    pid = conv.pid
    ok = await conv.interrupt(**kw)
    reply = await asyncio.wait_for(turn, 10)
    return ok, reply, pid


def test_interrupt_sends_control_request_and_waits_for_control_response(fake_cli, monkeypatch):
    cli, records = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow")

    async def scenario():
        conv = _conv(cli)
        try:
            ok, reply, pid = await _interrupt_after_first_text(conv)
            return ok, reply, pid, conv.pid, conv.alive, conv.spawns
        finally:
            conv.close()

    ok, reply, pid1, pid2, alive, spawns = asyncio.run(scenario())
    assert ok is True
    assert reply.cancelled and reply.error == CANCELLED_ERROR
    assert reply.text == "начало ответа"                 # что успело прийти — остаётся
    assert alive and pid1 == pid2 and spawns == 1        # процесс тот же
    control = [m for m in _messages(records) if m.get("type") == "control_request"]
    assert len(control) == 1
    assert control[0]["request"] == {"subtype": "interrupt"}
    assert re.fullmatch(r"req_\d+_[0-9a-f]{8}", control[0]["request_id"])


def test_interrupt_without_confirmation_kills_and_restarts(fake_cli, monkeypatch):
    cli, _ = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "deaf")
    lines = []

    async def scenario():
        conv = _conv(cli, log=lines.append)
        try:
            ok, reply, pid = await _interrupt_after_first_text(conv, wait_s=0.5)
            return ok, reply, pid, conv.pid, conv.alive, conv.spawns, conv.has_context
        finally:
            conv.close()

    ok, reply, pid1, pid2, alive, spawns, has_context = asyncio.run(scenario())
    assert ok is False
    assert reply.cancelled and reply.error == CANCELLED_ERROR
    assert alive and pid1 != pid2 and spawns == 2       # убит и поднят заново
    assert has_context is False                          # без persist — нужна затравка
    assert any("не подтвердил" in line for line in lines)


def test_interrupt_when_idle_writes_nothing(fake_cli):
    cli, records = fake_cli

    async def scenario():
        conv = _conv(cli)
        try:
            before = await conv.interrupt()
            await conv.send("x")
            after = await conv.interrupt()
            return before, after
        finally:
            conv.close()

    assert asyncio.run(scenario()) == (True, True)
    assert not [m for m in _messages(records) if m.get("type") == "control_request"]


def test_permission_request_from_cli_is_denied_and_tool_preamble_reset(fake_cli, monkeypatch):
    cli, records = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "tool")
    pieces = []

    async def scenario():
        conv = _conv(cli, responder=True)
        try:
            return await conv.send("вопрос", on_text=pieces.append, timeout_s=10)
        finally:
            conv.close()

    reply = asyncio.run(scenario())
    assert reply.error is None and reply.text == "ответ"
    assert pieces == ["посмотрю заметки", None, "ответ"]
    answers = [m for m in _messages(records) if m.get("type") == "control_response"]
    assert answers[0]["response"]["request_id"] == "cli_1"
    assert answers[0]["response"]["response"]["behavior"] == "deny"


# --- нативное продолжение -----------------------------------------------------------


def test_persistent_session_starts_with_our_id_and_resumes_after_restart(fake_cli, monkeypatch):
    cli, records = fake_cli

    async def scenario():
        conv = _conv(cli, persist=True)
        try:
            first = await conv.send("раз")
            sid = conv.session_id
            monkeypatch.setenv("FAKE_CLAUDE_KNOWN", sid)  # CLI «сохранил» сеанс
            conv.kill()
            context_after_kill = conv.has_context
            second = await conv.send("два")
            return first, second, sid, conv.session_id, context_after_kill
        finally:
            conv.close()

    first, second, sid1, sid2, context_after_kill = asyncio.run(scenario())
    assert first.error is None and second.error is None
    assert uuid.UUID(sid1) and sid1 == sid2
    assert context_after_kill is True                    # затравка не нужна
    start1, start2 = _starts(records)
    assert f"--session-id={sid1}" in start1 and "--no-session-persistence" not in start1
    assert f"--resume={sid1}" in start2


def test_resume_of_a_stored_session(fake_cli, monkeypatch):
    cli, records = fake_cli
    sid = str(uuid.uuid4())
    monkeypatch.setenv("FAKE_CLAUDE_KNOWN", sid)

    async def scenario():
        conv = _conv(cli, resume=sid, responder=True)
        try:
            ctx = conv.has_context
            return ctx, await conv.send("продолжим"), conv.session_id
        finally:
            conv.close()

    ctx, reply, current = asyncio.run(scenario())
    assert ctx is True and reply.error is None and not reply.resume_failed and current == sid
    (argv,) = _starts(records)
    assert f"--resume={sid}" in argv and "--restricted" in argv


def test_unknown_session_is_a_distinct_resume_failure_then_fresh_session(fake_cli):
    cli, records = fake_cli
    sid = str(uuid.uuid4())

    async def scenario():
        conv = _conv(cli, resume=sid)
        try:
            failed = await conv.send("продолжим")
            state = (conv.session_id, conv.has_context)
            fresh = await conv.send("затравка + сообщение")
            return failed, state, fresh, conv.session_id
        finally:
            conv.close()

    failed, (after_id, after_ctx), fresh, new_id = asyncio.run(scenario())
    assert failed.resume_failed and failed.error.startswith(RESUME_ERROR)
    assert "No conversation found" in failed.error
    assert after_id is None and after_ctx is False       # id забыт: нужна затравка
    assert fresh.error is None and not fresh.resume_failed
    start1, start2 = _starts(records)
    assert f"--resume={sid}" in start1
    assert f"--session-id={new_id}" in start2 and new_id != sid


def test_not_a_uuid_is_a_resume_failure_without_a_process(fake_cli):
    cli, records = fake_cli

    async def scenario():
        conv = _conv(cli, resume="--dangerously-skip-permissions")
        try:
            return await conv.send("x")
        finally:
            conv.close()

    reply = asyncio.run(scenario())
    assert reply.resume_failed and records() == []


def test_crash_later_in_a_resumed_process_is_an_ordinary_error(fake_cli, monkeypatch):
    """Сеанс найден (был init) — следующие сбои этого процесса не «сеанс не
    продолжить»: id остаётся, следующий процесс снова продолжит его."""
    cli, records = fake_cli
    sid = str(uuid.uuid4())
    monkeypatch.setenv("FAKE_CLAUDE_KNOWN", sid)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "crash")

    async def scenario():
        conv = _conv(cli, resume=sid)
        try:
            ok = await conv.send("раз")
            broken = await conv.send("два")
            return ok, broken, conv.session_id, conv.has_context
        finally:
            conv.close()

    ok, broken, current, ctx = asyncio.run(scenario())
    assert ok.error is None
    assert broken.error and "something broke" in broken.error and not broken.resume_failed
    assert current == sid and ctx is True


def test_responder_adds_no_folder_by_default(fake_cli, monkeypatch):
    """База знаний — только по просьбе человека (результаты kb_search движка в
    тексте сообщения), не папкой: без явных `add_dirs` у собеседника нет ни
    одного --add-dir, и сам Conversation ничего не добавляет."""
    cli, records = fake_cli
    assert "--add-dir" not in claude_stream.build_command(["claude"], system_prompt="s", responder=True)

    async def scenario():
        conv = _conv(cli, responder=True)
        try:
            await conv.send("x")
        finally:
            conv.close()

    asyncio.run(scenario())
    (argv,) = _starts(records)
    assert "--restricted" in argv and "--add-dir" not in argv
