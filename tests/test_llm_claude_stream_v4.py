"""V4 (0.3.6) у постоянного диалога Claude Code: собеседник с инструментами
чтения, изображения в stdin, остановка хода (`control_request` interrupt) и
нативное продолжение сеанса (`--session-id` / `--resume`). Всё — на поддельном
CLI (`fake_claude_stream.py`), настоящую модель не зовём."""

import asyncio
import base64
import io
import json
import re
import sys
import uuid
from pathlib import Path

import pytest
from PIL import Image

from meet.llm import claude_stream
from meet.llm.base import CANCELLED_ERROR, RESUME_ERROR

FAKE = Path(__file__).with_name("fake_claude_stream.py")


def _png_bytes(size=(4, 4), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (255, 0, 0)).save(buf, fmt)
    return buf.getvalue()


PNG = _png_bytes()


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


def test_responder_command_has_read_tools_dirs_and_twelve_turns(tmp_path):
    """v4-simple §6: агент сам читает и ищет — папки встречи, базы знаний и
    библиотеки передаёт вызывающий, все — в --add-dir."""
    rec, kb, library = tmp_path / "rec", tmp_path / "kb", tmp_path / "library"
    cmd = claude_stream.build_command(["claude"], system_prompt="s", responder=True,
                                      add_dirs=(rec, kb, library, None))
    assert "--restricted" in cmd
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    dirs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--add-dir"]
    assert dirs == [str(rec), str(kb), str(library)]
    assert cmd[cmd.index("--max-turns") + 1] == "12"
    assert "--disallowedTools" not in cmd                # запретов не передали — их нет
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


def test_bad_images_are_dropped_with_a_note_and_the_message_still_goes(fake_cli, tmp_path):
    """Негодное изображение не роняет сообщение: не уходит, модели — пометка
    в тексте, окну — `dropped_images` и `notes`."""
    cli, records = fake_cli
    bmp = tmp_path / "a.bmp"
    bmp.write_bytes(b"BM")
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)   # заголовок PNG, внутри мусор
    good = tmp_path / "ok.png"
    good.write_bytes(PNG)

    async def scenario():
        conv = _conv(cli)
        try:
            return await conv.send("вопрос", images=[bmp, broken, tmp_path / "нет.png", good])
        finally:
            conv.close()

    reply = asyncio.run(scenario())
    assert reply.error is None
    assert reply.dropped_images == [str(bmp), str(broken), str(tmp_path / "нет.png")]
    assert len(reply.notes) == 3 and all("не отправлено" in n for n in reply.notes)
    (msg,) = _messages(records)
    image, text = msg["message"]["content"]
    assert image["type"] == "image"
    assert text["text"].startswith("вопрос\n(Изображение «a.bmp» не отправлено")


def test_media_type_comes_from_bytes_not_extension(fake_cli, tmp_path):
    cli, records = fake_cli
    liar = tmp_path / "photo.png"
    liar.write_bytes(_png_bytes(fmt="JPEG"))

    async def scenario():
        conv = _conv(cli)
        try:
            return await conv.send("x", images=[liar])
        finally:
            conv.close()

    assert asyncio.run(scenario()).error is None
    image = _messages(records)[0]["message"]["content"][0]
    assert image["source"]["media_type"] == "image/jpeg"


def test_size_limit_is_on_base64_length(tmp_path, monkeypatch):
    """5 МБ у API — длина base64 (сырые байты × 4/3), не размер файла."""
    from meet.llm import base

    monkeypatch.setattr(base, "IMAGE_MAX_BASE64", 1000)
    raw = _png_bytes(size=(1, 1))
    fits = tmp_path / "fits.png"
    fits.write_bytes(raw + b"\x00" * (750 - len(raw)))       # base64 ровно 1000
    over = tmp_path / "over.png"
    over.write_bytes(raw + b"\x00" * (751 - len(raw)))       # 751 байт → base64 1004
    assert base.base64_size(750) == 1000 and base.base64_size(751) == 1004
    assert base.check_image(fits)[0] == "image/png"
    with pytest.raises(ValueError, match="5 МБ"):
        base.check_image(over)


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


# --- Fix round 1: «Стоп» без ложной отмены, окна остановки ------------------------


def test_turn_that_finished_as_stop_arrived_is_a_normal_reply(fake_cli, monkeypatch):
    """I1: CLI успел закончить ход до остановки (удачный result, потом
    control_response «простой») — это обычный ответ с полным текстом: он уже
    в сеансе модели, и его надо показать."""
    cli, _ = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "finish")

    async def scenario():
        conv = _conv(cli)
        try:
            return await _interrupt_after_first_text(conv)
        finally:
            conv.close()

    ok, reply, _ = asyncio.run(scenario())
    assert ok is True
    assert reply.cancelled is False and reply.error is None
    assert reply.text == "начало ответа и конец"


def test_stop_before_the_message_is_sent_skips_the_turn(fake_cli):
    """Окно «поднимается процесс»: остановка запоминается, ход не начинается."""
    cli, records = fake_cli

    async def scenario():
        conv = _conv(cli)
        gate = asyncio.Event()
        real = conv._ensure_process

        async def slow():
            await gate.wait()
            return await real()

        conv._ensure_process = slow
        try:
            turn = asyncio.create_task(conv.send("вопрос"))
            await asyncio.sleep(0.05)
            ok = await conv.interrupt()
            gate.set()
            return ok, await asyncio.wait_for(turn, 10)
        finally:
            conv.close()

    ok, reply = asyncio.run(scenario())
    assert ok is True and reply.cancelled and reply.text == ""
    assert not [m for m in _messages(records) if m.get("type") == "user"]


def test_stop_while_the_message_is_being_written_is_sent_after_it(fake_cli, monkeypatch):
    """Окно «сообщение пишется»: остановка не обгоняет сообщение (CLI счёл бы
    её остановкой простоя) — её посылает сам ход сразу после записи."""
    cli, records = fake_cli
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow")
    import time as _time

    async def scenario():
        conv = _conv(cli)
        real = conv._write_line

        def slow_write(proc, obj):
            if obj.get("type") == "user":
                _time.sleep(0.4)
            real(proc, obj)

        conv._write_line = slow_write
        try:
            turn = asyncio.create_task(conv.send("вопрос", timeout_s=20))
            for _ in range(200):
                if conv._phase == "writing":
                    break
                await asyncio.sleep(0.01)
            ok = await conv.interrupt()
            return ok, await asyncio.wait_for(turn, 15)
        finally:
            conv.close()

    ok, reply = asyncio.run(scenario())
    assert ok is True and reply.cancelled
    kinds = [m.get("type") for m in _messages(records)]
    assert kinds == ["user", "control_request"]          # остановка — после сообщения


def test_stray_events_between_turns_are_drained(fake_cli):
    cli, _ = fake_cli

    async def scenario():
        conv = _conv(cli)
        try:
            await conv.send("раз")
            conv._queue.put_nowait({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "ЛИШНЕЕ"}]}})
            return await conv.send("два")
        finally:
            conv.close()

    assert "ЛИШНЕЕ" not in asyncio.run(scenario()).text


def test_dead_process_is_not_killed_twice(monkeypatch):
    killed = []
    monkeypatch.setattr(claude_stream.procjob, "kill_tree", lambda proc: killed.append(proc))

    class Dead:
        def poll(self):
            return 0

    conv = claude_stream.Conversation(system_prompt="s", cli=["x"])
    proc = Dead()
    conv._proc = proc
    conv._kill_if(proc)
    conv._kill_if(proc)
    assert killed == [] and conv._proc is None


# --- Fix round 1: kb_exclude → правила запрета ------------------------------------


def test_deny_paths_become_read_rules_for_the_responder(tmp_path):
    kb = tmp_path / "База знаний"
    private = kb / "Личное, черновики"
    cmd = claude_stream.build_command(["claude"], system_prompt="s", responder=True,
                                      add_dirs=(kb,), deny_paths=(private,))
    i = cmd.index("--disallowedTools")
    rules = []
    for a in cmd[i + 1:]:
        if a.startswith("--"):
            break
        rules.append(a)
    assert rules and all(r.startswith("Read(//") and r.endswith("/**)") for r in rules)
    assert any("Личное, черновики" in r for r in rules)   # пробел и запятая — внутри скобок
    if sys.platform == "win32":
        drive = str(private)[0].lower()
        assert all(r.startswith(f"Read(//{drive}/") for r in rules)
        assert any(r[5:] == r[5:].lower() for r in rules)  # вариант пути в нижнем регистре
    # Слушатель без инструментов — запреты ему не нужны.
    listener = claude_stream.build_command(["claude"], system_prompt="s", deny_paths=(private,))
    assert "--disallowedTools" not in listener


def test_conversation_passes_deny_paths(fake_cli, tmp_path):
    cli, records = fake_cli

    async def scenario():
        conv = _conv(cli, responder=True, add_dirs=[tmp_path], deny_paths=[tmp_path / "x"])
        try:
            await conv.send("x")
        finally:
            conv.close()

    asyncio.run(scenario())
    (argv,) = _starts(records)
    assert "--disallowedTools" in argv


# --- Fix round 1: изображение отверг API — сеанс не отравлен ------------------------


def _rejecting(monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_REJECT_IMAGES", "1")


def test_rejected_image_forks_the_session_without_that_turn_and_retries(fake_cli, monkeypatch, tmp_path):
    cli, records = fake_cli
    _rejecting(monkeypatch)
    img = tmp_path / "a.png"
    img.write_bytes(PNG)

    async def scenario():
        conv = _conv(cli, persist=True, responder=True)
        try:
            first = await conv.send("код — ПЕЛИКАН")
            sid = conv.session_id
            monkeypatch.setenv("FAKE_CLAUDE_KNOWN", sid)
            second = await conv.send("что на картинке?", images=[img])
            return first, second, sid, conv.session_id
        finally:
            conv.close()

    first, second, sid, new_sid = asyncio.run(scenario())
    assert first.error is None
    assert second.error is None and second.text == '{"op":"none"}'
    assert second.dropped_images == [str(img)] and "не приняла" in second.notes[0]
    rows = records()
    kept = [r["assistant_uuid"] for r in rows if "assistant_uuid" in r][0]
    image_turn = [m for m in _messages(records)
                  if m.get("type") == "user" and isinstance(m["message"]["content"], list)][0]
    fork_argv = _starts(records)[1]
    assert f"--resume={sid}" in fork_argv and "--fork-session" in fork_argv
    assert f"--resume-session-at={kept}" in fork_argv
    assert f"--resume-drops-turn={image_turn['uuid']}" in fork_argv
    retry = _messages(records)[-1]
    assert isinstance(retry["message"]["content"], str) and "не принято моделью" in retry["message"]["content"]
    assert new_sid != sid and new_sid == [r["fork"]["to"] for r in rows if "fork" in r][0]


def test_rejected_image_in_the_first_turn_starts_a_clean_session(fake_cli, monkeypatch, tmp_path):
    cli, records = fake_cli
    _rejecting(monkeypatch)
    img = tmp_path / "a.png"
    img.write_bytes(PNG)

    async def scenario():
        conv = _conv(cli, persist=True)
        try:
            reply = await conv.send("затравка и вопрос", images=[img])
            return reply, conv.session_id
        finally:
            conv.close()

    reply, sid = asyncio.run(scenario())
    assert reply.error is None and reply.dropped_images == [str(img)]
    first, second = _starts(records)
    first_sid = [a for a in first if a.startswith("--session-id=")][0].split("=", 1)[1]
    assert any(a.startswith("--session-id=") for a in second) and sid != first_sid
    assert "затравка и вопрос" in _messages(records)[-1]["message"]["content"]


def test_rejected_image_without_a_cut_point_asks_for_a_seed(fake_cli, monkeypatch, tmp_path):
    """Сеанс продолжен из хранилища, в этом процессе ходов не было — точки
    усечения нет: сеанс забыт, ответ `resume_failed` (вызывающий — затравка)."""
    cli, _ = fake_cli
    _rejecting(monkeypatch)
    sid = str(uuid.uuid4())
    monkeypatch.setenv("FAKE_CLAUDE_KNOWN", sid)
    img = tmp_path / "a.png"
    img.write_bytes(PNG)

    async def scenario():
        conv = _conv(cli, resume=sid)
        try:
            return await conv.send("что тут?", images=[img]), conv.session_id
        finally:
            conv.close()

    reply, current = asyncio.run(scenario())
    assert reply.resume_failed and reply.dropped_images == [str(img)] and current is None


# --- Fix round 2: знаки шаблона в именах закрытых папок ----------------------------

# (папка, файлы внутри — запрещены, «соседи», которые шаблон без экранирования задел бы)
GLOB_CASES = [
    (r"D:\KB\a[1]", ["KB/a[1]/s.md"], ["KB/a1/s.md", "KB/a]/s.md"]),
    (r"D:\KB\b*c", ["KB/b*c/s.md"], ["KB/bXYc/s.md"]),
    (r"D:\KB\f (old)", ["KB/f (old)/s.md", "kb/F (OLD)/s.md"], ["KB/f old/s.md"]),
    (r"D:\KB\{x,y}", ["KB/{x,y}/s.md"], ["KB/x/s.md", "KB/y/s.md"]),
    (r"D:\KB\g+h^$|", ["KB/g+h^$|/s.md"], ["KB/gh/s.md"]),
    (r"D:\KB\Личное, черновики", ["KB/Личное, черновики/s.md"], ["KB/Личное/s.md"]),
    (r"D:\KB\#tag", ["KB/#tag/s.md"], ["KB/tag/s.md"]),
]


def test_glob_metacharacters_in_folder_names_are_escaped():
    from meet.llm import base

    assert base.claude_rule_path(r"D:\KB\a[1]") == r"//d/KB/a\[1\]"
    assert base.claude_rule_path(r"D:\KB\b*c") == r"//d/KB/b\*c"
    assert base.claude_rule_path(r"D:\KB\{x,y}") == "//d/KB/{x,y}"      # в .gitignore не особые
    assert base.claude_rule_path("/home/u/kb/[x]") == r"//home/u/kb/\[x\]"
    # Текст в Read(…): CLI снимает один слой (`\(`→`(`, затем `\\`→`\`).
    assert base.claude_rule_text(r"//d/KB/f \(old\)/**") == r"//d/KB/f \\\(old\\\)/**"
    assert base.claude_rule_text(r"//d/KB/a\[1\]/**") == r"//d/KB/a\\[1\\]/**"


@pytest.mark.skipif(not __import__("os").environ.get("MEET_NODE_IGNORE"),
                    reason="нужна node-ignore: MEET_NODE_IGNORE=<папка пакета ignore>")
def test_escaped_rules_match_only_their_folder_in_node_ignore():
    """Правила проверяются настоящей node-ignore тем же путём, что в claude
    2.1.292: файлы закрытой папки запрещены, «соседи» — нет."""
    import os
    import shutil
    import subprocess

    from meet.llm import base

    node = shutil.which("node")
    if node is None:
        pytest.skip("нет node")
    cases = [{"rule": base.claude_rule_text(base.claude_rule_path(folder) + "/**"),
              "inside": inside, "outside": outside} for folder, inside, outside in GLOB_CASES]
    script = Path(__file__).with_name("node_ignore_rules.js")
    out = subprocess.run([node, str(script), os.environ["MEET_NODE_IGNORE"], json.dumps(cases)],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    for case, res in zip(GLOB_CASES, json.loads(out)):
        assert all(res["inside"]), (case, res)
        assert not any(res["outside"]), (case, res)
