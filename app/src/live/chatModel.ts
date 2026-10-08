/**
 * Чат агента-участника глазами окна: свёртка журнала по событиям, что видно в
 * ленте, что закреплено, когда показывать «пишет…».
 *
 * Источник — поток `/live/events`: `chat_snapshot` (лента целиком при каждом
 * подключении — так окно и догоняет после обрыва), `chat` (добавление или
 * правка с номером `seq`; не новее известного — уже учтено), `chat_partial`
 * (текст ответа, который пишется) и `agent` (что с агентом).
 *
 * Что в ленту не попадает:
 * - записи-вложения (`attachment`) — их показывает сообщение, которое на них
 *   ссылается: вложение, добавленное в строку ввода и убранное, в ленте не
 *   появляется вовсе (ревью chat-api, M8);
 * - строки хода работы (`tool`, `event: "call"`) сами по себе: они идут под ответом
 *   своего хода (`reply`), а карточка согласия с тем же `tool_use_id` — в строке;
 * - служебные записи (`tool`) и ответы в скрытых статусах (`held`, `dropped`,
 *   `superseded`, `dismissed`) — правка может перевести ответ в скрытый
 *   статус, тогда он уходит из ленты;
 * - ответ, который пишется (`writing`), — пока не пришёл его текст или, если
 *   это ответ человеку (`mode: "reply"`), пока не прошло REVEAL_MS. Каждый ход
 *   агента начинается с записи `writing`, и ход по расшифровке, кончившийся
 *   молчанием, иначе мигал бы пузырём «Пишет…» каждые 8–25 с (ревью, M9).
 *
 * Сообщения человека видны сразу, до ответа сервера (`outbox`, по
 * `client_id`): пришла их запись — черновик уходит; не дошло — «Повторить» с
 * тем же `client_id` (сервер не задвоит).
 */

import type { AgentInfo, ChatEvent, ChatMessage, ChatPartial, ChatReaction, ChatSnapshot } from "../lib/types";

/** Статусы ответа агента, которых нет в ленте. */
export const HIDDEN_STATUSES: ReadonlySet<string> = new Set(["held", "dropped", "superseded", "dismissed"]);
/** Ответ человеку без текста показывается пузырём «Пишет…» не сразу, а через это время. */
export const REVEAL_MS = 1500;
/** Виды записей, которые лента показывает сами по себе (строки вызовов — под ответом своего хода). */
const FEED_KINDS: ReadonlySet<string> = new Set(["agent", "user", "system", "meeting"]);
/**
 * Реакции на сообщения агента. Ключ в журнале — эмодзи (как в 0.3.6), подпись —
 * формальная, `hint` — что будет (подсказка кнопки), `ack` — отклик окна сразу
 * после нажатия (его пишет окно, не модель; у ❓ вместо отклика — «Ассистент
 * поясняет…» до ответа). 👎 — «мимо темы», частоту он не меняет: её задаёт
 * только «Как часто писать».
 */
export const REACTIONS: { emoji: ChatReaction; label: string; hint: string; ack: string | null }[] = [
  { emoji: "👍", label: "Полезно", hint: "Полезно — ассистент будет писать больше такого", ack: "Учту: такое полезно" },
  {
    emoji: "👎", label: "Не по теме", hint: "Не по теме — ассистент поймёт, что промахнулся, и скорректирует, о чём писать",
    ack: "Учту: скорректирую, о чём пишу",
  },
  { emoji: "❓", label: "Поясни", hint: "Поясни — ассистент объяснит, на что опирался", ack: null },
];
/** ❓ ждёт пояснения не дольше этого (с): агент выключен, ответ потерян — «поясняет…» не висит вечно. */
export const EXPLAIN_WAIT_S = 300;
/** Пояснение — запись не старше ❓ (с допуском: время ❓ в окне ставится до ответа сервера). */
const EXPLAIN_SLACK_S = 2;
/**
 * Ответ агента, который закрывает ожидание пояснения. Пишется — тоже: у
 * пузыря уже есть метка «пояснение», второй «поясняет…» не нужен.
 */
const EXPLAIN_DONE: ReadonlySet<string> = new Set(["writing", "shown", "cancelled", "failed"]);

/** Сообщение человека, которое ещё не подтвердил журнал. */
export type Outgoing = {
  client_id: string;
  text: string;
  attachments: string[];
  /** Когда отправили (мс, часы окна). */
  at: number;
  state: "sending" | "sent" | "failed";
  /** Id сообщения у сервера (ответ POST пришёл раньше записи из потока). */
  id?: string;
  error?: string;
};

export type ChatState = {
  /** Последний учтённый номер события журнала; -1 — снимка ещё не было. */
  seq: number;
  loaded: boolean;
  /** Порядок записей, как пришли (все, включая скрытые и вложения). */
  order: string[];
  byId: Record<string, ChatMessage>;
  outbox: Outgoing[];
  /** Текст ответов, которые пишутся. */
  partial: Record<string, string>;
  agent: AgentInfo | null;
  /** Когда окно узнало об ответе `writing` (мс): от этого — REVEAL_MS. */
  seenAt: Record<string, number>;
  /** Нажатые, но ещё не записанные кнопки: id сообщения → надпись. */
  clicked: Record<string, string>;
  /** Правка пришла к неизвестной записи — ленту надо перечитать. */
  stale: boolean;
  /** Закреплённый вопрос, который человек убрал «×» (в этом окне). */
  hiddenPin: string | null;
};

export const EMPTY_CHAT: ChatState = {
  seq: -1, loaded: false, order: [], byId: {}, outbox: [], partial: {}, agent: null, seenAt: {}, clicked: {}, stale: false,
  hiddenPin: null,
};

export type ChatAction =
  /** `fetched` — снимок из `getChat` (перечитали): старее уже учтённых событий — не применяется. */
  | { type: "snapshot"; snap: ChatSnapshot; now: number; fetched?: boolean }
  | { type: "event"; event: ChatEvent; now: number }
  | { type: "partial"; partial: ChatPartial }
  | { type: "agent"; agent: AgentInfo }
  | { type: "queue"; out: Outgoing }
  | { type: "sent"; client_id: string; id: string }
  | { type: "failed"; client_id: string; error: string }
  | { type: "retry"; client_id: string }
  | { type: "click"; id: string; label: string | null }
  | { type: "react"; id: string; emoji: ChatReaction; on: boolean; at: number }
  | { type: "hidePin"; id: string };

/** Черновики, которые журнал уже показал (по `client_id` или id из ответа POST). */
function settle(outbox: Outgoing[], byId: Record<string, ChatMessage>): Outgoing[] {
  if (!outbox.length) return outbox;
  const ids = new Set<string>();
  for (const m of Object.values(byId)) if (m.kind === "user" && typeof m.client_id === "string") ids.add(m.client_id);
  const next = outbox.filter((o) => !ids.has(o.client_id) && !(o.id && byId[o.id]));
  return next.length === outbox.length ? outbox : next;
}

/** Нажатия, которые журнал уже записал (сообщение `via: "button"` с `re`). */
function settleClicks(clicked: Record<string, string>, byId: Record<string, ChatMessage>): Record<string, string> {
  const keys = Object.keys(clicked);
  if (!keys.length) return clicked;
  const done = new Set<string>();
  for (const m of Object.values(byId)) if (m.kind === "user" && m.via === "button" && m.re) done.add(m.re);
  if (!keys.some((k) => done.has(k))) return clicked;
  return Object.fromEntries(Object.entries(clicked).filter(([k]) => !done.has(k)));
}

function writingSeen(seenAt: Record<string, number>, byId: Record<string, ChatMessage>, now: number) {
  const next: Record<string, number> = {};
  for (const m of Object.values(byId)) {
    if (m.kind === "agent" && m.status === "writing") next[m.id] = seenAt[m.id] ?? now;
  }
  return next;
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "snapshot": {
      const { snap, now } = action;
      if (action.fetched && snap.seq < state.seq) return state;
      const byId: Record<string, ChatMessage> = {};
      const order: string[] = [];
      for (const m of snap.messages) {
        if (!m || typeof m.id !== "string") continue;
        if (!byId[m.id]) order.push(m.id);
        byId[m.id] = m;
      }
      // Текст ответа, который пишется, — от прежнего, пока ответ всё ещё пишется (после
      // встречи текст приходит событием раньше, чем перечитанная лента).
      const partial: Record<string, string> = {};
      for (const [id, text] of Object.entries(state.partial)) if (byId[id]?.status === "writing") partial[id] = text;
      if (snap.partial && byId[snap.partial.id]?.status === "writing") partial[snap.partial.id] = snap.partial.text;
      return {
        ...state, seq: snap.seq, loaded: true, order, byId, partial,
        agent: snap.agent ?? state.agent,
        outbox: settle(state.outbox, byId),
        clicked: settleClicks(state.clicked, byId),
        seenAt: writingSeen(state.seenAt, byId, now),
        stale: false,
      };
    }
    case "event": {
      const { event, now } = action;
      if (event.seq <= state.seq) return state;
      if (event.op === "add") {
        const m = event.message;
        if (!m || typeof m.id !== "string") return { ...state, seq: event.seq };
        const byId = { ...state.byId, [m.id]: m };
        const order = state.byId[m.id] ? state.order : [...state.order, m.id];
        const seenAt = m.kind === "agent" && m.status === "writing" ? { ...state.seenAt, [m.id]: now } : state.seenAt;
        return {
          ...state, seq: event.seq, byId, order, seenAt,
          outbox: settle(state.outbox, byId), clicked: settleClicks(state.clicked, byId),
        };
      }
      const cur = state.byId[event.id];
      if (!cur) {
        // Правка записи, которой окно не знает: скрытая запись стала видимой
        // (или окно что-то пропустило) — перечитать ленту целиком.
        const status = event.set.status;
        const visible = typeof status === "string" && !HIDDEN_STATUSES.has(status);
        return { ...state, seq: event.seq, stale: state.stale || visible };
      }
      const next = { ...cur, ...event.set } as ChatMessage;
      const byId = { ...state.byId, [event.id]: next };
      let { partial, seenAt, order } = state;
      if (next.kind === "agent" && typeof next.status === "string" && HIDDEN_STATUSES.has(next.status)) {
        // Скрытый ответ (молчание, склеенный) больше не нужен: за 2 часа их сотни.
        delete byId[event.id];
        order = order.filter((id) => id !== event.id);
      }
      if (next.status !== "writing" && (event.id in partial || event.id in seenAt)) {
        partial = { ...partial };
        delete partial[event.id];
        seenAt = { ...seenAt };
        delete seenAt[event.id];
      }
      return { ...state, seq: event.seq, byId, order, partial, seenAt };
    }
    case "partial": {
      const { id, text } = action.partial;
      const cur = state.byId[id];
      if (cur && cur.status !== "writing") return state;
      if (state.partial[id] === text) return state;
      return { ...state, partial: { ...state.partial, [id]: text } };
    }
    case "agent":
      return { ...state, agent: action.agent };
    case "queue":
      return { ...state, outbox: [...state.outbox, action.out] };
    case "sent": {
      const outbox = state.outbox
        .map((o) => (o.client_id === action.client_id ? { ...o, state: "sent" as const, id: action.id, error: undefined } : o));
      return { ...state, outbox: settle(outbox, state.byId) };
    }
    case "failed":
      return {
        ...state,
        outbox: state.outbox.map((o) => (o.client_id === action.client_id && o.state !== "sent"
          ? { ...o, state: "failed" as const, error: action.error } : o)),
      };
    case "retry":
      return {
        ...state,
        outbox: state.outbox.map((o) => (o.client_id === action.client_id ? { ...o, state: "sending" as const, error: undefined } : o)),
      };
    case "click": {
      const clicked = { ...state.clicked };
      if (action.label === null) delete clicked[action.id];
      else clicked[action.id] = action.label;
      return { ...state, clicked };
    }
    case "react": {
      const cur = state.byId[action.id];
      if (!cur) return state;
      const reactions = { ...(cur.reactions ?? {}) };
      if (action.on) reactions[action.emoji] = action.at;
      else delete reactions[action.emoji];
      return { ...state, byId: { ...state.byId, [action.id]: { ...cur, reactions } } };
    }
    case "hidePin":
      return { ...state, hiddenPin: action.id };
  }
}

/** Ответ, который пишется, уже можно показать: пришёл его текст или ответ человеку пишется дольше REVEAL_MS. */
export function revealed(state: ChatState, m: ChatMessage, now: number): boolean {
  if (m.status !== "writing") return true;
  if (state.partial[m.id]?.trim()) return true;
  if (m.mode !== "reply") return false;
  // Ответ человеку уже вызывает инструменты — виден сразу (строки хода работы).
  if (Object.values(state.byId).some((r) => r.reply === m.id && isToolRow(r))) return true;
  return now - (state.seenAt[m.id] ?? now) >= REVEAL_MS;
}

/** Когда (мс) следующий ответ человеку станет пузырём «Пишет…»; null — ждать нечего. */
export function nextReveal(state: ChatState, now: number): number | null {
  let soon: number | null = null;
  for (const [id, at] of Object.entries(state.seenAt)) {
    const m = state.byId[id];
    if (!m || m.status !== "writing" || m.mode !== "reply" || state.partial[id]?.trim()) continue;
    const when = at + REVEAL_MS;
    if (when > now && (soon === null || when < soon)) soon = when;
  }
  return soon;
}

function shownInFeed(state: ChatState, m: ChatMessage, now: number, rows: Map<string, ChatMessage[]>): boolean {
  if (!FEED_KINDS.has(m.kind)) return false;
  if (m.kind !== "agent") return true;
  if (typeof m.status === "string" && HIDDEN_STATUSES.has(m.status)) return false;
  // Ответ человеку, который уже что-то делает (строки вызовов), — виден сразу: как в CLI.
  if (m.status === "writing" && m.mode === "reply" && rows.has(m.id)) return true;
  return revealed(state, m, now);
}

/** Строка вызова инструмента агента (ход работы, 0.4) и карточка согласия этого вызова, если она есть. */
export type ToolItem = { row: ChatMessage; card?: ChatMessage };

/**
 * Пункт ленты: запись журнала (у ответа агента — строки вызовов его хода, `tools`) или
 * сообщение человека, которое ещё отправляется.
 */
export type FeedItem =
  | { type: "message"; message: ChatMessage; tools?: ToolItem[] }
  | { type: "outgoing"; out: Outgoing };

/** Строка хода работы (`kind: "tool"`, `event: "call"`); запросы запасного пути — служебные. */
export function isToolRow(m: ChatMessage): boolean {
  return m.kind === "tool" && m.event === "call";
}

/** Строки вызовов по ответам агента (`reply`), по порядку журнала. */
function rowsByReply(state: ChatState): Map<string, ChatMessage[]> {
  const out = new Map<string, ChatMessage[]>();
  for (const id of state.order) {
    const m = state.byId[id];
    if (!m || !isToolRow(m) || typeof m.reply !== "string") continue;
    const list = out.get(m.reply);
    if (list) list.push(m);
    else out.set(m.reply, [m]);
  }
  return out;
}

/**
 * Лента. Строки вызовов идут под ответом своего хода (без ответа в ленте — молчаливый ход по
 * репликам — их не видно); карточка согласия с `tool_use_id` показанной строки — внутри строки.
 */
export function feedItems(state: ChatState, now: number): FeedItem[] {
  const rows = rowsByReply(state);
  const cards = new Map<string, ChatMessage>();
  for (const id of state.order) {
    const m = state.byId[id];
    if (m?.card === "confirm" && typeof m.tool_use_id === "string") cards.set(m.tool_use_id, m);
  }
  const nested = new Set<string>();
  const items: FeedItem[] = [];
  for (const id of state.order) {
    const m = state.byId[id];
    if (!m || m.kind === "tool" || !shownInFeed(state, m, now, rows)) continue;
    if (m.kind === "agent" && rows.has(m.id)) {
      const tools = rows.get(m.id)!.map((row) => {
        const card = typeof row.tool_use_id === "string" ? cards.get(row.tool_use_id) : undefined;
        if (card) nested.add(card.id);
        return card ? { row, card } : { row };
      });
      items.push({ type: "message", message: m, tools });
    } else items.push({ type: "message", message: m });
  }
  const out = nested.size
    ? items.filter((it) => !(it.type === "message" && nested.has(it.message.id)))
    : items;
  for (const o of state.outbox) out.push({ type: "outgoing", out: o });
  return out;
}

/** Готовое сообщение агента (не пишется): его считают новым и показывают в свёрнутой панели. */
export function isFinalAgent(m: ChatMessage): boolean {
  return m.kind === "agent" && (m.status === "shown" || m.status === "cancelled" || m.status === "failed")
    && !!(m.text?.trim() || m.error || m.status === "failed");
}

/**
 * Закреплённый вопрос к человеку (`pin`): последний такой, пока человек
 * после него ничего не написал и не нажал (сообщение, которое не ушло, — не в
 * счёт) и не убрал его «×».
 */
export function pinnedOf(state: ChatState): ChatMessage | null {
  let pin: ChatMessage | null = null;
  for (const id of state.order) {
    const m = state.byId[id];
    if (!m) continue;
    if (m.kind === "agent" && m.pin && m.status === "shown") pin = m;
    else if (m.kind === "user" && pin) pin = null;
  }
  if (pin && state.outbox.some((o) => o.state !== "failed")) return null;
  return pin && pin.id !== state.hiddenPin ? pin : null;
}

/** Карточка подтверждения Meet ещё ждёт решения (не решена и срок не вышел; `now` — мс). */
export function cardOpen(m: ChatMessage, now: number): boolean {
  return m.kind === "system" && m.card === "confirm" && !m.decision
    && !(typeof m.expires_at === "number" && m.expires_at * 1000 < now);
}

/** Карточки подтверждения, которые ждут решения, по порядку журнала. */
export function pendingCards(state: ChatState, now: number): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const id of state.order) {
    const m = state.byId[id];
    if (m && cardOpen(m, now)) out.push(m);
  }
  return out;
}

/** Нажатые кнопки: id сообщения агента → надпись (из журнала и только что нажатые). Один проход. */
export function usedButtons(state: ChatState): Map<string, string> {
  const used = new Map<string, string>();
  for (const mid of state.order) {
    const m = state.byId[mid];
    if (m?.kind === "user" && m.via === "button" && typeof m.re === "string") used.set(m.re, m.text ?? "");
  }
  for (const [id, label] of Object.entries(state.clicked)) used.set(id, label);
  return used;
}

/** Какую кнопку сообщения уже нажали. */
export function usedButton(state: ChatState, id: string): string | null {
  return usedButtons(state).get(id) ?? null;
}

/**
 * ❓ поставлен и пояснения ещё нет: в журнале после ❓ нет ни ответа агента с
 * `explains` на это сообщение (пишется, готов, остановлен или упал), ни строки
 * «нечего добавить» с `re` на него (или на просьбу пояснить после встречи), и
 * с ❓ прошло меньше EXPLAIN_WAIT_S. `now` — секунды Unix.
 */
export function explainPending(state: ChatState, id: string, now: number): boolean {
  const at = state.byId[id]?.reactions?.["❓"];
  if (typeof at !== "number" || now - at > EXPLAIN_WAIT_S) return false;
  for (const rid of state.order) {
    const r = state.byId[rid];
    if (!r || typeof r.at !== "number" || r.at < at - EXPLAIN_SLACK_S) continue;
    if (r.kind === "agent" && r.explains === id && EXPLAIN_DONE.has(r.status ?? "")) return false;
    if (r.kind === "system" && typeof r.re === "string") {
      const asked = state.byId[r.re];
      if (r.re === id || (asked?.via === "reaction" && asked.re === id)) return false;
    }
  }
  return true;
}

/** Когда (мс) истечёт самое раннее ожидание пояснения; null — ждать нечего. */
export function nextExplainExpiry(state: ChatState, now: number): number | null {
  let soon: number | null = null;
  for (const id of state.order) {
    const at = state.byId[id]?.reactions?.["❓"];
    if (typeof at !== "number" || !explainPending(state, id, now / 1000)) continue;
    const when = (at + EXPLAIN_WAIT_S) * 1000 + 1;
    if (soon === null || when < soon) soon = when;
  }
  return soon;
}

/** Ответ, который пишется и виден (для «Стоп»): его id, иначе null. */
export function writingShown(state: ChatState, now: number): string | null {
  for (const id of state.order) {
    const m = state.byId[id];
    if (m?.kind === "agent" && m.status === "writing" && revealed(state, m, now)) return id;
  }
  return null;
}
