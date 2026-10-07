"""Чат агента-участника в ребёнке (`meet assist`, V4 задача 6): маршруты
`/chat*`, `/agent/frequency` и события SSE `chat_snapshot` / `chat` /
`chat_partial` / `agent`.

Агент — настоящий `Participant` с поддельным runner (модель не зовётся), журнал
и шина — настоящие, во временной папке; состояние ассистента — утиное, как
FakeState в test_assist_web.py."""

import asyncio
import io
import json

from aiohttp.test_utils import TestClient, TestServer

from meet.assist.bus import TranscriptBus
from meet.assist.chatlog import ChatLog
from meet.assist.participant import Participant
from meet.assist.web import PASTE_MAX_BYTES, TOKEN_HEADER, ChatFeed, build_app, check_attach_path
from meet.llm.base import AgentReply


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0   # каждый взгляд на часы — секунда: chat_partial не душится
        return self.t


class Runner:
    """Codex-подобный вызов на ход: кусками текста в on_text, затем ответ."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def __call__(self, prompt, *, on_text=None, **kwargs):
        self.calls.append((prompt, kwargs))
        text = self.replies.pop(0)
        if on_text is not None:
            on_text(text[: len(text) // 2])
            on_text(text[len(text) // 2:])
        return AgentReply(text=text, session_id="th-1")


class ChatState:
    """Утиное AssistState для web: шина, агент-участник, лента чата, токен."""

    def __init__(self, tmp_path, *, token=None, runner=None, participant=True):
        self.bus = TranscriptBus()
        self.changes = self.bus.changed
        self.folder = tmp_path / "lib" / "2026-10-07_10-00"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.chat = ChatLog(self.folder, log=lambda _m: None)
        self.runner = runner or Runner('{"say": "Привет, я тут", "buttons": ["Глянь", "Не надо"]}')
        self.participant = None
        self.chat_feed = None
        if participant:
            self.participant = Participant(
                self.bus, self.chat, provider="codex", folder=self.folder, runner=self.runner,
                library_root=self.folder.parent, clock=Clock(), log=lambda _m: None)
            self.participant.add_listener(lambda _n, _d: self.bus.changed.notify())
            self.chat_feed = ChatFeed(self.participant)
        self.control_token = token
        self.saved: list[str] = []
        self.stop_event = asyncio.Event()

    def persist_frequency(self, key):
        self.saved.append(key)

    def signature(self):
        return (self.participant.version if self.participant else None,)

    def view(self):
        return {"agent": self.participant.view()} if self.participant else {}

    def qa_version(self):
        return 0

    def qa_items(self):
        return []

    def request_stop(self):
        self.stop_event.set()
        self.bus.changed.notify()


def _run(coro):
    return asyncio.run(coro)


def _png(size=(4, 3)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, (200, 10, 10)).save(out, "PNG")
    return out.getvalue()


async def _read_events(resp, count):
    out = []
    while len(out) < count:
        raw = (await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 5)).decode()
        if raw.startswith(":"):
            continue
        fields = {}
        for row in raw.strip().splitlines():
            key, _, value = row.partition(": ")
            fields[key] = value
        out.append((fields.get("event"), json.loads(fields["data"])))
    return out


async def _until(resp, name, limit=40):
    """Читать события до первого `name` (остальные — в список)."""
    seen = []
    for _ in range(limit):
        (event, data), = await _read_events(resp, 1)
        seen.append((event, data))
        if event == name:
            return data, seen
    raise AssertionError(f"нет события {name}: {[e for e, _ in seen]}")


# --- POST /chat, GET /chat ------------------------------------------------------


def test_post_chat_is_202_and_idempotent_by_client_id(tmp_path):
    async def scenario():
        state = ChatState(tmp_path)
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.post("/chat", json={"text": "  Что с бюджетом?  ", "client_id": "c-1"})
            assert r.status == 202
            first = await r.json()
            assert first["id"] == "m1" and first["attachments"] == []
            r = await client.post("/chat", json={"text": "Что с бюджетом?", "client_id": "c-1"})
            assert r.status == 202
            again = await r.json()
            assert again["id"] == "m1" and again["duplicate"] is True
            users = [m for m in state.chat.messages() if m["kind"] == "user"]
            assert len(users) == 1 and users[0]["text"] == "Что с бюджетом?"
            r = await client.get("/chat")
            body = await r.json()
            assert [m["id"] for m in body["messages"]] == ["m1"]
            assert body["seq"] == 1 and body["agent"]["provider"] == "codex"
            assert body["partial"] is None

    _run(scenario())


def test_post_chat_validates_body(tmp_path):
    async def scenario():
        state = ChatState(tmp_path)
        async with TestClient(TestServer(build_app(state))) as client:
            for body in ({}, {"text": "   "}, {"text": 5}, {"text": "x" * 9000},
                         {"text": "a", "attachments": ["C:/секрет.docx"]},
                         {"text": "a", "attachments": "a1"}, {"text": "a", "client_id": 7}):
                r = await client.post("/chat", json=body)
                assert r.status == 400, body
            r = await client.post("/chat", data=b"{broken", headers={"Content-Type": "application/json"})
            assert r.status == 400
            assert state.chat.messages() == []

    _run(scenario())


def test_chat_routes_are_409_without_participant(tmp_path):
    async def scenario():
        state = ChatState(tmp_path, participant=False)
        async with TestClient(TestServer(build_app(state))) as client:
            assert (await client.get("/chat")).status == 409
            assert (await client.post("/chat", json={"text": "a"})).status == 409
            assert (await client.post("/chat/stop", json={})).status == 409
            assert (await client.post("/chat/m1/react", json={"emoji": "👍"})).status == 409

    _run(scenario())


def test_chat_after_shutdown_is_409(tmp_path):
    async def scenario():
        state = ChatState(tmp_path)
        async with TestClient(TestServer(build_app(state))) as client:
            await state.participant.shutdown()   # поток журнала закрыт
            r = await client.post("/chat", json={"text": "ещё?"})
            assert r.status == 409

    _run(scenario())


# --- вложения ---------------------------------------------------------------------


def test_paste_image_then_reference_it_in_a_message(tmp_path):
    async def scenario():
        state = ChatState(tmp_path)
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.post("/chat/paste", data=_png(),
                                  headers={"Content-Type": "image/png",
                                           "X-File-Name": "%D1%81%D0%BA%D1%80%D0%B8%D0%BD.png"})
            assert r.status == 200
            pasted = await r.json()
            assert pasted["id"] == "a1" and pasted["status"] == "ready"
            assert pasted["attachment"]["type"] == "image"
            assert pasted["attachment"]["name"] == "скрин.png"
            r = await client.post("/chat", json={"text": "вот", "attachments": ["a1"],
                                                 "client_id": "c-2"})
            assert (await r.json())["attachments"] == ["a1"]
            user = state.chat.get("m1")
            assert user["attachments"] == ["a1"]

    _run(scenario())


def test_paste_limits_type_and_size(tmp_path):
    async def scenario():
        state = ChatState(tmp_path)
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.post("/chat/paste", data=b"%PDF-1.4",
                                  headers={"Content-Type": "application/pdf"})
            assert r.status == 415
            big = b"\x89PNG" + b"0" * PASTE_MAX_BYTES
            r = await client.post("/chat/paste", data=big, headers={"Content-Type": "image/png"})
            assert r.status == 413
            r = await client.post("/chat/paste", data=b"", headers={"Content-Type": "image/png"})
            assert r.status == 400
            # Не картинка с типом картинки — запись вложения «не разобрано».
            r = await client.post("/chat/paste", data=b"not an image",
                                  headers={"Content-Type": "image/png"})
            body = await r.json()
            assert r.status == 200 and body["status"] == "failed" and body["error"]
            assert state.chat.get(body["id"])["status"] == "failed"

    _run(scenario())


def test_attach_path_needs_the_resident_token(tmp_path):
    note = tmp_path / "План.md"
    note.write_text("# План\n\nСрок — пятница.\n", encoding="utf-8")

    async def scenario():
        state = ChatState(tmp_path, token="s3cret")
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.post("/chat/attach", json={"path": str(note)})
            assert r.status == 403                       # без токена
            r = await client.post("/chat/attach", json={"path": str(note)},
                                  headers={TOKEN_HEADER: "wrong"})
            assert r.status == 403
            r = await client.post("/chat/attach", json={"path": str(note)},
                                  headers={TOKEN_HEADER: "s3cret"})
            assert r.status == 200
            body = await r.json()
            assert body["status"] == "ready" and body["attachment"]["type"] == "doc"
            assert state.chat.get(body["id"])["kind"] == "attachment"
            # Без токена (консоль) путь не принимается ни от кого (ревью I4).
            state.control_token = None
            r = await client.post("/chat/attach", json={"path": str(note)})
            assert r.status == 403
            r = await client.post("/chat/attach", json={"path": str(note)},
                                  headers={TOKEN_HEADER: "s3cret"})
            assert r.status == 403

    _run(scenario())


def test_attach_path_is_validated(tmp_path):
    async def scenario():
        state = ChatState(tmp_path, token="t")
        headers = {TOKEN_HEADER: "t"}
        async with TestClient(TestServer(build_app(state))) as client:
            for path in ("relative/file.md", "//server/share/x.docx", "\\\\server\\share\\x.docx",
                         str(tmp_path / "нет.md"), "", 5, "C:/a\x00b"):
                r = await client.post("/chat/attach", json={"path": path}, headers=headers)
                assert r.status == 400, path
            assert state.chat.messages() == []

    _run(scenario())


def test_check_attach_path_accepts_files_and_folders(tmp_path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    assert check_attach_path(str(tmp_path / "f.txt")) == tmp_path / "f.txt"
    assert check_attach_path(f"  {tmp_path}  ") == tmp_path


# --- кнопки, реакции, стоп ------------------------------------------------------------


def test_click_react_stop(tmp_path):
    async def scenario():
        state = ChatState(tmp_path)
        agent = state.chat.append("agent", status="shown", text="Глянуть план?",
                                  buttons=["Глянь", "Не надо"]).message
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.post(f"/chat/{agent['id']}/click", json={"label": "Глянь",
                                                                     "client_id": "k1"})
            assert r.status == 200
            click = await r.json()
            msg = state.chat.get(click["id"])
            assert msg["via"] == "button" and msg["re"] == agent["id"]
            r = await client.post(f"/chat/{agent['id']}/click", json={"label": "Нет такой"})
            assert r.status == 400
            r = await client.post("/chat/bogus/click", json={"label": "Глянь"})
            assert r.status == 400
            r = await client.post(f"/chat/{agent['id']}/react", json={"emoji": "👍"})
            assert await r.json() == {"ok": True, "changed": True}
            r = await client.post(f"/chat/{agent['id']}/react", json={"emoji": "👍", "on": True})
            assert await r.json() == {"ok": True, "changed": False}
            r = await client.post(f"/chat/{agent['id']}/react", json={"emoji": "🔥"})
            assert r.status == 400
            r = await client.post(f"/chat/{agent['id']}/react", json={"emoji": "👍", "on": "yes"})
            assert r.status == 400
            assert state.chat.get(agent["id"])["reactions"].keys() == {"👍"}
            r = await client.post("/chat/stop", json={})
            assert await r.json() == {"ok": False}      # ничего не пишется
            r = await client.post("/chat/stop", json={"id": "x1"})
            assert r.status == 400

    _run(scenario())


# --- «Как часто писать» -----------------------------------------------------------------


def test_agent_frequency_maps_both_ways_and_persists_the_key(tmp_path):
    async def scenario():
        state = ChatState(tmp_path)
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.put("/agent/frequency", json={"frequency": "реже"})
            assert await r.json() == {"frequency": "less", "label": "реже", "live": True,
                                      "saved": True}
            assert state.participant.frequency == "реже" and state.saved == ["less"]
            r = await client.put("/agent/frequency", json={"frequency": " Normal "})
            assert (await r.json())["label"] == "обычно"
            assert state.participant.frequency == "обычно" and state.saved == ["less", "normal"]
            # Резидент сохраняет сам: persist false — только агенту.
            r = await client.put("/agent/frequency", json={"frequency": "more", "persist": False})
            assert (await r.json())["saved"] is False
            assert state.participant.frequency == "чаще" and state.saved == ["less", "normal"]
            for bad in ({"frequency": "always"}, {}, {"frequency": "more", "persist": "no"}):
                assert (await client.put("/agent/frequency", json=bad)).status == 400
            r = await client.put("/agent/frequency", json={"frequency": "less"},
                                 headers={"Origin": "http://evil.example"})
            assert r.status == 403

    _run(scenario())


def test_frequency_key_reverse_mapping():
    from meet.settings import FREQUENCY_LABELS, frequency_key

    from meet.assist import participant_prompts as pp

    for key, label in FREQUENCY_LABELS.items():
        assert frequency_key(key) == key and frequency_key(label) == key
        assert pp.normalize_frequency(key) == label   # то же, что видит агент
    assert frequency_key("ЧАЩЕ") == "more"
    assert frequency_key("иногда") is None and frequency_key(None) is None


def test_frequency_persists_into_settings(tmp_path, monkeypatch):
    """`AssistState.persist_frequency` пишет ключ в config.json (временный)."""
    from meet import settings
    from meet.assist.app import AssistState
    from meet.assist.live_state import LiveState

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    state = AssistState(bus=TranscriptBus(), live=LiveState(), glossary="", vault=None, cwd=tmp_path)
    state.persist_frequency("less")
    assert settings.load().assist.frequency == "less"


# --- SSE --------------------------------------------------------------------------------


def test_sse_snapshot_then_chat_partial_and_agent_events(tmp_path):
    async def scenario():
        state = ChatState(tmp_path)
        state.chat.append("agent", status="dropped", text="скрытая")      # не в ленте
        state.chat.append("user", text="раньше")
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events") as resp:
                snap, _ = await _until(resp, "chat_snapshot")
                assert [m["text"] for m in snap["messages"]] == ["раньше"]
                assert snap["seq"] == 2 and snap["agent"]["state"] == "listening"
                r = await client.post("/chat", json={"text": "Привет", "client_id": "c1"})
                assert r.status == 202
                added, _ = await _until(resp, "chat")
                assert added["op"] == "add" and added["message"]["text"] == "Привет"
                assert added["seq"] > snap["seq"]
                assert await state.participant.tick()        # ход агента (поддельный runner)
                partial, seen = await _until(resp, "chat_partial")
                writing = [d for e, d in seen if e == "chat" and d["op"] == "add"]
                assert writing and writing[0]["message"]["status"] == "writing"
                assert partial["id"] == writing[0]["message"]["id"] and partial["text"]
                done, seen = await _until(resp, "chat")
                while not (done["op"] == "patch" and done["set"].get("status") == "shown"):
                    done, more = await _until(resp, "chat")
                    seen += more
                assert done["set"]["text"] == "Привет, я тут"
                assert done["set"]["buttons"] == ["Глянь", "Не надо"]
                agent, _ = await _until(resp, "agent")
                assert agent["provider"] == "codex"

    _run(scenario())


def test_sse_hidden_adds_are_not_sent(tmp_path):
    feed = ChatFeed()
    feed.push("chat", {"seq": 1, "op": "add", "message": {"id": "m1", "kind": "tool"}})
    feed.push("chat", {"seq": 2, "op": "add",
                       "message": {"id": "m2", "kind": "agent", "status": "dropped"}})
    feed.push("chat", {"seq": 3, "op": "patch", "id": "m2", "set": {"status": "dropped"}})
    feed.push("chat", {"seq": 4, "op": "add", "message": {"id": "m3", "kind": "user"}})
    feed.push("qa", {})
    assert [d["seq"] for _, _, d in feed.since(0)] == [3, 4]
    assert feed.since(feed.count) == []


def test_sse_feed_gap_asks_for_a_new_snapshot():
    feed = ChatFeed()
    feed.MAX = 3
    import collections

    feed._events = collections.deque(maxlen=3)
    for n in range(5):
        feed.push("chat_partial", {"id": "m1", "text": str(n)})
    assert feed.since(0) is None              # отстал больше буфера
    assert [d["text"] for _, _, d in feed.since(2)] == ["2", "3", "4"]


# --- агент: правки по ревью участника (M5, M11) и API «после встречи» ------------------


def test_journal_failure_after_a_shown_reply_does_not_repeat_it(tmp_path):
    """M5: модель ответила, первое сообщение показано, журнал упал на втором —
    входы не возвращаются в очередь (иначе следующий ход повторил бы ответ)."""
    two = '{"say": "Первое"}\n{"say": "Второе"}'
    state = ChatState(tmp_path, runner=Runner(two))
    p = state.participant
    p._merge_window = 0
    real = state.chat.append

    def append(kind, *a, **kw):
        if kind == "agent" and kw.get("status") == "shown":   # второе сообщение ответа
            raise OSError("диск отвалился")
        return real(kind, *a, **kw)

    async def main():
        await p.post_user_message("Привет", client_id="c")
        state.chat.append = append
        assert await p.tick()
        assert p._user == [] and not p.turn_due()
        shown = [m for m in state.chat.messages() if m["kind"] == "agent"]
        assert [m["text"] for m in shown] == ["Первое"] and shown[0]["status"] == "shown"
        await p.shutdown()

    _run(main())


def test_task_context_change_reaches_a_running_session(tmp_path):
    runner = Runner('{"silent": true}', '{"silent": true}')
    state = ChatState(tmp_path, runner=runner)
    p = state.participant

    async def main():
        await p.post_user_message("Привет", client_id="c1")
        assert await p.tick()
        p.set_task_context("Ревью архитектуры платежей")
        await p.post_user_message("Дальше", client_id="c2")
        assert await p.tick()
        assert "Ревью архитектуры платежей" in runner.calls[1][0]
        await p.shutdown()

    _run(main())


def test_queue_existing_message_written_by_another_writer(tmp_path):
    runner = Runner('{"say": "Отвечаю"}')
    state = ChatState(tmp_path, runner=runner)
    agent = state.chat.append("agent", status="shown", text="Глянуть?", buttons=["Глянь"]).message
    click = state.chat.click_button(agent["id"], "Глянь")       # «резидент» записал нажатие
    p = state.participant

    async def main():
        await p.queue_existing(click.message["id"])
        p.add_note("Встреча уже закончилась.")
        assert await p.tick()
        prompt = runner.calls[0][0]
        assert "Глянь" in prompt and "Глянуть?" in prompt and "Встреча уже закончилась." in prompt
        try:
            await p.queue_existing(agent["id"])                # не сообщение пользователя
        except ValueError:
            pass
        else:
            raise AssertionError("ждали ValueError")
        await p.shutdown()

    _run(main())


def test_foreign_host_is_refused(tmp_path):
    """I3: страница с чужим именем, указывающим на 127.0.0.1 (DNS-подмена),
    не читает ни ленту, ни чат."""
    async def scenario():
        state = ChatState(tmp_path)
        async with TestClient(TestServer(build_app(state))) as client:
            port = client.server.port
            for host in (f"evil.example:{port}", "evil.example", f"127.0.0.1:{port + 1}",
                         f"localhost.evil:{port}"):
                for path in ("/chat", "/events", "/"):
                    r = await client.get(path, headers={"Host": host})
                    assert r.status == 403, (host, path)
            r = await client.post("/chat", json={"text": "a"}, headers={"Host": f"evil.example:{port}"})
            assert r.status == 403 and state.chat.messages() == []
            for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
                assert (await client.get("/chat", headers={"Host": host})).status == 200

    _run(scenario())


def test_failed_snapshot_is_retried(tmp_path):
    """M2: снимок не прочитался — он повторяется, а не уходят одни дельты."""
    async def scenario():
        state = ChatState(tmp_path)
        real = state.participant.snapshot
        calls = {"n": 0}

        async def flaky(limit=200):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("журнал занят")
            return await real(limit)

        state.participant.snapshot = flaky
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events") as resp:
                await _read_events(resp, 1)          # state
                await client.post("/chat", json={"text": "раз", "client_id": "c1"})
                snap, seen = await _until(resp, "chat_snapshot")
                assert [m["text"] for m in snap["messages"]] == ["раз"]
                assert not [e for e, _ in seen if e == "chat"]   # дельт до снимка нет

    _run(scenario())



def test_live_silence_to_a_user_message_leaves_a_visible_line(tmp_path):
    """I2 во время встречи: на сообщение пользователя агент промолчал — строка
    «Ассистенту нечего добавить»; молчание по репликам встречи — без неё."""
    runner = Runner('{"silent": true}', '{"silent": true}')
    state = ChatState(tmp_path, runner=runner)
    p = state.participant

    async def main():
        await p.post_user_message("Спасибо", client_id="c1")
        assert await p.tick()
        state.bus.publish("[00:00:05] Демьян: дальше", {"t": 5.0, "speaker": "Демьян", "text": "дальше"})
        p._pause = p._min_gap = -5.0     # пауза — сразу (часы теста тикают на каждый взгляд)
        assert await p.tick()
        assert "дальше" in runner.calls[1][0]
        await p.shutdown()

    _run(main())
    feed = state.chat.snapshot(feed=True)["messages"]
    assert [(m["kind"], m.get("text"), m.get("re")) for m in feed] == [
        ("user", "Спасибо", None), ("system", "Ассистенту нечего добавить", "m1")]


def test_duplicate_post_of_an_unanswered_message_queues_it_again(tmp_path):
    """I1 во время встречи: ребёнок перезапустился, окно повторило POST /chat —
    сообщение без ответа снова уходит агенту."""
    state = ChatState(tmp_path, runner=Runner('{"say": "Отвечаю"}'))
    state.chat.append("user", text="Сроки?", client_id="c1")      # прошлый ребёнок
    p = state.participant

    async def main():
        again = await p.post_user_message("Сроки?", client_id="c1")
        assert again["duplicate"] is True and again["queued"] is True
        assert (await p.post_user_message("Сроки?", client_id="c1"))["queued"] is False   # уже ждёт
        assert await p.tick()
        assert (await p.post_user_message("Сроки?", client_id="c1"))["queued"] is False   # отвечено
        await p.shutdown()

    _run(main())
    agent = [m for m in state.chat.messages() if m["kind"] == "agent"]
    assert agent[0]["re"] == "m1" and agent[0]["text"] == "Отвечаю"
