"""Локальный веб-интерфейс live-ассистента: сводка, подсказки, вопросы, лента.

Отдаёт одну HTML-страницу, поток состояния через SSE (`GET /events`),
приём вопросов (`POST /ask`: вопрос или быстрое действие `quick`), действия с подсказками (`POST /hint`:
закрепить, открепить, скрыть), смену задачи-контекста (`POST /task`) и
штатную остановку (`POST /stop` — так резидент гасит дочерний `meet assist`;
`{"detach": true}` — выключить ассистента, подключённого к идущей записи).

SSE шлёт `event: state` (`state.view()`: сводка, подсказки, статус) при
каждом их изменении, `event: qa` (`{"qa": [...]}` — история вопросов) — только
когда меняется она, `event: qa_partial` (`{"id", "a"}` — ответ, который ещё
пишется), `event: line` с `{"t", "speaker", "text", "voice"?}` на каждую новую
строку ленты и `event: voices` (`{"rev", "speakers", "hidden"}`: подписи голосов,
пришедшие задним числом, номера спрятанных строк-дублей и `session` —
метка ассистента, к которой относятся номера) — при каждом
подключении и при смене `rev`; это состояние, а не дельта, и без `id:`. Поток не опрашивает состояние по таймеру: он ждёт сигнала
`state.changes` (`Notifier`) и шлёт изменения сразу; в тишине — комментарий
`: keepalive` раз в KEEPALIVE_S. Хвост ленты строками (`transcript` в `state`) — только по
`/events?transcript=1`, для страницы в браузере: панели он не нужен;
`id:` строки — её номер в шине, поэтому переподключившийся EventSource
(заголовок Last-Event-ID) получает только пропущенные строки.
Потребляет утиный объект состояния (в тестах — FakeState, в бою — AssistState).

**Чат агента-участника** (V4, задача 6; `state.participant` — иначе 409):

- `GET /chat[?limit=N]` → `{"messages", "seq", "agent", "partial"}` — лента
  (`ChatLog.snapshot(feed=True)`, только `visible_in_feed`);
- `POST /chat` `{"text", "attachments": ["a3"], "client_id"}` → 202 `{"id",
  "queued", "attachments", "duplicate"?}`; повтор того же `client_id` — то же
  сообщение. Вложения здесь — только id записей журнала;
- `POST /chat/paste` — сырое тело картинки (≤ 10 МБ), имя — `X-File-Name`
  (URL-кодировано) → `{"id", "status", "attachment"}`;
- `POST /chat/attach` `{"path"}` — файл или папка с диска, только от
  доверенного вызывающего (`_trusted`: заголовок `X-Meet-Token` с токеном,
  который резидент дал ребёнку в окружении; без токена — только запрос без
  Origin, то есть не страница браузера) → как `/chat/paste`;
- `POST /chat/{id}/click` `{"label", "client_id"?}` → `{"ok", "id"}`;
- `POST /chat/{id}/react` `{"emoji", "on"?}` → `{"ok", "changed"}`;
- `POST /chat/stop` `{"id"?}` → `{"ok"}`;
- `PUT /agent/frequency` `{"frequency": "less|normal|more|реже|обычно|чаще",
  "persist"?}` → `{"frequency", "label", "live", "saved"}`: агенту — сразу,
  в настройки (`assist.frequency`) — ключом, если не `persist: false`
  (резидент сохраняет сам).

SSE для чата (без `id:`): `chat_snapshot` (`GET /chat` — при подключении и
если поток отстал от буфера `ChatFeed`), `chat` (событие журнала `{"seq",
"op":"add","message"}` | `{"seq","op":"patch","id","set"}`; `add` скрытых
записей не шлётся), `chat_partial` (`{"id","text"}`), `agent` (`view()`
агента).
"""

import asyncio
import collections
import hmac
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote

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
HINT_ACTIONS = ("pin", "unpin", "dismiss", "restore")
# Чат агента-участника.
CHAT_SNAPSHOT_LIMIT = 200
CHAT_TEXT_MAX = 8000
CHAT_ATTACHMENTS_MAX = 10
CLIENT_ID_MAX = 100
LABEL_MAX = 200
PATH_MAX = 1024
PASTE_MAX_BYTES = 10 * 1024 * 1024
ATTACH_TIMEOUT_S = 90.0
PASTE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff")
# Токен доверенного вызывающего (`/chat/attach`): резидент кладёт его ребёнку
# в окружение (live_control), ребёнок забирает и убирает оттуда (cli).
TOKEN_HEADER = "X-Meet-Token"
_MESSAGE_ID = re.compile(r"^m\d{1,9}$")
_ATTACHMENT_ID = re.compile(r"^a\d{1,9}$")
KEEPALIVE_S = 15.0
# Состояние без сигнала изменений (старый утиный объект) — опрос раз в секунду.
POLL_S = 1.0


def _signal(state):
    """Сигнал изменений состояния: `state.changes`, иначе `state.bus.changed`."""
    return getattr(state, "changes", None) or getattr(state.bus, "changed", None)


KEEPALIVE = b": keepalive\n\n"


def _event(name: str, data, event_id: int | None = None) -> bytes:
    """Одно событие SSE: имя, необязательный id, данные JSON одной строкой."""
    head = f"event: {name}\n" + (f"id: {event_id}\n" if event_id is not None else "")
    return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


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
    """POST/PUT — только со своей страницы или без Origin (резидент, curl):
    чужая вкладка браузера не может ни остановить запись, ни задавать вопросы,
    ни писать в чат."""
    origin = request.headers.get("Origin")
    if (request.method in ("POST", "PUT", "PATCH", "DELETE") and origin is not None
            and origin not in _own_origins(request)):
        raise web.HTTPForbidden(text="чужой Origin")
    return await handler(request)


def _trusted(request, state) -> bool:
    """Доверенный вызывающий (резидент): заголовок с токеном, который он дал
    ребёнку (`state.control_token`). Без токена (`meet assist` из консоли) —
    запрос без Origin: страница браузера (даже своя) путь к файлу не пришлёт,
    она отдаёт байты (`/chat/paste`)."""
    token = getattr(state, "control_token", None)
    if token:
        given = request.headers.get(TOKEN_HEADER) or ""
        return hmac.compare_digest(given.encode("utf-8"), str(token).encode("utf-8"))
    return request.headers.get("Origin") is None


def check_attach_path(raw) -> Path:
    """Путь вложения от доверенного вызывающего: строка, абсолютный, не сетевой
    и не путь устройства (UNC-путь `//server/share` или путь `//?/…` —
    Windows сходил бы за ним в сеть), существующий файл или папка. Иначе
    ValueError с текстом."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("путь — непустая строка")
    if len(raw) > PATH_MAX or "\x00" in raw:
        raise ValueError("негодный путь")
    text = raw.strip()
    if text.startswith(("\\\\", "//")):
        raise ValueError("сетевые пути и пути устройств не принимаются")
    path = Path(text)
    if not path.is_absolute():
        raise ValueError("нужен полный путь")
    try:
        is_file, is_dir = path.is_file(), path.is_dir()
    except OSError:
        is_file = is_dir = False
    if not (is_file or is_dir):
        raise ValueError(f"нет такого файла или папки: {path.name or text}")
    return path


class ChatFeed:
    """События чата для SSE: слушатель агента-участника складывает их сюда,
    поток каждого клиента идёт по номеру. Всё — в цикле событий ребёнка
    (агент шлёт события оттуда же). `add` записей, которых нет в ленте
    (`visible_in_feed`), не копится: окно их не показывает."""

    MAX = 2000

    def __init__(self, participant=None, *, notify=None) -> None:
        self._events: collections.deque = collections.deque(maxlen=self.MAX)
        self.count = 0
        self._notify = notify
        if participant is not None:
            participant.add_listener(self.push)

    def push(self, name: str, data) -> None:
        if name not in ("chat", "chat_partial", "agent"):
            return
        if name == "chat" and isinstance(data, dict) and data.get("op") == "add":
            from meet.assist.chatlog import visible_in_feed

            if not visible_in_feed(data.get("message")):
                return
        self.count += 1
        self._events.append((self.count, name, data))
        if self._notify is not None:
            self._notify()

    def since(self, cursor: int) -> list | None:
        """События после `cursor` — `(номер, имя, данные)`; None — поток
        отстал больше буфера (нужен новый снимок)."""
        if cursor >= self.count:
            return []
        oldest = self._events[0][0] if self._events else self.count + 1
        if cursor + 1 < oldest:
            return None
        return [event for event in self._events if event[0] > cursor]


def _participant(state):
    """Агент-участник ассистента; выключен — 409 (чата нет)."""
    participant = getattr(state, "participant", None)
    if participant is None:
        raise web.HTTPConflict(text="Чат ассистента выключен: агент-участник не запущен")
    return participant


async def _chat_snapshot(state) -> dict | None:
    """Тело `chat_snapshot` / `GET /chat`; журнал не прочитался — None."""
    participant = getattr(state, "participant", None)
    if participant is None:
        return None
    try:
        snap = await participant.snapshot(CHAT_SNAPSHOT_LIMIT)
    except (OSError, RuntimeError):
        return None
    return {**snap["chat"], "agent": snap["agent"], "partial": snap["partial"]}


async def _guarded(coro):
    """Вызов агента из маршрута: ошибка вызывающего — 400, журнал занят — 503,
    ассистент уже остановлен (поток журнала закрыт) — 409."""
    from meet.assist.chatlog import FileLockTimeout

    try:
        return await coro
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e) or "негодный запрос")
    except FileLockTimeout:
        raise web.HTTPServiceUnavailable(text="Журнал чата занят — повторите")
    except RuntimeError as e:
        raise web.HTTPConflict(text=f"Ассистент останавливается ({e})")
    except asyncio.TimeoutError:
        raise                      # срок решает маршрут (TimeoutError — тоже OSError)
    except OSError as e:
        raise web.HTTPServiceUnavailable(text=f"Журнал чата не записан: {e}")


async def _json_body(request) -> dict:
    try:
        body = await request.json() if request.can_read_body else {}
    except Exception:
        raise web.HTTPBadRequest(text="ожидается JSON (UTF-8)")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="ожидается JSON-объект")
    return body


def _client_id(body: dict) -> str | None:
    value = body.get("client_id")
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > CLIENT_ID_MAX:
        raise web.HTTPBadRequest(text=f"client_id — строка до {CLIENT_ID_MAX} символов")
    return value


def _message_id(request) -> str:
    mid = request.match_info["mid"]
    if not _MESSAGE_ID.match(mid):
        raise web.HTTPBadRequest(text="неизвестное сообщение")
    return mid


def _attachment_reply(record: dict) -> dict:
    out = {"id": record.get("id"), "status": record.get("status"), "attachment": record}
    if record.get("error"):
        out["error"] = record["error"]
    return out


def _file_name(header: str | None) -> str | None:
    """Имя вставленной картинки из `X-File-Name` (URL-кодировано): только имя."""
    name = unquote(header or "").strip().replace(chr(92), "/")
    return os.path.basename(name)[:200] or None


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
        sent_partial = None
        with_transcript = request.query.get("transcript") == "1"
        cursor = _first_line_index(request, state.bus.size())
        signal = _signal(state)
        voices_of = getattr(state.bus, "voices", None)
        sent_voices = None  # при подключении — всегда (новый ассистент: карта пуста)
        chat_cursor = None  # чат агента: при подключении — снимок `chat_snapshot`
        # Клиент закрыл вкладку → ConnectionResetError (в т.ч. наследник
        # aiohttp.ClientConnectionResetError). Тихо завершаем хендлер без
        # traceback'а. CancelledError не глотаем — это штатная отмена задачи.
        try:
            while not _stop_requested(state):
                seen = signal.seq if signal is not None else 0
                wrote = False
                snapshot = state.signature()
                if with_transcript:
                    snapshot = (*snapshot, state.bus.size(),
                                voices_of()[0] if voices_of is not None else 0)
                if snapshot != sent:
                    view = state.view()
                    if with_transcript:
                        start = max(0, state.bus.size() - TRANSCRIPT_TAIL)
                        visible = getattr(state.bus, "visible_since", None)
                        lines = visible(start) if visible is not None else state.bus.since(start)[0]
                        view = {**view, "transcript": lines}
                    await resp.write(_event("state", view))
                    sent = snapshot
                    wrote = True
                qa_version = state.qa_version()
                if qa_version != sent_qa:
                    await resp.write(_event("qa", {"qa": state.qa_items()}))
                    sent_qa = qa_version
                    wrote = True
                partial_version = getattr(state, "qa_partial_version", lambda: 0)()
                if partial_version != sent_partial:
                    # Переподключились посреди ответа — сразу его текст на сейчас.
                    for part in state.qa_partials() if hasattr(state, "qa_partials") else ():
                        if sent_partial is not None or part.get("a"):
                            await resp.write(_event("qa_partial", part))
                            wrote = True
                    sent_partial = partial_version
                if voices_of is not None:
                    rev, speakers, hidden = voices_of()
                    if rev != sent_voices:
                        await resp.write(_event("voices", {
                            "rev": rev, "speakers": speakers, "hidden": hidden,
                            "session": getattr(state.bus, "session", None)}))
                        sent_voices = rev
                        wrote = True
                feed = getattr(state, "chat_feed", None)
                if feed is not None:
                    pending = None if chat_cursor is None else feed.since(chat_cursor)
                    if pending is None:
                        # Подключились (или отстали от буфера) — лента целиком;
                        # события после номера ниже придут ещё раз, окно
                        # отбрасывает `seq` не новее снимка.
                        chat_cursor = feed.count
                        snap = await _chat_snapshot(state)
                        if snap is not None:
                            await resp.write(_event("chat_snapshot", snap))
                            wrote = True
                    else:
                        for n, name, data in pending:
                            await resp.write(_event(name, data))
                            chat_cursor = n
                            wrote = True
                entries, size = state.bus.entries_since(cursor)
                for i, entry in enumerate(entries, start=cursor):
                    await resp.write(_event("line", entry, event_id=i))
                    wrote = True
                cursor = size
                if signal is None:
                    await asyncio.sleep(POLL_S)
                    continue
                if await signal.wait(seen, KEEPALIVE_S) == seen and not wrote:
                    await resp.write(KEEPALIVE)
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
        if getattr(state, "ready", True) is False:
            # Звук уже пишется, а модель распознавания ещё грузится: отвечать не по чему.
            raise web.HTTPConflict(text="Ассистент ещё запускается — спросите через несколько секунд")
        if getattr(state, "qa", True) is None:
            # Агент-участник (`assist.participant`): вопросы — сообщениями в его
            # чат (маршруты чата — задача 6), прежнего «Спросить» нет.
            raise web.HTTPConflict(text="Вопросы теперь — сообщениями ассистенту в чате")
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
            raise web.HTTPBadRequest(text="ожидается id и action: pin, unpin, dismiss или restore")
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
        # ответа, иначе клиент ждал бы финальную расшифровку. `{"detach":
        # true}` — ассистента, подключённого к записи, выключают, а запись
        # идёт дальше: сводка помечается неполной.
        try:
            body = await request.json() if request.can_read_body else {}
        except Exception:
            body = {}
        detach = isinstance(body, dict) and body.get("detach") is True
        if detach and hasattr(state, "mark_detached"):
            state.mark_detached()
        state.request_stop()
        return _json_response({"ok": True})

    # --- чат агента-участника ---

    async def chat_get(request):
        participant = _participant(state)
        try:
            limit = int(request.query.get("limit", CHAT_SNAPSHOT_LIMIT))
        except ValueError:
            raise web.HTTPBadRequest(text="limit — число")
        snap = await _guarded(participant.snapshot(max(0, min(limit, 5000))))
        return _json_response({**snap["chat"], "agent": snap["agent"], "partial": snap["partial"]})

    async def chat_post(request):
        participant = _participant(state)
        body = await _json_body(request)
        text = body.get("text", "")
        if not isinstance(text, str):
            raise web.HTTPBadRequest(text="text — строка")
        text = text.strip()
        if len(text) > CHAT_TEXT_MAX:
            raise web.HTTPBadRequest(text=f"сообщение длиннее {CHAT_TEXT_MAX} символов")
        refs = body.get("attachments") or []
        if (not isinstance(refs, list) or len(refs) > CHAT_ATTACHMENTS_MAX
                or not all(isinstance(r, str) and _ATTACHMENT_ID.match(r) for r in refs)):
            # Пути сюда не принимаются: файл — через /chat/attach (только
            # доверенный вызывающий), картинка — через /chat/paste.
            raise web.HTTPBadRequest(
                text=f"attachments — до {CHAT_ATTACHMENTS_MAX} id вложений («a3»)")
        if not text and not refs:
            raise web.HTTPBadRequest(text="пустое сообщение")
        result = await _guarded(participant.post_user_message(
            text, list(dict.fromkeys(refs)), client_id=_client_id(body)))
        return web.json_response(result, status=202,
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False))

    async def chat_paste(request):
        participant = _participant(state)
        too_big = "Изображение больше 10 МБ"
        if request.content_length is not None and request.content_length > PASTE_MAX_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=PASTE_MAX_BYTES,
                                                actual_size=request.content_length, text=too_big)
        kind = (request.content_type or "").lower()
        if kind not in PASTE_TYPES:
            raise web.HTTPUnsupportedMediaType(
                text="нужна картинка: PNG, JPEG, WEBP, GIF, BMP или TIFF")
        data = bytearray()
        async for chunk in request.content.iter_chunked(1 << 16):
            data += chunk
            if len(data) > PASTE_MAX_BYTES:
                raise web.HTTPRequestEntityTooLarge(max_size=PASTE_MAX_BYTES,
                                                    actual_size=len(data), text=too_big)
        if not data:
            raise web.HTTPBadRequest(text="пустое тело")
        name = _file_name(request.headers.get("X-File-Name"))
        record = await _guarded(participant.attach({"data": bytes(data), "name": name}))
        return _json_response(_attachment_reply(record))

    async def chat_attach(request):
        participant = _participant(state)
        if not _trusted(request, state):
            raise web.HTTPForbidden(text="путь к файлу принимается только от приложения")
        body = await _json_body(request)
        try:
            path = check_attach_path(body.get("path"))
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e))
        try:
            record = await _guarded(asyncio.wait_for(participant.attach(str(path)),
                                                     ATTACH_TIMEOUT_S))
        except asyncio.TimeoutError:
            raise web.HTTPGatewayTimeout(text=f"Файл не разобран за {ATTACH_TIMEOUT_S:.0f} с")
        return _json_response(_attachment_reply(record))

    async def chat_click(request):
        participant = _participant(state)
        mid = _message_id(request)
        body = await _json_body(request)
        label = body.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > LABEL_MAX:
            raise web.HTTPBadRequest(text="label — надпись кнопки")
        message = await _guarded(participant.click(mid, label, client_id=_client_id(body)))
        return _json_response({"ok": True, "id": message.get("id")})

    async def chat_react(request):
        participant = _participant(state)
        mid = _message_id(request)
        body = await _json_body(request)
        emoji, on = body.get("emoji"), body.get("on")
        if not isinstance(emoji, str):
            raise web.HTTPBadRequest(text="emoji — 👍, 👎 или ❓")
        if on is not None and not isinstance(on, bool):
            raise web.HTTPBadRequest(text="on — true, false или null")
        changes = await _guarded(participant.react(mid, emoji, on))
        return _json_response({"ok": True, "changed": bool(changes)})

    async def chat_stop(request):
        participant = _participant(state)
        body = await _json_body(request)
        mid = body.get("id")
        if mid is not None and (not isinstance(mid, str) or not _MESSAGE_ID.match(mid)):
            raise web.HTTPBadRequest(text="неизвестное сообщение")
        stopped = await _guarded(participant.stop_reply(mid))
        return _json_response({"ok": bool(stopped)})

    async def agent_frequency(request):
        from meet.settings import FREQUENCY_LABELS, frequency_key

        body = await _json_body(request)
        key = frequency_key(body.get("frequency"))
        if key is None:
            raise web.HTTPBadRequest(text="frequency — less, normal или more (реже, обычно, чаще)")
        persist = body.get("persist", True)
        if not isinstance(persist, bool):
            raise web.HTTPBadRequest(text="persist — true или false")
        participant = getattr(state, "participant", None)
        if participant is not None:
            participant.set_frequency(FREQUENCY_LABELS[key])
        saved = False
        save = getattr(state, "persist_frequency", None)
        if persist and save is not None:
            try:
                await asyncio.to_thread(save, key)
                saved = True
            except (OSError, ValueError) as e:
                raise web.HTTPServiceUnavailable(text=f"Настройка не сохранена: {e}")
        return _json_response({"frequency": key, "label": FREQUENCY_LABELS[key],
                               "live": participant is not None, "saved": saved})

    # Картинка до 10 МБ в теле `/chat/paste` (по умолчанию aiohttp — 1 МБ).
    app = web.Application(middlewares=[_same_origin_posts],
                          client_max_size=PASTE_MAX_BYTES + 64 * 1024)
    app.add_routes([
        web.get("/", index),
        web.get("/events", events),
        web.post("/ask", ask),
        web.post("/hint", hint),
        web.post("/task", set_task),
        web.post("/stop", stop),
        web.get("/chat", chat_get),
        web.post("/chat", chat_post),
        web.post("/chat/paste", chat_paste),
        web.post("/chat/attach", chat_attach),
        web.post("/chat/stop", chat_stop),
        web.post("/chat/{mid}/click", chat_click),
        web.post("/chat/{mid}/react", chat_react),
        web.put("/agent/frequency", agent_frequency),
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
