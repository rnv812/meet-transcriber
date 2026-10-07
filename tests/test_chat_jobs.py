"""«Продолжить разговор» после встречи (`jobs.CHAT`, `job_worker._chat`),
склейка журналов чата при объединении встреч (`merge.merge_chats`,
`ChatLog.write_journal`) и квоты материалов под замком id (ревью материалов M7).

Модель — поддельный runner, папки — временные; модель не зовётся."""

import json
import threading
import time
from datetime import datetime

import pytest

from meet import job_worker, jobs, library, materials, merge
from meet.assist import attachments
from meet.assist.chatlog import ChatLog, SESSIONS_JSON, chat_dir
from meet.llm.base import AgentReply, resume_failure

RID = "2026-10-07_10-00"


class Runner:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def __call__(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        item = self.replies.pop(0)
        if isinstance(item, AgentReply):
            return item
        if kwargs.get("on_text") is not None:
            kwargs["on_text"](item)
        return AgentReply(text=item, session_id="th-1")


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    folder = tmp_path / "recordings" / RID
    folder.mkdir(parents=True)
    library.write_transcript(folder, {"version": 1, "title": "Планёрка", "segments": [
        {"start": 0.0, "end": 2.0, "speaker": "Демьян", "text": "Релиз переносим на пятницу."},
        {"start": 3.0, "end": 5.0, "speaker": "Олег", "text": "Согласен."}]})
    return folder


def _lines(capsys) -> list[dict]:
    out = []
    for line in capsys.readouterr().out.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def test_chat_job_seeds_a_new_session_then_resumes_it(folder, capsys):
    log = ChatLog(folder)
    log.append("user", text="Когда релиз?", after_meeting=True)
    runner = Runner('{"say": "В пятницу — так решили Демьян и Олег."}')
    assert job_worker._chat(str(folder), "m1", runner=runner, provider="codex") == 0
    prompt, kwargs = runner.calls[0]
    assert kwargs.get("keep_session") is True and "resume" not in kwargs
    assert "Релиз переносим на пятницу." in prompt          # затравка — расшифровка встречи
    assert "Когда релиз?" in prompt and "Встреча уже закончилась" in prompt
    reply = [m for m in log.messages() if m["kind"] == "agent"][0]
    assert reply["status"] == "shown" and reply["re"] == "m1" and reply["mode"] == "reply"
    assert reply["text"].startswith("В пятницу") and "t" not in reply
    assert log.session_id("codex") == "th-1"
    kinds = [line["kind"] for line in _lines(capsys)]
    assert "chat.updated" in kinds and kinds[-1] == "job.result"
    # Следующее сообщение — продолжение того же сеанса, без затравки.
    log.append("user", text="А кто отвечает?", after_meeting=True)
    runner2 = Runner('{"say": "Демьян."}')
    assert job_worker._chat(str(folder), "m3", runner=runner2, provider="codex") == 0
    prompt2, kwargs2 = runner2.calls[0]
    assert kwargs2.get("resume") == "th-1"
    assert "А кто отвечает?" in prompt2 and "Релиз переносим на пятницу." not in prompt2
    assert [m["text"] for m in log.snapshot(feed=True)["messages"]][-1] == "Демьян."


def test_chat_job_falls_back_to_the_journal_when_resume_fails(folder, capsys):
    log = ChatLog(folder)
    log.set_session_id("codex", "old-session")
    log.append("user", text="Когда релиз?", after_meeting=True)
    runner = Runner(resume_failure("no rollout"), '{"say": "В пятницу."}')
    assert job_worker._chat(str(folder), "m1", runner=runner, provider="codex") == 0
    assert runner.calls[0][1].get("resume") == "old-session"
    seeded, kwargs = runner.calls[1]
    assert kwargs.get("keep_session") is True and "Релиз переносим" in seeded
    assert log.session_id("codex") == "th-1"


def test_chat_job_error_is_visible_and_fails_the_job(folder, capsys):
    log = ChatLog(folder)
    log.append("user", text="Когда релиз?", after_meeting=True)
    runner = Runner(AgentReply(text="", error="сеть недоступна"))
    assert job_worker._chat(str(folder), "m1", runner=runner, provider="codex") == 1
    reply = [m for m in log.messages() if m["kind"] == "agent"][0]
    assert reply["status"] == "failed" and "сеть" in reply["error"]
    assert any(line["kind"] == "error" for line in _lines(capsys))


def test_chat_job_unknown_message(folder, capsys):
    assert job_worker._chat(str(folder), "m9", runner=Runner(), provider="codex") == 3
    assert job_worker._chat(str(folder), "", runner=Runner(), provider="codex") == 3


def test_chat_job_uses_the_live_feed_without_a_transcript(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    folder = tmp_path / "rec" / RID
    folder.mkdir(parents=True)
    (folder / "live_transcript.md").write_text(
        "[00:00:05] Демьян: бюджет утвердили\n", encoding="utf-8")
    assert job_worker._meeting_lines(folder)[0][1] == {"t": 5.0, "speaker": "Демьян",
                                                       "text": "бюджет утвердили"}


def test_chat_job_argv_and_queue_events(tmp_path):
    job = jobs.Job(id="j", kind=jobs.CHAT, folder=str(tmp_path / RID),
                   options={"message": "m4", "provider": "codex"})
    argv = jobs.worker_argv(job)
    assert argv[-2:] == ["--message=m4", "--provider=codex"] and "chat" in argv
    assert jobs.CHAT in jobs.MODEL_KINDS

    def spawn(job, on_line):
        on_line(json.dumps({"kind": "chat.updated"}))
        on_line(json.dumps({"kind": "chat.updated", "partial": {"id": "m5", "text": "При"}}))
        on_line(json.dumps({"kind": "job.result", "path": "chat.jsonl"}))
        return 0

    queue = jobs.JobQueue(spawn=spawn)
    got = []
    queue.bus.subscribe(got.append)
    try:
        job = queue.submit(jobs.CHAT, str(tmp_path / RID), {"message": "m4"})
        deadline = time.monotonic() + 5
        while queue.get(job.id).state != jobs.DONE and time.monotonic() < deadline:
            time.sleep(0.02)
        assert queue.get(job.id).state == jobs.DONE
    finally:
        queue.stop()
    updates = [e.data for e in got if e.kind == jobs.CHAT_UPDATED]
    assert updates == [{"id": RID}, {"id": RID, "partial": {"id": "m5", "text": "При"}}]


# --- объединение встреч --------------------------------------------------------------


def _part(root, name, start, msgs, *, image=False):
    folder = root / name
    folder.mkdir(parents=True)
    log = ChatLog(folder, clock=iter(msgs["at"]).__next__)
    for kind, fields in msgs["items"]:
        log.append(kind, **fields)
    if image:
        files = chat_dir(folder) / "files"
        files.mkdir(parents=True, exist_ok=True)
        (files / "a1.png").write_bytes(b"png")
    ChatLog(folder).set_session_id("codex", f"sess-{name}")
    return merge.Part(id=name, folder=folder, start=start, duration_s=600.0)


def test_merge_concatenates_two_journals_in_time_order(tmp_path):
    root = tmp_path / "rec"
    img = str(root / "p1" / "assistant" / "files" / "a1.png")
    p1 = _part(root, "p1", datetime(2026, 10, 7, 10, 0), {
        "at": [1000.0, 1001.0, 1002.0, 1003.0, 5000.0],
        "items": [
            ("attachment", {"type": "image", "name": "скрин", "status": "ready", "path": img,
                            "ref": "a1"}),
            ("user", {"text": "смотри", "attachments": ["a1"], "t": 10.0}),
            ("agent", {"status": "shown", "text": "вижу", "re": "m1", "t": 12.0}),
            ("agent", {"status": "writing", "text": ""}),
            ("user", {"text": "после встречи", "after_meeting": True}),
        ]}, image=True)
    p2 = _part(root, "p2", datetime(2026, 10, 7, 10, 15), {
        "at": [2000.0, 2001.0],
        "items": [("user", {"text": "вторая часть", "t": 5.0, "client_id": "c-1"}),
                  ("agent", {"status": "shown", "text": "ок", "re": "m1", "t": 6.0})]})
    target = root / "merged"
    target.mkdir()
    parts = [p1, p2]
    count = merge.merge_chats(target, parts, merge.parts_meta(parts))
    msgs = ChatLog(target).load()
    assert count == len(msgs) == 9
    texts = [(m["kind"], m.get("text")) for m in msgs]
    assert texts == [("meeting", "— часть 1 —"), ("attachment", None), ("user", "смотри"),
                     ("agent", "вижу"), ("agent", ""), ("meeting", "— часть 2 —"),
                     ("user", "вторая часть"), ("agent", "ок"), ("user", "после встречи")]
    ids = [m["id"] for m in msgs]
    assert len(set(ids)) == len(ids) and ids[1] == "a1"
    by_text = {m.get("text"): m for m in msgs}
    assert by_text["смотри"]["attachments"] == ["a1"] and by_text["смотри"]["t"] == 10.0
    assert by_text["вижу"]["re"] == by_text["смотри"]["id"]
    assert by_text["вторая часть"]["t"] == 605.0                     # сдвиг части 2
    assert by_text["ок"]["re"] == by_text["вторая часть"]["id"]
    assert by_text["вторая часть"]["client_id"] == "c-1"
    assert by_text["— часть 2 —"]["t"] == 600.0
    assert msgs[4]["status"] == "cancelled"                         # недописанный ответ
    copied = msgs[1]["path"]
    assert copied.startswith(str(target)) and copied.endswith("p1-a1.png")
    assert (target / "assistant" / "files" / "p1-a1.png").read_bytes() == b"png"
    assert [m["seq"] for m in msgs] == list(range(1, 10))
    assert not (chat_dir(target) / SESSIONS_JSON).exists()          # сеансы не переносятся
    # Повтор сборки — журнал уже есть, не переписывается.
    assert merge.merge_chats(target, parts, merge.parts_meta(parts)) == 0
    # Дописывать в склеенный журнал можно как обычно.
    assert ChatLog(target).append("user", text="дальше").message["id"] == "m9"


def test_merge_without_chats_writes_nothing(tmp_path):
    root = tmp_path / "rec"
    a, b = root / "a", root / "b"
    a.mkdir(parents=True)
    b.mkdir()
    parts = [merge.Part("a", a, datetime(2026, 1, 1, 10), 60.0),
             merge.Part("b", b, datetime(2026, 1, 1, 11), 60.0)]
    target = root / "m"
    target.mkdir()
    assert merge.merge_chats(target, parts, merge.parts_meta(parts)) == 0
    assert not chat_dir(target).exists()


def test_write_journal_refuses_bad_input_and_existing_journal(tmp_path):
    log = ChatLog(tmp_path / "x")
    with pytest.raises(ValueError):
        log.write_journal([{"id": "a1", "kind": "user", "at": 1.0}])       # id не того вида
    with pytest.raises(ValueError):
        log.write_journal([{"id": "m1", "kind": "user"}, {"id": "m1", "kind": "agent"}])
    assert log.write_journal([{"id": "m1", "kind": "user", "at": 1.0, "text": "a", "seq": 9}]) == 1
    assert log.load()[0]["seq"] == 1
    with pytest.raises(FileExistsError):
        log.write_journal([{"id": "m2", "kind": "user", "at": 2.0}])


# --- квоты материалов под замком id (ревью материалов M7) ------------------------------------


def _race(fn, n=2):
    errors, results = [], []
    barrier = threading.Barrier(n)

    def work(i):
        barrier.wait()
        try:
            results.append(fn(i))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return results, errors


def test_material_quota_holds_under_concurrent_adds(tmp_path, monkeypatch):
    rec = tmp_path / "rec"
    rec.mkdir()
    docs = []
    for i in range(2):
        doc = tmp_path / f"d{i}.md"
        doc.write_text(f"# Документ {i}\n\nтекст {i}\n", encoding="utf-8")
        docs.append(doc)
    monkeypatch.setattr(materials, "MAX_MATERIALS", 1)
    real_parse = materials.parse

    def slow_parse(path):
        time.sleep(0.3)      # оба прошли быструю проверку до разбора
        return real_parse(path)

    monkeypatch.setattr(materials, "parse", slow_parse)
    results, errors = _race(lambda i: materials.add(rec, docs[i]))
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], materials.MaterialError)
    assert len(materials.records(rec)) == 1


def test_session_chars_quota_holds_under_concurrent_adds(tmp_path, monkeypatch):
    rec = tmp_path / "rec"
    rec.mkdir()
    docs = []
    for i in range(2):
        doc = tmp_path / f"d{i}.md"
        doc.write_text("слово " * 100, encoding="utf-8")
        docs.append(doc)
    real_parse = materials.parse
    first = real_parse(docs[0])
    monkeypatch.setattr(materials, "MAX_SESSION_CHARS", first.chars + 10)

    def slow_parse(path):
        time.sleep(0.3)
        return real_parse(path)

    monkeypatch.setattr(materials, "parse", slow_parse)
    results, errors = _race(lambda i: materials.add(rec, docs[i]))
    assert len(results) == 1 and len(errors) == 1
    assert len(materials.records(rec)) == 1


def test_image_quota_holds_under_concurrent_saves(tmp_path, monkeypatch):
    import io

    from PIL import Image

    rec = tmp_path / "rec"
    rec.mkdir()
    out = io.BytesIO()
    Image.new("RGB", (4, 4), (0, 0, 0)).save(out, "PNG")
    monkeypatch.setattr(attachments, "MAX_PER_SESSION", 1)
    real = attachments.normalize

    def slow(data):
        time.sleep(0.3)
        return real(data)

    monkeypatch.setattr(attachments, "normalize", slow)
    results, errors = _race(lambda i: attachments.save(rec, out.getvalue()))
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], attachments.AttachmentError)
    assert attachments.count(rec) == 1


def test_merge_run_glues_the_chats_of_its_parts(tmp_path, monkeypatch):
    from test_merge import FakeRun, _at, _recording

    monkeypatch.setattr(merge.shutil, "which", lambda name: name)
    a = _recording(tmp_path, "2026-09-30_10-00", started=_at("2026-09-30T10:00:00"))
    b = _recording(tmp_path, "2026-09-30_10-30", started=_at("2026-09-30T10:30:00"))
    ChatLog(a).append("user", text="первая", t=30.0)
    ChatLog(b).append("user", text="вторая", t=20.0)
    folder = merge.create(tmp_path, [a, b], keep_originals=False)
    merge.run(folder, run=FakeRun(), probe=lambda p: 600.0)
    msgs = ChatLog(folder).load()
    assert [(m.get("text"), m.get("t")) for m in msgs] == [
        ("— часть 1 —", 0.0), ("первая", 30.0), ("— часть 2 —", 600.0), ("вторая", 620.0)]
