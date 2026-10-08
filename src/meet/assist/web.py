"""Локальный веб-интерфейс live-ассистента: чат с агентом-участником или
(прежний режим) сводка, подсказки, вопросы, лента.

`GET /` — страница для браузера (`meet assist` из консоли открывает её сам):
при запущенном агенте-участнике — чат (`CHAT_PAGE`: лента, кнопки агента,
реакции, строка ввода, картинки, «Стоп» — только маршруты `/chat*` и события
чата ниже), иначе — прежняя `PAGE` (сводка, подсказки, `/ask`). Страница шлёт
запросы со своего Origin; токен ей не нужен — путь к файлу (`/chat/attach`)
принимается только от приложения.

Отдаёт поток состояния через SSE (`GET /events`),
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

Любой запрос — только с `Host` 127.0.0.1 или localhost и своим портом
(защита от DNS-подмены: иначе чужая страница прочла бы ленту и чат).

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
  который резидент дал ребёнку в окружении; без токена — 403) → как
  `/chat/paste`;
- `POST /chat/{id}/click` `{"label", "client_id"?}` → `{"ok", "id"}`;
- `POST /chat/{id}/react` `{"emoji", "on"?}` → `{"ok", "changed"}`;
- `POST /chat/{id}/confirm` `{"allow": bool, "meeting"?: bool}` → `{"ok"}`:
  решение по карточке подтверждения Meet (свобода по согласию, 0.3.7);
  `meeting` — «разрешать такое до конца встречи»; один раз — решённая
  карточка — 400;
- `POST /chat/{id}/revoke` → `{"ok"}`: отозвать разрешение «до конца встречи»;
- `POST /chat/stop` `{"id"?}` → `{"ok"}`;
- `POST /chat/attachments/{aid}/remove` → `{"ok", "changed"}`: вложение
  убрали из строки ввода до отправки (запись — `removed`, файлы — с диска);
  уже отправленное — 400;
- `PUT /agent/frequency` `{"frequency": "less|normal|more|реже|обычно|чаще",
  "persist"?}` → `{"frequency", "label", "live", "saved"}`: агенту — сразу,
  в настройки (`assist.frequency`) — ключом, если не `persist: false`
  (резидент сохраняет сам);
- `PUT /agent/profile` `{"profile": "work|personal"}` → `{"profile", "label",
  "live"}`: профиль только этой сессии (0.3.7) — агенту пометка в ближайший
  ход, журналу встречи — новое значение; настройка по умолчанию не меняется.

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

# Страница чата с агентом-участником (`assist.participant`, по умолчанию с
# 0.3.6): `GET /` отдаёт её, когда агент запущен, иначе — прежнюю `PAGE`.
# Без зависимостей: лента из `chat_snapshot` / `chat` / `chat_partial` /
# `agent` (SSE `/events?transcript=1`), кнопки агента, реакции, строка ввода
# (Enter — отправить, Shift+Enter — новая строка), картинки (Ctrl+V,
# перетаскивание, «📎» → `/chat/paste`), «Стоп», «Как часто писать».
# Текст агента — только через textContent (жирный `**…**` и списки
# собираются узлами DOM, HTML из ответа не исполняется). Все запросы — со
# своего Origin; путь к файлу (`/chat/attach`) страница не шлёт: он только
# для приложения с токеном.
CHAT_PAGE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>meet assist — чат</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--fg:#1d1d1f;--muted:#6b6b72;--line:#e2e2e6;
  --agent:#f2f3f6;--me:#e5effd;--accent:#2a62d0;--pin:#fff4d6;--err:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#1c1c1f;--fg:#ececf0;--muted:#9c9ca4;
  --line:#34343a;--agent:#2a2a2f;--me:#1e324f;--accent:#80a8ff;--pin:#3b3420;--err:#ff8a80}}
*{box-sizing:border-box}
body{margin:0;height:100vh;display:flex;flex-direction:column;background:var(--bg);
  color:var(--fg);font:14px/1.45 "Segoe UI",system-ui,sans-serif}
header{display:flex;flex-wrap:wrap;align-items:center;gap:4px 12px;padding:8px 16px;
  border-bottom:1px solid var(--line)}
header h1{margin:0;font-size:16px}
.muted{color:var(--muted);font-size:12px}
#state.error{color:var(--err)}
#link{color:var(--err);font-size:12px}
#feed{flex:1;overflow:auto;padding:12px 16px}
.msg{max-width:46rem;margin:0 0 12px}
.msg .meta{color:var(--muted);font-size:12px;margin-bottom:2px}
.msg .body{border-radius:10px;padding:8px 10px;overflow-wrap:anywhere}
.msg .body p{margin:0 0 4px}
.msg .body ul,.msg .body ol{margin:2px 0 4px;padding-left:20px}
.agent .body{background:var(--agent)}
.agent.pin .body{background:var(--pin)}
.agent.writing .body{opacity:.75}
.user{margin-left:auto}
.user .body{background:var(--me);white-space:pre-wrap}
.line{color:var(--muted);font-size:12px;text-align:center;margin:0 0 12px}
.note{color:var(--muted);font-size:12px;margin-top:2px}
.err{color:var(--err)}
.row{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
button{font:inherit;cursor:pointer;border:1px solid var(--line);border-radius:14px;
  padding:2px 10px;background:transparent;color:inherit}
button:hover{border-color:var(--accent)}
button:disabled{cursor:default;opacity:.5}
button.used{border-color:var(--accent);color:var(--accent)}
.react button{padding:0 6px;opacity:.55}
.react button[aria-pressed=true]{opacity:1;border-color:var(--accent)}
.react .lbl{display:none;margin-left:4px;font-size:12px}
.msg:hover .react .lbl,.msg:focus-within .react .lbl,.react button[aria-pressed=true] .lbl{display:inline}
.msg:hover .react button,.msg:focus-within .react button{opacity:1}
.ack{color:var(--accent);font-size:12px;margin-top:4px;animation:ack 3.5s ease forwards}
@keyframes ack{0%,70%{opacity:1}100%{opacity:0}}
.pending{color:var(--muted);font-size:12px;margin-top:4px;font-style:italic}
.tag{border:1px solid var(--accent);color:var(--accent);border-radius:8px;padding:0 6px;font-size:11px}
button.ref{border:0;padding:0;color:var(--accent);font-size:12px;text-decoration:underline;border-radius:0}
.msg.flash .body{outline:2px solid var(--accent)}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
@media (max-width:420px){.react .lbl{display:none!important}}
@media (prefers-reduced-motion:reduce){.ack{animation:none}}
footer{border-top:1px solid var(--line);padding:8px 16px}
#chips{display:flex;flex-wrap:wrap;gap:6px}
#chips:not(:empty){margin-bottom:6px}
#chips span{font-size:12px;border:1px solid var(--line);border-radius:10px;padding:1px 8px}
#composer{display:flex;gap:8px;align-items:flex-end}
#text{flex:1;resize:none;font:inherit;padding:6px 8px;border:1px solid var(--line);
  border-radius:8px;background:var(--bg);color:inherit;min-height:2.4em;max-height:10em}
#notice{min-height:1.2em}
details{border-top:1px solid var(--line);padding:4px 16px;max-height:24vh;overflow:auto}
#transcript{white-space:pre-wrap;color:var(--muted);font-size:12px}
body.drop #feed{outline:2px dashed var(--accent);outline-offset:-6px}
.card{max-width:46rem;margin:0 auto 12px;border:1px solid var(--accent);border-radius:10px;padding:8px 10px}
.card pre{margin:4px 0;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.4 Consolas,monospace;unicode-bidi:plaintext;text-align:left;max-height:50vh;overflow:auto}
.card .done{color:var(--muted);font-size:12px}
.card .warn{color:var(--err);font-weight:600;font-size:12px}
#grants button{padding:0 6px;margin-left:2px}
#pending{padding:0 16px}
#pending:not(:empty){padding-top:8px;border-bottom:1px solid var(--line)}
</style></head><body>
<header>
  <h1>Ассистент</h1>
  <span id="state" class="muted">подключаюсь…</span>
  <span id="model" class="muted"></span>
  <span id="sees" class="muted"></span>
  <span id="notes" class="muted"></span>
  <span id="grants" class="muted"></span>
  <label class="muted">Как часто писать
    <select id="frequency"><option>реже</option><option>обычно</option><option>чаще</option></select>
  </label>
  <span id="status" class="muted"></span>
  <span id="link"></span>
</header>
<section id="pending" aria-label="Подтверждение действия"></section>
<main id="feed" role="log" aria-live="polite"></main>
<span id="announce" class="sr" role="status" aria-live="polite"></span>
<footer>
  <div id="chips"></div>
  <div id="composer">
    <textarea id="text" rows="2" placeholder="Сообщение ассистенту (Enter — отправить, Shift+Enter — новая строка)"></textarea>
    <input id="file" type="file" accept="image/*" multiple hidden>
    <button id="pick" title="Прикрепить изображение">📎</button>
    <button id="send">Отправить</button>
    <button id="stop" hidden>Стоп</button>
  </div>
  <div id="notice" class="muted" role="status"></div>
</footer>
<details><summary class="muted">Расшифровка</summary><div id="transcript"></div></details>
<script>
(function () {
  const HIDDEN = new Set(["held", "dropped", "superseded", "dismissed"]);
  // Реакции: эмодзи (ключ журнала), подпись, что будет (подсказка), отклик после нажатия.
  const REACTIONS = [
    ["👍", "Полезно", "Полезно — ассистент будет писать больше такого", "Учту: такое полезно"],
    ["👎", "Не по теме", "Не по теме — ассистент поймёт, что промахнулся, и скорректирует, о чём писать",
     "Учту: скорректирую, о чём пишу"],
    ["❓", "Поясни", "Поясни — ассистент объяснит, на что опирался", ""],
  ];
  const ACK_MS = 3500, EXPLAIN_WAIT_S = 300;
  // Отклик на реакцию — только в этом окне (не в журнале): id сообщения → текст.
  const acks = new Map();
  const PASTE_MAX = 10 * 1024 * 1024;
  const $ = (id) => document.getElementById(id);
  const feed = $("feed"), text = $("text");
  let msgs = new Map(), order = [], seq = -1, partial = null, agent = null;
  let chips = [], draftId = newId(), sending = false, resyncing = false, noticeTimer = 0;
  // Узлы ленты по id: перерисовывается только изменившееся сообщение —
  // фокус, выделение и нажатия на остальных не теряются (ревью I1).
  const nodes = new Map();
  // Нажатая кнопка агента до ответа сервера: ряд блокируется сразу (ревью M1).
  const pendingClicks = new Map();
  const CHIPS_MAX = 10;
  let staleTries = 0;

  function newId() {
    return "web-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
  }

  function notice(msg) {
    $("notice").textContent = msg || "";
    clearTimeout(noticeTimer);
    if (msg) noticeTimer = setTimeout(() => { $("notice").textContent = ""; }, 8000);
  }

  async function api(method, path, body, headers) {
    const init = {method: method, headers: headers || {}};
    if (body instanceof Blob) init.body = body;
    else if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const r = await fetch(path, init);
    const raw = await r.text();
    if (!r.ok) throw new Error(raw || ("ошибка " + r.status));
    try { return raw ? JSON.parse(raw) : {}; } catch (e) { return {}; }
  }

  // --- журнал: снимок и события по номеру ---

  function applySnapshot(s) {
    if (!s || !Array.isArray(s.messages)) return;
    msgs = new Map(); order = [];
    for (const m of s.messages) { if (m && m.id) { msgs.set(m.id, m); order.push(m.id); } }
    seq = typeof s.seq === "number" ? s.seq : seq;
    partial = s.partial || null;
    if (s.agent) agent = s.agent;
    render();
  }

  async function resync() {
    if (resyncing) return;
    resyncing = true;
    let again = false;
    try {
      const s = await api("GET", "/chat");
      if (typeof s.seq !== "number" || s.seq >= seq) { applySnapshot(s); staleTries = 0; }
      else again = true;   // снимок старее применённых событий — повторить (ревью M2)
    } catch (e) { notice("Чат не обновился: " + e.message); again = true; }
    finally { resyncing = false; }
    if (again && staleTries < 5) {
      staleTries += 1;
      setTimeout(resync, 500 * staleTries);
    }
  }

  function applyEvent(e) {
    if (!e || typeof e !== "object") return;
    if (typeof e.seq === "number" && e.seq <= seq) return;
    if (e.op === "add" && e.message && e.message.id) {
      if (!msgs.has(e.message.id)) order.push(e.message.id);
      msgs.set(e.message.id, e.message);
    } else if (e.op === "patch" && e.id) {
      const m = msgs.get(e.id);
      if (!m) { resync(); return; }
      Object.assign(m, e.set || {});
      if (m.status !== "writing" && partial && partial.id === m.id) partial = null;
    } else return;
    if (typeof e.seq === "number") seq = e.seq;
    render();
  }

  // --- текст агента: без HTML, только жирный и списки узлами DOM ---

  function inline(el, s) {
    const parts = String(s).split("**");
    parts.forEach((part, i) => {
      if (i % 2 === 1 && i < parts.length - 1) {
        const b = document.createElement("b");
        b.textContent = part;
        el.appendChild(b);
      } else {
        el.appendChild(document.createTextNode(i % 2 === 1 ? "**" + part : part));
      }
    });
  }

  function renderText(el, s) {
    let list = null;
    for (const line of String(s || "").split(/\r?\n/)) {
      const item = /^\s*(?:([-*•])|(\d+)[.)])\s+(.*)$/.exec(line);
      if (item) {
        const tag = item[2] ? "ol" : "ul";
        if (!list || list.tagName.toLowerCase() !== tag) {
          list = document.createElement(tag);
          el.appendChild(list);
        }
        const li = document.createElement("li");
        inline(li, item[3]);
        list.appendChild(li);
        continue;
      }
      list = null;
      if (!line.trim()) continue;
      const p = document.createElement("p");
      inline(p, line);
      el.appendChild(p);
    }
  }

  // --- лента ---

  function clock(t) {
    if (typeof t !== "number" || !(t >= 0)) return "";
    const s = Math.floor(t), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60;
    const two = (n) => String(n).padStart(2, "0");
    return (h ? h + ":" + two(m) : String(m)) + ":" + two(s % 60);
  }

  function partialText(m) {
    return partial && partial.id === m.id && partial.text ? partial.text : "";
  }

  function visible(m) {
    if (!m || typeof m !== "object") return false;
    if (m.kind === "tool" || m.kind === "attachment") return false;
    if (m.kind === "meeting" && (m.event === "reaction" || m.event === "voiced")) return false;
    if (m.kind === "agent") {
      if (HIDDEN.has(m.status)) return false;
      // Молчаливый ход сам по себе: пузырь — только с текстом или на ответ вам.
      if (m.status === "writing" && !m.text && !partialText(m) && m.mode !== "reply") return false;
    }
    return true;
  }

  function usedButtons() {
    const used = new Map();
    for (const id of order) {
      const m = msgs.get(id);
      if (m && m.kind === "user" && m.via === "button" && m.re) used.set(m.re, m.text);
    }
    return used;
  }

  function el(tag, cls, txt) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (txt !== undefined) node.textContent = txt;
    return node;
  }

  function quoteOf(mid) {
    const t = msgs.get(mid) && msgs.get(mid).text;
    if (!t) return "";
    const flat = String(t).replace(/[*_`#>]/g, "").split(/\s+/).join(" ").trim();
    return " «" + (flat.length > 40 ? flat.slice(0, 39).trimEnd() + "…" : flat) + "»";
  }

  function showMessage(mid) {
    const entry = nodes.get(mid);
    if (!entry) return;
    entry.node.scrollIntoView({block: "center"});
    entry.node.tabIndex = -1;
    entry.node.focus({preventScroll: true});
    entry.node.classList.add("flash");
    setTimeout(() => entry.node.classList.remove("flash"), 1500);
  }

  // ❓ поставлен недавно, а пояснения (ответа с `explains`) или строки «нечего
  // добавить» с `re` на это сообщение (или на просьбу после встречи) ещё нет.
  function explainPending(m) {
    const at = m.reactions && m.reactions["❓"];
    if (typeof at !== "number" || Date.now() / 1000 - at > EXPLAIN_WAIT_S) return false;
    for (const id of order) {
      const r = msgs.get(id);
      if (!r || typeof r.at !== "number" || r.at < at - 1) continue;
      if (r.kind === "agent" && r.explains === m.id && ["writing", "shown", "failed", "cancelled"].includes(r.status)) return false;
      if (r.kind === "system" && r.re) {
        const asked = msgs.get(r.re);
        if (r.re === m.id || (asked && asked.via === "reaction" && asked.re === m.id)) return false;
      }
    }
    return true;
  }

  // «поясняет…» гаснет само через EXPLAIN_WAIT_S — и после перезагрузки страницы:
  // таймер ставится по самому раннему ожиданию при каждой отрисовке.
  let expiryTimer = 0;
  function armExplainExpiry() {
    clearTimeout(expiryTimer);
    let soon = null;
    for (const id of order) {
      const m = msgs.get(id);
      if (!m || m.kind !== "agent" || !explainPending(m)) continue;
      const when = (m.reactions["❓"] + EXPLAIN_WAIT_S) * 1000 + 50;
      if (soon === null || when < soon) soon = when;
    }
    if (soon !== null) expiryTimer = setTimeout(render, Math.max(0, soon - Date.now()));
  }

  function agentBody(body, m) {
    body.replaceChildren();
    const shown = m.text || partialText(m);
    if (shown) renderText(body, shown);
    else body.textContent = m.status === "writing" ? "пишет…" : (m.status === "failed" ? "Не удалось получить ответ" : "");
  }

  function agentNode(m, used) {
    const box = el("div", "msg agent" + (m.pin ? " pin" : "") + (m.status === "writing" ? " writing" : ""));
    box.dataset.id = m.id;
    // Пишется — экранный диктор ждёт готового текста, а не каждого куска.
    if (m.status === "writing") box.setAttribute("aria-busy", "true");
    const meta = ["Ассистент", clock(m.t)];
    if (m.pin) meta.push("вопрос вам");
    if (m.status === "cancelled" && !m.note) meta.push("остановлено");
    const head = el("div", "meta", meta.filter(Boolean).join(" · "));
    if (typeof m.explains === "string") {
      head.appendChild(document.createTextNode(" "));
      head.appendChild(el("span", "tag", "пояснение"));
      head.appendChild(document.createTextNode(" "));
      const ref = el("button", "ref", "к сообщению" + quoteOf(m.explains));
      ref.title = "Показать сообщение, которое поясняет ассистент";
      ref.addEventListener("click", () => showMessage(m.explains));
      head.appendChild(ref);
    }
    box.appendChild(head);
    const body = el("div", "body");
    agentBody(body, m);
    box.appendChild(body);
    if (m.status === "failed" && m.error) box.appendChild(el("div", "note err", m.error));
    if (m.note) box.appendChild(el("div", "note", m.note));
    const buttons = Array.isArray(m.buttons) ? m.buttons : [];
    if (buttons.length && m.status !== "writing") {
      const row = el("div", "row");
      const chosen = used.has(m.id) ? used.get(m.id) : pendingClicks.get(m.id);
      for (const label of buttons) {
        const b = el("button", label === chosen ? "used" : "", (label === chosen ? "✓ " : "") + label);
        if (chosen !== undefined) b.disabled = true;
        b.addEventListener("click", () => click(m.id, label));
        row.appendChild(b);
      }
      box.appendChild(row);
    }
    if (m.status === "shown") {
      const row = el("div", "row react");
      const set = m.reactions && typeof m.reactions === "object" ? m.reactions : {};
      for (const [emoji, label, hint] of REACTIONS) {
        const on = Object.prototype.hasOwnProperty.call(set, emoji);
        const b = el("button", "", emoji);
        b.appendChild(el("span", "lbl", label));
        b.title = hint;
        b.setAttribute("aria-label", emoji + " " + label);
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.addEventListener("click", () => react(m.id, emoji, !on));
        row.appendChild(b);
      }
      box.appendChild(row);
      // Видимые заметки; диктору их объявляет постоянная область #announce.
      if (acks.has(m.id)) box.appendChild(el("div", "ack", acks.get(m.id)));
      if (explainPending(m)) box.appendChild(el("div", "pending", "Ассистент поясняет…"));
    }
    return box;
  }

  function userNode(m) {
    const box = el("div", "msg user");
    box.dataset.id = m.id;
    box.appendChild(el("div", "meta", ["Вы", clock(m.t), m.via === "button" ? "кнопка" : ""].filter(Boolean).join(" · ")));
    if (m.text) box.appendChild(el("div", "body", m.text));
    for (const aid of Array.isArray(m.attachments) ? m.attachments : []) {
      const a = msgs.get(aid);
      if (!a) continue;
      const parts = ["📎 " + (a.name || aid)];
      if (a.status === "failed") parts.push("не разобрано");
      if (a.note) parts.push(a.note);
      box.appendChild(el("div", "note", parts.join(" · ")));
    }
    return box;
  }

  function signature(m, used) {
    // Всё, от чего зависит узел, кроме текста, который ещё пишется.
    const files = m.kind === "user" && Array.isArray(m.attachments)
      ? m.attachments.map((aid) => { const a = msgs.get(aid) || {}; return [aid, a.name, a.status, a.note]; })
      : null;
    const chosen = used.has(m.id) ? used.get(m.id) : pendingClicks.get(m.id);
    const extra = m.kind === "agent"
      ? [acks.get(m.id) || null, explainPending(m), m.explains || null, m.explains ? quoteOf(m.explains) : null] : null;
    return JSON.stringify([m.kind, m.status, m.text, m.t, m.pin, m.note, m.error, m.buttons, m.decision, m.args, m.preview,
      m.reactions, m.via, files, chosen === undefined ? null : chosen, extra,
      m.status === "writing" && !m.text && !partialText(m)]);
  }

  // --- карточка подтверждения Meet (свобода по согласию) ---

  const DECIDED = {allow: "Разрешено один раз", allow_meeting: "Разрешено до конца встречи", deny: "Отклонено", timeout: "Время вышло — не выполнено",
    cancelled: "Отменено", expired: "Не дождались ответа — не выполнено"};

  function cardOpen(m) {
    return !m.decision && !(typeof m.expires_at === "number" && m.expires_at * 1000 < Date.now());
  }

  function cardNode(m) {
    const box = el("div", "card");
    box.dataset.id = m.id;
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Ассистент хочет выполнить: " + (m.title || m.tool || ""));
    box.appendChild(el("div", "", "Ассистент хочет выполнить: " + (m.title || m.tool || "") + (m.size ? " · " + m.size : "")));
    for (const w of Array.isArray(m.warnings) ? m.warnings : []) box.appendChild(el("div", "warn", w));
    // Вызов целиком или, если длинный, начало и конец (резидент прислал оба, с видимыми
    // пометками пробелов и переводов строк): хвост виден всегда, «Показать полностью» — по желанию.
    const args = String(m.args || "");
    const pre = el("pre", "", m.preview || args);
    pre.setAttribute("dir", "ltr");
    if (args) box.appendChild(pre);
    if (m.preview) {
      const more = el("button", "", "Показать полностью");
      more.addEventListener("click", () => { pre.textContent = args; more.remove(); });
      box.appendChild(more);
    }
    if (cardOpen(m)) {
      const row = el("div", "row");
      const yes = el("button", "", "Разрешить один раз");
      yes.addEventListener("click", () => decide(m.id, true, false));
      row.append(yes);
      if (m.grant && m.grant.label) {
        const all = el("button", "", "Разрешать такое до конца встречи");
        all.title = "Дальше до конца встречи без вопросов: " + m.grant.label;
        all.addEventListener("click", () => decide(m.id, true, true));
        row.append(all);
      }
      const no = el("button", "", "Отклонить");
      no.addEventListener("click", () => decide(m.id, false, false));
      row.append(no);
      box.appendChild(row);
      box.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); decide(m.id, false, false); } });
    } else {
      box.appendChild(el("div", "done", DECIDED[m.decision] || DECIDED.expired));
    }
    return box;
  }

  const deciding = new Set();
  async function decide(mid, allow, meeting) {
    if (deciding.has(mid)) return;
    deciding.add(mid);
    try { await api("POST", "/chat/" + encodeURIComponent(mid) + "/confirm", {allow: allow, meeting: !!meeting}); }
    catch (e) { notice("Решение не сохранено: " + e.message); }
    finally { deciding.delete(mid); }
  }

  function renderPending() {
    // Карточки, которые ждут решения, — над лентой: их видно, даже если лента прокручена вверх.
    const box = $("pending");
    box.replaceChildren();
    for (const id of order) {
      const m = msgs.get(id);
      if (m && m.kind === "system" && m.card === "confirm" && cardOpen(m)) box.appendChild(cardNode(m));
    }
  }

  function build(m, used) {
    if (m.kind === "agent") return agentNode(m, used);
    if (m.kind === "user") return userNode(m);
    if (m.kind === "system" && m.card === "confirm") return cardOpen(m) ? el("div", "line", m.text + " — ждёт решения (выше)") : cardNode(m);
    return el("div", "line", m.text);
  }

  function render() {
    const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40;
    const used = usedButtons();
    const want = [];
    const keep = new Set();
    for (const id of order) {
      const m = msgs.get(id);
      if (!visible(m) || (m.kind !== "agent" && m.kind !== "user" && !m.text)) continue;
      const sig = signature(m, used), part = partialText(m);
      let entry = nodes.get(id);
      if (!entry || entry.sig !== sig) {
        entry = {node: build(m, used), sig: sig, part: part};
        nodes.set(id, entry);
      } else if (entry.part !== part) {
        // Пишется ответ: меняется только текст пузыря, сам узел остаётся.
        agentBody(entry.node.querySelector(".body"), m);
        entry.part = part;
      }
      keep.add(id);
      want.push(entry.node);
    }
    for (const id of Array.from(nodes.keys())) { if (!keep.has(id)) nodes.delete(id); }
    want.forEach((node, i) => {
      if (feed.children[i] !== node) feed.insertBefore(node, feed.children[i] || null);
    });
    while (feed.children.length > want.length) feed.lastChild.remove();
    if (atBottom) feed.scrollTop = feed.scrollHeight;
    armExplainExpiry();
    renderPending();
    renderAgent();
  }

  function renderAgent() {
    const a = agent || {};
    const st = $("state");
    st.className = a.state === "error" ? "muted error" : "muted";
    // «пишет…» — когда пузырь ответа виден; молчаливый ход — «думает…».
    const bubble = a.writing ? visible(msgs.get(a.writing)) : false;
    st.textContent = a.state === "error" ? "ошибка" + (a.error ? ": " + a.error : "")
      : a.state === "writing" ? (bubble ? "пишет…" : "думает…") : (agent ? "слушает" : "подключаюсь…");
    $("model").textContent = a.label || a.provider || "";
    const sees = a.sees || {}, parts = [];
    if (sees.conversation !== false) parts.push("разговор");
    if (sees.kb) parts.push(sees.kb_docs === false ? "карта" : "структура базы знаний");
    if (sees.materials > 0) parts.push(sees.materials + " " + plural(sees.materials, "материал", "материала", "материалов"));
    if (sees.images > 0) parts.push(sees.images + " " + plural(sees.images, "изображение", "изображения", "изображений"));
    const can = a.can || {};
    const mcp = Array.isArray(can.mcp) && can.mcp.length ? "MCP (" + can.mcp.slice(0, 3).join(", ") + (can.mcp.length > 3 ? "…" : "") + ")" : "MCP";
    const canText = can.mode === "consent" ? "файлы, " + mcp + ", веб — по вашему согласию"
      : can.mode === "files" ? "читать файлы по вашей просьбе" : "";
    $("sees").textContent = agent ? "видит: " + parts.join(", ") + (canText ? " · может: " + canText : "") : "";
    const notes = [];
    if (agent && !a.vision) notes.push("модель не видит изображения — уходит только текст");
    if (agent && !a.deny_enforced) notes.push("исключённые папки — только просьба в инструкции");
    $("notes").textContent = notes.join(" · ");
    // Разрешено «до конца встречи» — с «×», чтобы отозвать.
    const grants = $("grants");
    grants.replaceChildren();
    const list = Array.isArray(a.grants) ? a.grants : [];
    if (list.length) grants.appendChild(document.createTextNode("разрешено: "));
    list.forEach((g, i) => {
      if (i) grants.appendChild(document.createTextNode(", "));
      grants.appendChild(document.createTextNode(g.label));
      const x = el("button", "", "×");
      x.setAttribute("aria-label", "Отозвать: " + g.label);
      x.addEventListener("click", () => api("POST", "/chat/" + encodeURIComponent(g.id) + "/revoke", {})
        .catch((e) => notice("Не отозвано: " + e.message)));
      grants.appendChild(x);
    });
    if (a.frequency && document.activeElement !== $("frequency")) $("frequency").value = a.frequency;
    $("stop").hidden = !bubble;
  }

  // --- действия ---

  function plural(n, one, few, many) {
    const d = n % 10, h = n % 100;
    if (d === 1 && h !== 11) return one;
    if (d >= 2 && d <= 4 && (h < 12 || h > 14)) return few;
    return many;
  }

  async function click(mid, label) {
    // Второе нажатие (двойной щелчок) — не второе сообщение (ревью M1).
    if (pendingClicks.has(mid) || usedButtons().has(mid)) return;
    pendingClicks.set(mid, label);
    render();
    try { await api("POST", "/chat/" + encodeURIComponent(mid) + "/click", {label: label, client_id: newId()}); }
    catch (e) {
      pendingClicks.delete(mid);
      render();
      notice("Кнопка не сработала: " + e.message);
    }
  }

  async function react(mid, emoji, on) {
    // Отклик — сразу, от страницы, не от модели; снятая реакция — без отклика.
    const ack = on ? (REACTIONS.find((r) => r[0] === emoji) || [])[3] : "";
    const said = ack || (on && emoji === "❓" ? "Ассистент поясняет…" : "");
    if (said) $("announce").textContent = said;
    if (ack) {
      acks.set(mid, ack);
      render();
      setTimeout(() => { if (acks.get(mid) === ack) { acks.delete(mid); render(); } }, ACK_MS);
    }
    try { await api("POST", "/chat/" + encodeURIComponent(mid) + "/react", {emoji: emoji, on: on}); }
    catch (e) {
      if (acks.get(mid) === ack) { acks.delete(mid); render(); }
      notice("Реакция не сохранена: " + e.message);
    }
  }

  async function send() {
    if (sending) return;
    const body = text.value.trim();
    if (chips.some((c) => c.status === "uploading")) { notice("Подождите: изображение ещё загружается"); return; }
    const ids = chips.filter((c) => c.status === "ready" && c.id).map((c) => c.id);
    if (!body && !ids.length) return;
    sending = true;
    $("send").disabled = true;
    try {
      await api("POST", "/chat", {text: body, attachments: ids, client_id: draftId});
      text.value = "";
      chips = [];
      draftId = newId();
      renderChips();
    } catch (e) {
      notice("Не отправлено: " + e.message);
    } finally {
      sending = false;
      $("send").disabled = false;
      text.focus();
    }
  }

  async function stop() {
    try { await api("POST", "/chat/stop", agent && agent.writing ? {id: agent.writing} : {}); }
    catch (e) { notice("Не остановлено: " + e.message); }
  }

  async function setFrequency(label) {
    try { await api("PUT", "/agent/frequency", {frequency: label}); }
    catch (e) {
      $("frequency").value = (agent && agent.frequency) || "чаще";   // ревью M3
      notice("Частота не изменена: " + e.message);
    }
  }

  // --- изображения: Ctrl+V, перетаскивание, «📎» ---

  function renderChips() {
    const box = $("chips");
    box.replaceChildren();
    for (const c of chips) {
      const chip = el("span", "", "📎 " + c.name + (c.status === "uploading" ? " · загружается…"
        : c.status === "failed" ? " · не разобрано" : "") + (c.note ? " · " + c.note : "") + " ");
      const x = el("button", "", "×");
      x.title = "Убрать";
      x.addEventListener("click", () => removeChip(c));
      chip.appendChild(x);
      box.appendChild(chip);
    }
  }

  async function removeChip(c) {
    chips = chips.filter((other) => other !== c);
    c.removed = true;
    renderChips();
    if (c.id) {
      try { await api("POST", "/chat/attachments/" + c.id + "/remove"); } catch (e) { /* уже отправлено */ }
    }
  }

  async function upload(file) {
    if (!file || !/^image\//.test(file.type)) {
      notice("Здесь прикрепляются только изображения; документы — в окне Meet");
      return;
    }
    if (file.size > PASTE_MAX) { notice("Изображение больше 10 МБ"); return; }
    if (chips.length >= CHIPS_MAX) { notice("В одном сообщении — не больше 10 вложений"); return; }
    const c = {name: file.name || "изображение", status: "uploading", id: null};
    chips.push(c);
    renderChips();
    try {
      const r = await api("POST", "/chat/paste", file,
        {"Content-Type": file.type, "X-File-Name": encodeURIComponent(c.name)});
      c.id = r.id || null;
      c.status = r.status === "ready" ? "ready" : "failed";
      c.note = r.attachment && r.attachment.note ? r.attachment.note : "";
      if (c.removed && c.id) await api("POST", "/chat/attachments/" + c.id + "/remove").catch(() => {});
    } catch (e) {
      chips = chips.filter((other) => other !== c);
      notice("Изображение не загружено: " + e.message);
    }
    renderChips();
  }

  text.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      send();
    }
  });
  text.addEventListener("paste", (e) => {
    const data = e.clipboardData;
    if (!data || data.types.includes("text/plain") || data.types.includes("text/html")) return;
    const files = Array.from(data.files || []).filter((f) => /^image\//.test(f.type));
    if (!files.length) return;
    e.preventDefault();
    files.forEach(upload);
  });
  document.addEventListener("dragover", (e) => { e.preventDefault(); document.body.classList.add("drop"); });
  document.addEventListener("dragleave", (e) => {
    if (!e.relatedTarget) document.body.classList.remove("drop");
  });
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    document.body.classList.remove("drop");
    Array.from((e.dataTransfer && e.dataTransfer.files) || []).forEach(upload);
  });
  $("pick").addEventListener("click", () => $("file").click());
  $("file").addEventListener("change", (e) => {
    Array.from(e.target.files || []).forEach(upload);
    e.target.value = "";
  });
  $("send").addEventListener("click", send);
  $("stop").addEventListener("click", stop);
  $("frequency").addEventListener("change", (e) => setFrequency(e.target.value));

  // --- поток событий ---

  function parse(e) { try { return JSON.parse(e.data); } catch (err) { return null; } }
  const es = new EventSource("/events?transcript=1");
  es.addEventListener("open", () => { $("link").textContent = ""; });
  es.addEventListener("error", () => { $("link").textContent = "нет связи — переподключаюсь…"; });
  es.addEventListener("state", (e) => {
    const s = parse(e);
    if (!s) return;
    $("transcript").textContent = (s.transcript || []).join("\n");
    $("status").textContent = s.status || "";
    if (s.agent) { agent = s.agent; renderAgent(); }
  });
  es.addEventListener("chat_snapshot", (e) => applySnapshot(parse(e)));
  es.addEventListener("chat", (e) => applyEvent(parse(e)));
  es.addEventListener("chat_partial", (e) => {
    const p = parse(e);
    if (p && p.id) { partial = p; render(); }
  });
  es.addEventListener("agent", (e) => {
    const a = parse(e);
    if (a) { agent = a; renderAgent(); }
  });
})();
</script></body></html>"""

# Страницу нельзя встроить в чужую (подсовывание щелчков: «Стоп», кнопки,
# реакции), и она ничего не грузит со стороны: скрипт и стили — свои, внутри.
PAGE_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src 'self' data: blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
    "Referrer-Policy": "no-referrer",
}

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


def _own_port(request):
    sock = request.transport.get_extra_info("sockname") if request.transport else None
    return sock[1] if sock else request.url.port


def _own_origins(request) -> set[str]:
    port = _own_port(request)
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


@web.middleware
async def _own_host(request, handler):
    """Только `Host` 127.0.0.1 или localhost со своим портом: страница с
    чужим именем, которое DNS-подменой указывает на 127.0.0.1, с нами
    «одного происхождения» и иначе прочла бы `GET /chat` и `/events`
    (ревью I3)."""
    port = _own_port(request)
    host = (request.headers.get("Host") or "").strip().lower()
    if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
        raise web.HTTPForbidden(text="чужой Host")
    return await handler(request)


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
    ребёнку (`state.control_token`). Без токена (`meet assist` из консоли)
    путь не принимается вовсе: иначе любой процесс машины заставил бы
    ассистента прочесть любой файл пользователя (ревью I4). Страница браузера
    отдаёт байты (`/chat/paste`)."""
    token = getattr(state, "control_token", None)
    if not token:
        return False
    given = request.headers.get(TOKEN_HEADER) or ""
    return hmac.compare_digest(given.encode("utf-8"), str(token).encode("utf-8"))


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
        # Агент-участник запущен — страница чата; иначе прежняя (сводка,
        # подсказки, «Спросить»): `assist.participant=false` или «Только сводка».
        page = CHAT_PAGE if getattr(state, "participant", None) is not None else PAGE
        return web.Response(text=page, content_type="text/html", headers=PAGE_HEADERS)

    async def events(request):
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
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
                        taken = feed.count
                        snap = await _chat_snapshot(state)
                        if snap is not None:
                            # Номер — только после удачного снимка: не прочитался
                            # (журнал занят) — снимок повторится на следующем
                            # проходе, а не уйдут одни дельты (ревью M2).
                            chat_cursor = taken
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

    async def chat_confirm(request):
        participant = _participant(state)
        mid = _message_id(request)
        body = await _json_body(request)
        allow, meeting = body.get("allow"), body.get("meeting", False)
        if not isinstance(allow, bool) or not isinstance(meeting, bool):
            raise web.HTTPBadRequest(text="allow, meeting — true или false")
        await _guarded(participant.confirm(mid, allow, meeting=meeting))
        return _json_response({"ok": True})

    async def chat_revoke(request):
        participant = _participant(state)
        await _guarded(participant.revoke_grant(_message_id(request)))
        return _json_response({"ok": True})

    async def chat_voice_cancel(request):
        # «Отменить» у «Засчитано голосом» (0.5): нажатие кнопки голосом не выполнится.
        participant = _participant(state)
        await _guarded(participant.cancel_voice(_message_id(request)))
        return _json_response({"ok": True})

    async def chat_stop(request):
        participant = _participant(state)
        body = await _json_body(request)
        mid = body.get("id")
        if mid is not None and (not isinstance(mid, str) or not _MESSAGE_ID.match(mid)):
            raise web.HTTPBadRequest(text="неизвестное сообщение")
        stopped = await _guarded(participant.stop_reply(mid))
        return _json_response({"ok": bool(stopped)})

    async def chat_remove(request):
        participant = _participant(state)
        aid = request.match_info["aid"]
        if not _ATTACHMENT_ID.match(aid):
            raise web.HTTPBadRequest(text="неизвестное вложение")
        event = await _guarded(participant.remove_attachment(aid))
        return _json_response({"ok": True, "changed": event is not None})

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

    async def agent_profile(request):
        from meet.settings import PROFILE_LABELS, profile_key

        body = await _json_body(request)
        key = profile_key(body.get("profile"))
        if key is None:
            raise web.HTTPBadRequest(text="profile — work или personal")
        participant = _participant(state)
        apply = getattr(state, "apply_profile", None)
        if apply is not None:
            apply(key)               # агенту и линии сводки
        else:
            participant.set_profile(key)
        return _json_response({"profile": key, "label": PROFILE_LABELS[key], "live": True})

    # Картинка до 10 МБ в теле `/chat/paste` (по умолчанию aiohttp — 1 МБ).
    app = web.Application(middlewares=[_own_host, _same_origin_posts],
                          client_max_size=PASTE_MAX_BYTES + 64 * 1024)

    async def _no_store(request, response):
        # Чат, расшифровка, вложения — личное: в дисковый кеш браузера не класть.
        response.headers["Cache-Control"] = "no-store"

    app.on_response_prepare.append(_no_store)
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
        web.post("/chat/attachments/{aid}/remove", chat_remove),
        web.post("/chat/{mid}/click", chat_click),
        web.post("/chat/{mid}/react", chat_react),
        web.post("/chat/{mid}/confirm", chat_confirm),
        web.post("/chat/{mid}/revoke", chat_revoke),
        web.post("/chat/{mid}/voice-cancel", chat_voice_cancel),
        web.put("/agent/frequency", agent_frequency),
        web.put("/agent/profile", agent_profile),
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
