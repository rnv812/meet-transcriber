import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from meet.assist.web import build_app


class FakeState:
    class _D:
        version = 1

        @staticmethod
        def render():
            return "## Тема\n- [1] Тезис"

    class _B:
        @staticmethod
        def since(i):
            return (["[00:00:01] Вы: привет"], 1)

        @staticmethod
        def size():
            return 1

    digest, bus = _D(), _B()
    tasks_set: list[str] = []

    @staticmethod
    def status():
        return None

    class _QA:
        @staticmethod
        async def ask(q):
            return f"ответ на: {q}"

    qa = _QA()

    async def set_task(self, name):
        self.tasks_set.append(name)


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

    _run(scenario())
