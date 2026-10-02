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
        return (1, self.bus.size(), self.status())

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
        @staticmethod
        async def ask(q):
            return f"ответ на: {q}"

    qa = _QA()

    async def set_task(self, name):
        self.tasks_set.append(name)

    def __init__(self):
        self.hint_calls = []


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
                assert payload["transcript"] == ["[00:00:01] Вы: привет"]

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
                events = await _read_events(resp, 2)
                kinds = {e[0] for e in events}
                assert kinds == {"state", "line"}
                line = next(e for e in events if e[0] == "line")
                assert line[1] == "0"
                assert line[2] == {"t": 3.0, "speaker": "Вы", "text": "привет"}
                state.bus.publish("[00:00:09] Собеседник: да",
                                  {"t": 9.5, "speaker": "Собеседник", "text": "да"})
                new = [e for e in await _read_events(resp, 2) if e[0] == "line"]
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
                events = await _read_events(resp, 2)
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
            for bad in ({"id": "h1", "action": "delete"}, {"action": "pin"}, [1]):
                r = await client.post("/hint", json=bad)
                assert r.status == 400
            r = await client.post("/hint", json={"id": "h1", "action": "pin"},
                                  headers={"Origin": "http://evil.example"})
            assert r.status == 403
        assert state.hint_calls == [("h1", "pin"), ("h9", "dismiss")]

    _run(scenario())
