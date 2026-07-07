"""Локальный веб-интерфейс live-ассистента: дайджест, чат вопросов, лента транскрипта.

Отдаёт одну HTML-страницу, поток состояния через SSE (`GET /events`),
приём вопросов (`POST /ask`) и смену задачи-контекста (`POST /task`).
Потребляет утиный объект состояния (в тестах — FakeState, в бою — AssistState).
"""

import asyncio
import json

from aiohttp import web

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
const es = new EventSource('/events');
es.addEventListener('state', e => {
  const s = JSON.parse(e.data);
  document.getElementById('digest').textContent = s.digest;
  document.getElementById('transcript').textContent = s.transcript.join('\\n');
  document.getElementById('status').textContent = s.status || '';
});
async function send(question){
  const chat = document.getElementById('chat');
  chat.insertAdjacentHTML('beforeend', `<div class="q"></div>`);
  chat.lastChild.textContent = question;
  const r = await fetch('/ask', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({question})});
  const a = document.createElement('div'); a.className = 'a';
  a.textContent = (await r.json()).answer;
  chat.appendChild(a); chat.scrollTop = chat.scrollHeight;
}
function ask(){const q=document.getElementById('q');
  if(q.value.trim()){send(q.value.trim()); q.value='';}}
function recap(){send('Что я пропустил?');}
async function setTask(){const t=document.getElementById('task');
  if(t.value.trim()) await fetch('/task', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({task:t.value.trim()})});}
document.getElementById('q').addEventListener('keydown',
  e => {if(e.key==='Enter') ask();});
</script></body></html>"""

TRANSCRIPT_TAIL = 50


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
        # Клиент закрыл вкладку → ConnectionResetError (в т.ч. наследник
        # aiohttp.ClientConnectionResetError). Тихо завершаем хендлер без
        # traceback'а. CancelledError не глотаем — это штатная отмена задачи.
        try:
            while True:
                lines, _ = state.bus.since(
                    max(0, state.bus.size() - TRANSCRIPT_TAIL))
                snapshot = (state.digest.version, state.bus.size(),
                            state.status())
                if snapshot != sent:
                    payload = json.dumps({
                        "digest": state.digest.render(),
                        "transcript": lines,
                        "status": state.status(),
                    }, ensure_ascii=False)
                    await resp.write(
                        f"event: state\ndata: {payload}\n\n".encode())
                    sent = snapshot
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
        question = body.get("question", "").strip()
        if not question:
            raise web.HTTPBadRequest(text="пустой вопрос")
        # QA-раннер может пробросить исключение (ревью Task 9) —
        # не роняем хендлер, а возвращаем ошибку текстом ответа.
        try:
            answer = await state.qa.ask(question)
        except Exception as e:
            return _json_response({"answer": f"⚠ внутренняя ошибка: {e}"})
        return _json_response({"answer": answer})

    async def set_task(request):
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="ожидается JSON (UTF-8)")
        task = body.get("task", "").strip()
        if task:
            await state.set_task(task)
        raise web.HTTPNoContent()

    app = web.Application()
    app.add_routes([
        web.get("/", index),
        web.get("/events", events),
        web.post("/ask", ask),
        web.post("/task", set_task),
    ])
    return app


async def run_web(state, port: int) -> web.AppRunner:
    runner = web.AppRunner(build_app(state))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner
