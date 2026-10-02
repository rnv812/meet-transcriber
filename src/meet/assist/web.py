"""Локальный веб-интерфейс live-ассистента: сводка, подсказки, вопросы, лента.

Отдаёт одну HTML-страницу, поток состояния через SSE (`GET /events`),
приём вопросов (`POST /ask`: вопрос или быстрое действие `quick`), действия с подсказками (`POST /hint`:
закрепить, открепить, скрыть), смену задачи-контекста (`POST /task`) и
штатную остановку (`POST /stop` — так резидент гасит дочерний `meet assist`).

SSE шлёт `event: state` (`state.view()`: сводка, подсказки, статус) при
каждом их изменении, `event: qa` (`{"qa": [...]}` — история вопросов) — только
когда меняется она, и `event: line` с `{"t", "speaker", "text"}` на каждую
новую строку ленты. Хвост ленты строками (`transcript` в `state`) — только по
`/events?transcript=1`, для страницы в браузере: панели он не нужен;
`id:` строки — её номер в шине, поэтому переподключившийся EventSource
(заголовок Last-Event-ID) получает только пропущенные строки.
Потребляет утиный объект состояния (в тестах — FakeState, в бою — AssistState).
"""

import asyncio
import json

from aiohttp import web

from meet.assist.qa import QUICK

PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>meet assist</title>
<style>
body{font-family:Segoe UI,sans-serif;margin:0;display:grid;
     grid-template-columns:1fr 1fr;grid-template-rows:auto 1fr auto;height:100vh}
h1{grid-column:1/3;margin:8px 16px;font-size:18px}
#status{color:#b00;margin-left:12px;font-size:13px}
#digest,#chat{overflow:auto;padding:0 16px 16px;white-space:pre-wrap}
#chat .q{color:#246;font-weight:600;margin-top:10px}
#chat .a{margin:4px 0 0 12px}
#bar{grid-column:1/3;display:flex;gap:8px;padding:8px 16px;border-top:1px solid #ccc}
#q{flex:1;padding:6px}
#transcript{grid-column:1/3;max-height:22vh;overflow:auto;padding:4px 16px;
            color:#666;font-size:12px;border-top:1px solid #eee;white-space:pre-wrap}
</style></head><body>
<h1>meet assist <span id="status"></span></h1>
<div id="digest">…</div><div id="chat"></div>
<div id="bar">
  <input id="task" placeholder="задача (контекст)" size="18">
  <button onclick="setTask()">задать</button>
  <input id="q" placeholder="вопрос по встрече">
  <button onclick="ask()">спросить</button>
  <button onclick="recap()">Что я пропустил?</button>
</div>
<div id="transcript"></div>
<script>
const es = new EventSource('/events?transcript=1');
es.addEventListener('state', e => {
  const s = JSON.parse(e.data);
  const hints = (s.hints || []).map(h => '• ' + h.text).join('\\n');
  document.getElementById('digest').textContent =
    s.digest + (hints ? '\\n\\nПодсказки:\\n' + hints : '');
  document.getElementById('transcript').textContent = (s.transcript || []).join('\\n');
  document.getElementById('status').textContent = s.status || '';
});
async function send(question, quick){
  const chat = document.getElementById('chat');
  chat.insertAdjacentHTML('beforeend', `<div class="q"></div>`);
  chat.lastChild.textContent = question;
  const r = await fetch('/ask', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(quick ? {quick} : {question})});
  const a = document.createElement('div'); a.className = 'a';
  a.textContent = (await r.json()).answer;
  chat.appendChild(a); chat.scrollTop = chat.scrollHeight;
}
function ask(){const q=document.getElementById('q');
  if(q.value.trim()){send(q.value.trim()); q.value='';}}
function recap(){send('Что я пропустил?', 'missed');}
async function setTask(){const t=document.getElementById('task');
  if(t.value.trim()) await fetch('/task', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({task:t.value.trim()})});}
document.getElementById('q').addEventListener('keydown',
  e => {if(e.key==='Enter') ask();});
</script></body></html>"""

TRANSCRIPT_TAIL = 50
HINT_ACTIONS = ("pin", "unpin", "dismiss")


def _stop_requested(state) -> bool:
    event = getattr(state, "stop_event", None)
    return event is not None and event.is_set()


def _first_line_index(request, size: int) -> int:
    """С какой строки слать `line`: после Last-Event-ID, иначе — хвост ленты."""
    try:
        return min(size, max(0, int(request.headers["Last-Event-ID"]) + 1))
    except (KeyError, ValueError):
        return max(0, size - TRANSCRIPT_TAIL)


def _own_origins(request) -> set[str]:
    sock = request.transport.get_extra_info("sockname") if request.transport else None
    port = sock[1] if sock else request.url.port
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


@web.middleware
async def _same_origin_posts(request, handler):
    """POST — только со своей страницы или без Origin (резидент, curl): чужая
    вкладка браузера не может ни остановить запись, ни задавать вопросы."""
    origin = request.headers.get("Origin")
    if (request.method == "POST" and origin is not None
            and origin not in _own_origins(request)):
        raise web.HTTPForbidden(text="чужой Origin")
    return await handler(request)


def build_app(state) -> web.Application:
    async def index(request):
        return web.Response(text=PAGE, content_type="text/html")

    async def events(request):
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
        })
        await resp.prepare(request)
        sent: tuple | None = None
        sent_qa = None
        with_transcript = request.query.get("transcript") == "1"
        cursor = _first_line_index(request, state.bus.size())
        # Клиент закрыл вкладку → ConnectionResetError (в т.ч. наследник
        # aiohttp.ClientConnectionResetError). Тихо завершаем хендлер без
        # traceback'а. CancelledError не глотаем — это штатная отмена задачи.
        try:
            while not _stop_requested(state):
                snapshot = state.signature()
                if with_transcript:
                    snapshot = (*snapshot, state.bus.size())
                if snapshot != sent:
                    view = state.view()
                    if with_transcript:
                        lines, _ = state.bus.since(
                            max(0, state.bus.size() - TRANSCRIPT_TAIL))
                        view = {**view, "transcript": lines}
                    payload = json.dumps(view, ensure_ascii=False)
                    await resp.write(
                        f"event: state\ndata: {payload}\n\n".encode())
                    sent = snapshot
                qa_version = state.qa_version()
                if qa_version != sent_qa:
                    payload = json.dumps({"qa": state.qa_items()}, ensure_ascii=False)
                    await resp.write(f"event: qa\ndata: {payload}\n\n".encode())
                    sent_qa = qa_version
                entries, size = state.bus.entries_since(cursor)
                for i, entry in enumerate(entries, start=cursor):
                    data = json.dumps(entry, ensure_ascii=False)
                    await resp.write(
                        f"event: line\nid: {i}\ndata: {data}\n\n".encode())
                cursor = size
                await asyncio.sleep(1.0)
        except ConnectionResetError:
            pass
        return resp

    def _json_response(data):
        # ensure_ascii=False — не экранировать кириллицу в JSON-ответе.
        return web.json_response(
            data, dumps=lambda o: json.dumps(o, ensure_ascii=False))

    async def ask(request):
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="ожидается JSON (UTF-8)")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="ожидается JSON-объект")
        question = body.get("question") or ""
        quick = body.get("quick")
        since = body.get("since_t")
        if not isinstance(question, str):
            raise web.HTTPBadRequest(text="вопрос должен быть строкой")
        question = question.strip()
        if quick is not None and quick not in QUICK:
            raise web.HTTPBadRequest(text="неизвестное быстрое действие")
        if quick is None and not question:
            raise web.HTTPBadRequest(text="пустой вопрос")
        if since is not None and (isinstance(since, bool) or not isinstance(since, (int, float))
                                  or since < 0):
            raise web.HTTPBadRequest(text="since_t — секунды от начала записи")
        # QA-раннер может пробросить исключение (ревью Task 9) —
        # не роняем хендлер, а возвращаем ошибку текстом ответа.
        try:
            answer = await state.qa.ask(question or None, quick=quick, since_t=since)
        except Exception as e:
            return _json_response({"answer": f"⚠ внутренняя ошибка: {e}"})
        return _json_response({"answer": answer})

    async def hint(request):
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="ожидается JSON (UTF-8)")
        hint_id = body.get("id") if isinstance(body, dict) else None
        action = body.get("action") if isinstance(body, dict) else None
        if not isinstance(hint_id, str) or action not in HINT_ACTIONS:
            raise web.HTTPBadRequest(text="ожидается id и action: pin, unpin или dismiss")
        changed = state.hint_action(hint_id, action)
        return _json_response({"ok": True, "changed": changed})

    async def set_task(request):
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="ожидается JSON (UTF-8)")
        task = body.get("task", "").strip()
        if task:
            await state.set_task(task)
        raise web.HTTPNoContent()

    async def stop(request):
        # Только просим остановиться: дорожки дописывает run_assist уже после
        # ответа, иначе клиент ждал бы финальную расшифровку.
        state.request_stop()
        return _json_response({"ok": True})

    app = web.Application(middlewares=[_same_origin_posts])
    app.add_routes([
        web.get("/", index),
        web.get("/events", events),
        web.post("/ask", ask),
        web.post("/hint", hint),
        web.post("/task", set_task),
        web.post("/stop", stop),
    ])
    return app


def bound_port(runner: web.AppRunner) -> int:
    """Фактический порт сервера (при `port=0` его выбирает система)."""
    return runner.addresses[0][1]


async def run_web(state, port: int) -> web.AppRunner:
    # shutdown_timeout: открытый SSE-поток не должен держать выход процесса
    # минуту (дефолт aiohttp) — поток и так закрывается по stop_event.
    runner = web.AppRunner(build_app(state), shutdown_timeout=5.0)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner
