"""Страница `meet assist` в браузере (V4, задача 9): при агенте-участнике
`GET /` отдаёт чат (`CHAT_PAGE`) на маршрутах `/chat*` и событиях чата, а не
прежнюю страницу с `/ask` (тот при агенте отвечает 409). Проверяется и то, что
запросы страницы проходят проверки ребёнка (Host, Origin) у консольного
`meet assist` без токена, а путь к файлу (`/chat/attach`) — нет."""

import asyncio
import re
import shutil
import subprocess

import pytest
from aiohttp.test_utils import TestClient, TestServer

from meet.assist.web import CHAT_PAGE, PAGE, build_app
from test_chat_api import ChatState, _png, _until


def _run(coro):
    return asyncio.run(coro)


def _script(page: str) -> str:
    return page.split("<script>", 1)[1].split("</script>", 1)[0]


# Крючки, на которых держится страница чата: маршруты и события задачи 6.
CHAT_HOOKS = (
    'new EventSource("/events?transcript=1")',
    '"chat_snapshot"', '"chat"', '"chat_partial"', '"agent"',
    '"/chat"', '"/chat/paste"', '"/chat/stop"', '"/agent/frequency"',
    '"/click"', '"/react"', '"/chat/attachments/"',
    'e.key === "Enter" && !e.shiftKey',
    'id="feed"', 'id="text"', 'id="send"', 'id="stop"', 'id="frequency"',
)


def test_index_serves_the_chat_page_with_the_participant(tmp_path):
    async def scenario():
        state = ChatState(tmp_path)
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.get("/")
            assert r.status == 200 and "text/html" in r.headers["Content-Type"]
            html = await r.text()
            assert html == CHAT_PAGE
            for hook in CHAT_HOOKS:
                assert hook in html, hook
            # Ни «Спросить» (409 при агенте), ни пути к файлу (только приложение с токеном).
            assert "/ask" not in html and "/chat/attach\"" not in html

    _run(scenario())


def test_index_keeps_the_old_page_without_the_participant(tmp_path):
    """`assist.participant=false` (или «Только сводка») — прежняя страница."""
    async def scenario():
        state = ChatState(tmp_path, participant=False)
        async with TestClient(TestServer(build_app(state))) as client:
            html = await (await client.get("/")).text()
            assert html == PAGE and "/ask" in html and "chat_snapshot" not in html

    _run(scenario())


def test_chat_page_passes_the_host_check_only_on_its_own_address(tmp_path):
    async def scenario():
        state = ChatState(tmp_path)
        async with TestClient(TestServer(build_app(state))) as client:
            port = client.server.port
            for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
                r = await client.get("/", headers={"Host": host})
                assert r.status == 200 and "chat_snapshot" in await r.text()
            for host in (f"evil.example:{port}", f"127.0.0.1:{port + 1}", "localhost"):
                assert (await client.get("/", headers={"Host": host})).status == 403

    _run(scenario())


def test_page_requests_work_from_its_own_origin_without_a_token(tmp_path):
    """Консольный `meet assist`: токена нет. Всё, что шлёт страница (со своим
    Origin и Host), принимается; `/chat/attach` — 403, страница его и не зовёт."""
    async def scenario():
        state = ChatState(tmp_path, token=None)
        agent = state.chat.append("agent", status="shown", text="Глянуть план?",
                                  buttons=["Глянь", "Не надо"]).message
        async with TestClient(TestServer(build_app(state))) as client:
            port = client.server.port
            for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                host = origin.split("//", 1)[1]
                page = {"Origin": origin, "Host": host}
                r = await client.post("/chat/paste", data=_png(), headers={
                    **page, "Content-Type": "image/png", "X-File-Name": "%D1%81%D0%BA%D1%80%D0%B8%D0%BD.png"})
                assert r.status == 200, await r.text()
                aid = (await r.json())["id"]
                r = await client.post("/chat", json={"text": "вот", "attachments": [aid],
                                                     "client_id": f"web-{port}-{host}"}, headers=page)
                assert r.status == 202, await r.text()
                r = await client.post(f"/chat/{agent['id']}/react",
                                      json={"emoji": "👍", "on": True}, headers=page)
                assert r.status == 200
                r = await client.post("/chat/stop", json={}, headers=page)
                assert r.status == 200
                r = await client.put("/agent/frequency", json={"frequency": "обычно"}, headers=page)
                assert r.status == 200 and (await r.json())["label"] == "обычно"
                r = await client.post("/chat/attach", json={"path": str(tmp_path)}, headers=page)
                assert r.status == 403
            r = await client.post(f"/chat/{agent['id']}/click", json={"label": "Глянь"},
                                  headers={"Origin": f"http://127.0.0.1:{port}"})
            assert r.status == 200
            # Чужая вкладка — нет.
            r = await client.post("/chat", json={"text": "x"}, headers={"Origin": "http://evil.example"})
            assert r.status == 403
        texts = [m.get("text") for m in state.chat.messages() if m["kind"] == "user"]
        assert texts == ["вот", "вот", "Глянь"]

    _run(scenario())


def test_page_event_stream_carries_transcript_and_chat(tmp_path):
    """Страница подписана на `/events?transcript=1`: и хвост расшифровки
    (`state.transcript`), и снимок чата."""
    async def scenario():
        state = ChatState(tmp_path)
        state.bus.publish("[00:00:03] Демьян: привет", {"t": 3.0, "speaker": "Демьян", "text": "привет"})
        state.chat.append("user", text="раньше")
        async with TestClient(TestServer(build_app(state))) as client:
            async with client.get("/events?transcript=1") as resp:
                st, _ = await _until(resp, "state")
                assert st["transcript"] == ["[00:00:03] Демьян: привет"]
                assert st["agent"]["provider"] == "codex"
                snap, _ = await _until(resp, "chat_snapshot")
                assert [m["text"] for m in snap["messages"]] == ["раньше"]

    _run(scenario())


def test_chat_page_never_inserts_html():
    """Текст агента и пользователя — только textContent / текстовые узлы."""
    script = _script(CHAT_PAGE)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in script, sink
    assert "textContent" in script and "createTextNode" in script


def test_chat_page_script_string_literals_have_no_raw_newlines():
    script = _script(CHAT_PAGE)
    for m in re.finditer(r"'[^']*'|\"[^\"]*\"", script):
        assert "\n" not in m.group(0), m.group(0)


def test_chat_page_script_parses_with_node(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не найден")
    path = tmp_path / "chat.js"
    path.write_text(_script(CHAT_PAGE), encoding="utf-8")
    run = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


RENDER_HARNESS = r"""
// Мини-DOM: ровно то, чем пользуются inline() и renderText() страницы.
class Node_ { constructor(tag) { this.tagName = tag; this.children = []; this.text = ""; }
  appendChild(c) { this.children.push(c); return c; }
  set textContent(v) { this.text = String(v); this.children = []; }
  html() { const inner = this.text.replace(/&/g, "&amp;").replace(/</g, "&lt;")
             + this.children.map((c) => c.html()).join("");
           return this.tagName === "#text" ? inner : "<" + this.tagName + ">" + inner + "</" + this.tagName + ">"; } }
const document = { createElement: (t) => new Node_(t),
  createTextNode: (s) => { const n = new Node_("#text"); n.text = String(s); return n; } };
const SRC = require("fs").readFileSync(process.argv[2], "utf8");
const inlineSrc = SRC.slice(SRC.indexOf("function inline("), SRC.indexOf("// --- лента"));
eval(inlineSrc);
const root = new Node_("div");
renderText(root, process.argv[3]);
process.stdout.write(root.html());
"""


def test_agent_text_renders_bold_and_lists_and_escapes_html(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node не найден")
    page = tmp_path / "chat.js"
    page.write_text(_script(CHAT_PAGE), encoding="utf-8")
    harness = tmp_path / "render.js"
    harness.write_text(RENDER_HARNESS, encoding="utf-8")
    text = ("Срок — **15.11**, а не 01.12\n\n- первый <b>пункт</b>\n- второй\n"
            "1. раз\n2. два\n<img src=x onerror=alert(1)> **незакрыт")
    run = subprocess.run([node, str(harness), str(page), text],
                         capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    assert run.stdout == (
        "<div><p>Срок — <b>15.11</b>, а не 01.12</p>"
        "<ul><li>первый &lt;b>пункт&lt;/b></li><li>второй</li></ul>"
        "<ol><li>раз</li><li>два</li></ol>"
        "<p>&lt;img src=x onerror=alert(1)> **незакрыт</p></div>")


def test_index_cannot_be_framed_and_loads_nothing_from_outside(tmp_path):
    """Обе страницы: не встраиваются в чужую (подсовывание щелчков) и
    ничего не грузят со стороны (ревью S-M1)."""
    async def scenario():
        for participant in (True, False):
            state = ChatState(tmp_path / str(participant), participant=participant)
            async with TestClient(TestServer(build_app(state))) as client:
                r = await client.get("/")
                assert r.headers["X-Frame-Options"] == "DENY"
                csp = r.headers["Content-Security-Policy"]
                assert "frame-ancestors 'none'" in csp and "default-src 'self'" in csp

    _run(scenario())


def test_chat_page_updates_messages_in_place():
    """Ревью I1: лента не пересобирается целиком — узлы по id, текст, который
    пишется, меняется внутри своего пузыря (`aria-busy`, пока пишется);
    М1: повторное нажатие кнопки агента не шлётся; M2: устаревший снимок —
    повтор."""
    script = _script(CHAT_PAGE)
    assert "feed.replaceChildren" not in script
    assert "const nodes = new Map()" in script and "insertBefore" in script
    assert 'setAttribute("aria-busy", "true")' in script
    assert "pendingClicks.has(mid)" in script
    assert "setTimeout(resync" in script


RENDER_FEED_HARNESS = r"""
// Мини-DOM с детьми и insertBefore: хватает для render() страницы.
let made = 0;
class N { constructor(tag) { this.tagName = tag; this.childNodes = []; this.parent = null; this.text = "";
    this.dataset = {}; this.attrs = {}; this.uid = ++made; this.className = ""; this.listeners = {}; }
  get children() { return this.childNodes; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1]; }
  appendChild(c) { return this.insertBefore(c, null); }
  insertBefore(c, ref) { if (c.parent) c.remove(); const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c); c.parent = this; return c; }
  remove() { const p = this.parent; if (p) { p.childNodes.splice(p.childNodes.indexOf(this), 1); this.parent = null; } }
  replaceChildren() { for (const c of [...this.childNodes]) c.remove(); }
  set textContent(v) { this.replaceChildren(); this.text = String(v); }
  get textContent() { return this.text + this.childNodes.map((c) => c.textContent).join(""); }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(k, f) { this.listeners[k] = f; }
  querySelector(sel) { const cls = sel.slice(1);
    for (const c of this.childNodes) { if (c.className.split(" ").includes(cls)) return c;
      const d = c.querySelector(sel); if (d) return d; } return null; } }
const byId = {};
const document = { createElement: (t) => new N(t),
  createTextNode: (s) => { const n = new N("#text"); n.text = String(s); return n; },
  createDocumentFragment: () => new N("#frag"),
  getElementById: (id) => (byId[id] = byId[id] || new N("div")), activeElement: null,
  addEventListener() {}, body: { classList: { add() {}, remove() {} } } };
class EventSource { constructor() { this.l = {}; } addEventListener(k, f) { this.l[k] = f; } }
globalThis.document = document; globalThis.EventSource = EventSource;
globalThis.fetch = async () => ({ ok: true, text: async () => "{}" });
const SRC = require("fs").readFileSync(process.argv[2], "utf8");
// Тело IIFE без вызова: доступ к его функциям и к потоку событий.
const body = SRC.slice(SRC.indexOf("{") + 1, SRC.lastIndexOf("}"));
const api = new Function("document", "EventSource",
  body + "; return {applySnapshot, applyEvent, es, setPartial: (p) => { partial = p; render(); }, feed};");
const page = api(document, EventSource);
const snap = { seq: 3, partial: null, agent: { state: "writing", writing: "m3" }, messages: [
  { id: "m1", kind: "agent", status: "shown", text: "Глянуть план?", buttons: ["Глянь", "Не надо"], reactions: {} },
  { id: "m2", kind: "user", text: "что с бюджетом" },
  { id: "m3", kind: "agent", status: "writing", mode: "reply", text: "" } ] };
page.applySnapshot(snap);
const before = page.feed.childNodes.map((n) => n.uid);
page.setPartial({ id: "m3", text: "Бюджет **120**" });
const mid = page.feed.childNodes.map((n) => n.uid);
page.setPartial({ id: "m3", text: "Бюджет **120** тысяч" });
const after = page.feed.childNodes.map((n) => n.uid);
const bubble = page.feed.childNodes[2];
page.applyEvent({ seq: 4, op: "patch", id: "m3", set: { status: "shown", text: "Бюджет **120** тысяч" } });
const done = page.feed.childNodes.map((n) => n.uid);
process.stdout.write(JSON.stringify({ before, mid, after, done,
  busy: bubble.getAttribute("aria-busy"), text: bubble.querySelector(".body").textContent }));
"""


def test_streaming_text_changes_only_its_bubble(tmp_path):
    """Ревью I1 в деле: кусок ответа не пересоздаёт ни чужие узлы, ни сам
    пузырь (меняется только его текст); готовый ответ — новый узел один раз."""
    import json

    node = shutil.which("node")
    if node is None:
        pytest.skip("node не найден")
    page = tmp_path / "chat.js"
    page.write_text(_script(CHAT_PAGE), encoding="utf-8")
    harness = tmp_path / "feed.js"
    harness.write_text(RENDER_FEED_HARNESS, encoding="utf-8")
    run = subprocess.run([node, str(harness), str(page)], capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert len(out["before"]) == 3
    # первый кусок: «пишет…» → текст — пересоздаётся только пузырь
    assert out["mid"][:2] == out["before"][:2] and out["mid"][2] != out["before"][2]
    # следующие куски — тот же узел, тот же текст внутри обновлён
    assert out["after"] == out["mid"]
    assert out["busy"] == "true" and out["text"] == "Бюджет 120 тысяч"
    # готово — пузырь новый (без aria-busy), остальные на месте
    assert out["done"][:2] == out["before"][:2] and out["done"][2] != out["after"][2]
