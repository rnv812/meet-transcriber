import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from meet.assist.web import build_app


class FakeState:
    class _B:
        @staticmethod
        def since(i):
            return (["[00:00:01] Вы: привет"], 1)

        @staticmethod
        def entries_since(i):
            return ([{"t": 1.0, "speaker": "Вы", "text": "привет"}][i:], 1)

        @staticmethod
        def size():
            return 1

    bus = _B()
    tasks_set: list[str] = []

    @staticmethod
    def status():
        return None

    def signature(self):
        return (1, self.status())

    def qa_version(self):
        return self.qa_v

    def qa_items(self):
        return [{"id": 1, "q": "срок?", "a": "пятница", "error": None, "pending": False,
                 "at": 1.0, "quick": None}] if self.qa_v else []

    def view(self):
        return {"version": 1, "digest": "### Решения\n- Тезис",
                "summary": {"topic": "Запуск", "points": [], "decisions": [{"id": "d1", "text": "Тезис"}],
                            "tasks": [], "open_questions": []},
                "hints": [{"id": "h1", "kind": "risk", "text": "Нет владельца"}],
                "status": self.status()}

    def hint_action(self, hint_id, action):
        self.hint_calls.append((hint_id, action))
        return hint_id == "h1"

    class _QA:
        calls: list = []

        @classmethod
        async def ask(cls, q, quick=None, since_t=None):
            cls.calls.append((q, quick, since_t))
            return f"ответ на: {q or quick}"

    qa = _QA()

    async def set_task(self, name):
        self.tasks_set.append(name)

    def __init__(self):
        self.hint_calls = []
        self.qa_v = 0


def _run(coro):
    return asyncio.run(coro)


def test_index_ask_and_task():
    async def scenario():
        state = FakeState()
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.get("/")
            assert r.status == 200 and "text/html" in r.headers["Content-Type"]
            r = await client.post("/ask", json={"question": "срок?"})
            assert (await r.json())["answer"] == "ответ на: срок?"
            r = await client.post("/task", json={"task": "demo"})
            assert r.status == 204 and state.tasks_set == ["demo"]

    _run(scenario())


def test_ask_is_409_when_the_participant_replaces_qa():
    async def scenario():
        state = FakeState()
        state.qa = None   # агент-участник включён: «Спросить» нет
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.post("/ask", json={"question": "срок?"})
            assert r.status == 409 and "чате" in await r.text()

    _run(scenario())


def test_ask_broken_json_returns_400():
    async def scenario():
        async with TestClient(TestServer(build_app(FakeState()))) as client:
            r = await client.post(
                "/ask", data=b"{broken",
                headers={"Content-Type": "application/json"})
            assert r.status == 400

    _run(scenario())


def test_ask_non_utf8_body_returns_400():
    async def scenario():
        async with TestClient(TestServer(build_app(FakeState()))) as client:
            r = await client.post(
                "/ask", data="вопрос".encode("cp1251"),
                headers={"Content-Type": "application/json"})
            assert r.status == 400

    _run(scenario())


def test_sse_first_event_has_digest():
    async def scenario():
        async with TestClient(TestServer(build_app(FakeState()))) as client:
            async with client.get("/events") as resp:
                assert resp.status == 200
                raw = await resp.content.readuntil(b"\n\n")
                payload = json.loads(
                    raw.decode("utf-8").split("data: ", 1)[1].strip()
                )
                assert "Тезис" in payload["digest"]
                assert payload["summary"]["topic"] == "Запуск"
                assert payload["hints"][0]["id"] == "h1"
                assert "transcript" not in payload  # панели хвост ленты строками не нужен

    _run(scenario())


class BusState(FakeState):
    """Состояние с настоящей шиной и событием остановки."""

    def __init__(self):
        super().__init__()
        from meet.assist.bus import TranscriptBus

        self.bus = TranscriptBus()
        self.stop_event = asyncio.Event()
        self.stop_requests = 0

    def request_stop(self):
        self.stop_requests += 1
        self.stop_event.set()
        self.bus.changed.notify()  # как AssistState: поток замечает сразу


async def _read_events(resp, count):
    """Читает из SSE `count` событий как (event, id, data)."""
    out = []
    while len(out) < count:
        raw = (await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 5)).decode()
        fields = {}
        for row in raw.strip().splitlines():
            key, _, value = row.partition(": ")
            fields[key] = value
        out.append((fields.get("event"), fields.get("id"),
                    json.loads(fields["data"])))
    return out


def test_stop_route_requests_shutdown():
    async def scenario():
        state = BusState()
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.post("/stop")
            assert r.status == 200 and await r.json() == {"ok": True}
            assert state.stop_requests == 1 and state.stop_event.is_set()

    _run(scenario())


def test_sse_sends_structured_line_for_each_new_line():
    async def scenario():
        state = BusState()
        state.bus.publish("[00:00:03] Вы: привет",
                          {"t": 3.0, "speaker": "Вы", "text": "привет"})
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events") as resp:
                events = await _read_events(resp, 4)
                kinds = {e[0] for e in events}
                assert kinds == {"state", "qa", "voices", "line"}
                line = next(e for e in events if e[0] == "line")
                assert line[1] == "0"
                assert line[2] == {"t": 3.0, "speaker": "Вы", "text": "привет"}
                state.bus.publish("[00:00:09] Собеседник: да",
                                  {"t": 9.5, "speaker": "Собеседник", "text": "да"})
                # Новая строка — только `line`: `state` от неё не меняется.
                new = await _read_events(resp, 1)
                assert new[0][0] == "line"
                assert new[0][1] == "1"
                assert new[0][2]["speaker"] == "Собеседник"
                assert new[0][2]["text"] == "да"

    _run(scenario())


def test_sse_resumes_after_last_event_id():
    """Переподключившийся EventSource шлёт Last-Event-ID — строки не дублируются."""
    async def scenario():
        state = BusState()
        for i in range(3):
            state.bus.publish(f"l{i}", {"t": float(i), "speaker": "Вы", "text": f"l{i}"})
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events",
                                  headers={"Last-Event-ID": "1"}) as resp:
                events = await _read_events(resp, 4)
                lines = [e for e in events if e[0] == "line"]
                assert [e[2]["text"] for e in lines] == ["l2"]

    _run(scenario())


def test_sse_closes_when_stop_requested():
    async def scenario():
        state = BusState()
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events") as resp:
                await _read_events(resp, 1)
                state.request_stop()
                rest = await asyncio.wait_for(resp.content.read(), 5)
                assert b"event: line" not in rest  # поток просто закончился

    _run(scenario())


def test_post_routes_reject_foreign_origin():
    async def scenario():
        state = BusState()
        async with TestClient(TestServer(build_app(state))) as client:
            evil = {"Origin": "http://evil.example"}
            r = await client.post("/stop", headers=evil)
            assert r.status == 403 and state.stop_requests == 0
            r = await client.post("/ask", json={"question": "x"}, headers=evil)
            assert r.status == 403
            r = await client.post("/task", json={"task": "x"}, headers=evil)
            assert r.status == 403
            # Чужой порт на том же хосте — тоже чужой origin.
            r = await client.post("/stop", headers={"Origin": "http://127.0.0.1:1"})
            assert r.status == 403 and state.stop_requests == 0

    _run(scenario())


def test_post_routes_allow_own_origin_and_no_origin():
    async def scenario():
        state = BusState()
        async with TestClient(TestServer(build_app(state))) as client:
            port = client.server.port
            for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                r = await client.post("/ask", json={"question": "срок?"},
                                      headers={"Origin": origin})
                assert r.status == 200
            r = await client.post("/stop")  # резидент: без Origin
            assert r.status == 200 and state.stop_requests == 1
            r = await client.post("/stop",
                                  headers={"Origin": f"http://localhost:{port}"})
            assert r.status == 200 and state.stop_requests == 2

    _run(scenario())


def test_hint_route_validates_and_forwards():
    async def scenario():
        state = FakeState()
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.post("/hint", json={"id": "h1", "action": "pin"})
            assert r.status == 200 and await r.json() == {"ok": True, "changed": True}
            r = await client.post("/hint", json={"id": "h9", "action": "dismiss"})
            assert (await r.json())["changed"] is False
            r = await client.post("/hint", json={"id": "h1", "action": "restore"})
            assert r.status == 200
            for bad in ({"id": "h1", "action": "delete"}, {"action": "pin"}, [1]):
                r = await client.post("/hint", json=bad)
                assert r.status == 400
            r = await client.post("/hint", json={"id": "h1", "action": "pin"},
                                  headers={"Origin": "http://evil.example"})
            assert r.status == 403
        assert state.hint_calls == [("h1", "pin"), ("h9", "dismiss"), ("h1", "restore")]

    _run(scenario())


def test_ask_quick_action_and_validation():
    async def scenario():
        state = FakeState()
        FakeState._QA.calls = []
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.post("/ask", json={"quick": "missed", "since_t": 120})
            assert r.status == 200 and (await r.json())["answer"] == "ответ на: missed"
            for bad in ({"quick": "dance"}, {"question": ""}, {"quick": "brief", "since_t": -5},
                        {"question": 7}, ["вопрос"]):
                r = await client.post("/ask", json=bad)
                assert r.status == 400, bad
        assert FakeState._QA.calls == [(None, "missed", 120)]

    _run(scenario())


def test_qa_event_only_when_history_changes_and_transcript_only_for_the_page():
    async def scenario():
        state = BusState()
        state.bus.publish("[00:00:03] Вы: привет", {"t": 3.0, "speaker": "Вы", "text": "привет"})
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events") as resp:
                first = await _read_events(resp, 4)
                qa = next(e for e in first if e[0] == "qa")
                assert qa[2] == {"qa": []}
                state.qa_v = 1
                state.bus.changed.notify()  # QAService сигналит о новой истории
                again = await _read_events(resp, 1)
                assert again[0][0] == "qa" and again[0][2]["qa"][0]["a"] == "пятница"
            async with client.get("/events?transcript=1") as resp:
                events = await _read_events(resp, 4)
                st = next(e for e in events if e[0] == "state")
                assert st[2]["transcript"] == ["[00:00:03] Вы: привет"]

    _run(scenario())


def _page_script() -> str:
    from meet.assist.web import PAGE

    return PAGE.split("<script>", 1)[1].split("</script>", 1)[0]


def test_page_script_string_literals_have_no_raw_newlines():
    import re

    script = _page_script()
    for m in re.finditer(r"'[^']*'|\"[^\"]*\"", script):
        assert "\n" not in m.group(0), m.group(0)


def test_page_script_parses_with_node(tmp_path):
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node не найден")
    path = tmp_path / "page.js"
    path.write_text(_page_script(), encoding="utf-8")
    run = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


def test_sse_pushes_a_new_line_within_100_ms():
    """Поток не опрашивает состояние раз в секунду: новая реплика (из потока
    распознавания) уходит клиенту сразу по сигналу изменений."""
    import threading
    import time

    async def scenario():
        state = BusState()
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events") as resp:
                await _read_events(resp, 3)          # state + qa + voices
                await asyncio.sleep(0.05)            # поток ждёт сигнала
                sent = {}

                def publish():
                    sent["t"] = time.monotonic()
                    state.bus.publish("[00:00:02] Ольга: новость",
                                      {"t": 2.0, "speaker": "Ольга", "text": "новость"})

                threading.Thread(target=publish).start()
                event = (await _read_events(resp, 1))[0]
                took = time.monotonic() - sent["t"]
                return event, took

    event, took = _run(scenario())
    assert event[0] == "line" and event[2]["text"] == "новость"
    assert took < 0.1


def test_sse_sends_keepalive_when_nothing_happens(monkeypatch):
    from meet.assist import web as web_mod

    monkeypatch.setattr(web_mod, "KEEPALIVE_S", 0.05)

    async def scenario():
        state = BusState()
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events") as resp:
                await _read_events(resp, 3)
                raw = await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 5)
                return raw

    assert _run(scenario()) == b": keepalive\n\n"


def test_sse_streams_partial_answers():
    class Partial(BusState):
        def __init__(self):
            super().__init__()
            self.partial_v = 0
            self.parts = []

        def qa_partial_version(self):
            return self.partial_v

        def qa_partials(self):
            return self.parts

    async def scenario():
        state = Partial()
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events") as resp:
                await _read_events(resp, 3)
                state.parts = [{"id": 3, "a": "Предлагаю пере"}]
                state.partial_v = 1
                state.bus.changed.notify()
                return (await _read_events(resp, 1))[0]

    event = _run(scenario())
    assert event[0] == "qa_partial" and event[2] == {"id": 3, "a": "Предлагаю пере"}


def test_reconnected_stream_gets_the_answer_being_written_at_once():
    class Partial(BusState):
        def qa_partial_version(self):
            return 5

        def qa_partials(self):
            return [{"id": 2, "a": "Предлагаю"}]

    async def scenario():
        state = Partial()
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events") as resp:
                return await _read_events(resp, 4)

    events = _run(scenario())
    assert ("qa_partial", None, {"id": 2, "a": "Предлагаю"}) in events


def test_sse_sends_voices_on_connect_and_on_change():
    """Подписи голосов задним числом: состояние при каждом подключении
    (новый ассистент — пустая карта) и при каждой смене, без `id:`."""
    async def scenario():
        state = BusState()
        state.bus.publish("[00:00:03] Собеседник: привет",
                          {"t": 3.0, "speaker": "Собеседник", "text": "привет", "voice": "sys:0"})
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events") as resp:
                first = await _read_events(resp, 4)
                voices = next(e for e in first if e[0] == "voices")
                assert voices[1] is None
                session = state.bus.session
                assert voices[2] == {"rev": 0, "speakers": {}, "hidden": [], "session": session}
                line = next(e for e in first if e[0] == "line")
                assert line[2]["voice"] == "sys:0"
                state.bus.relabel("sys:0", "Демьян")
                again = await _read_events(resp, 1)
                assert again[0][0] == "voices" and again[0][1] is None
                assert again[0][2] == {"rev": 1, "speakers": {"sys:0": "Демьян"}, "hidden": [],
                                       "session": session}
                state.bus.hide([0])
                hidden = await _read_events(resp, 1)
                assert hidden[0][2] == {"rev": 2, "speakers": {"sys:0": "Демьян"}, "hidden": [0],
                                        "session": session}
            # Переподключение после всех строк: состояние голосов — сразу.
            async with client.get("/events", headers={"Last-Event-ID": "0"}) as resp:
                events = await _read_events(resp, 3)
                assert ("voices", None, {"rev": 2, "speakers": {"sys:0": "Демьян"}, "hidden": [0],
                                         "session": state.bus.session}) in events
                assert not [e for e in events if e[0] == "line"]

    _run(scenario())


def test_page_transcript_follows_relabel():
    async def scenario():
        state = BusState()
        state.bus.publish("[00:00:03] Собеседник: привет",
                          {"t": 3.0, "speaker": "Собеседник", "text": "привет", "voice": "sys:0"})
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events?transcript=1") as resp:
                await _read_events(resp, 4)
                state.bus.relabel("sys:0", "Демьян")
                events = await _read_events(resp, 2)
                st = next(e for e in events if e[0] == "state")
                assert st[2]["transcript"] == ["[00:00:03] Демьян: привет"]

    _run(scenario())


def test_page_transcript_drops_hidden_duplicates():
    """Ревью M6: спрятанная строка-дубль не видна и на странице `?transcript=1`."""
    async def scenario():
        state = BusState()
        state.bus.publish("[00:00:01] Вы: своё", {"t": 1.0, "speaker": "Вы", "text": "своё"})
        state.bus.publish("[00:00:02] Собеседник рядом: копия",
                          {"t": 2.0, "speaker": "Собеседник рядом", "text": "копия", "voice": "x/mic:0"})
        state.bus.hide([1])
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events?transcript=1") as resp:
                events = await _read_events(resp, 5)
                st = next(e for e in events if e[0] == "state")
                assert st[2]["transcript"] == ["[00:00:01] Вы: своё"]

    _run(scenario())
