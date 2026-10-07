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
/** Виды записей, которые лента показывает сами по себе. */
const FEED_KINDS: ReadonlySet<string> = new Set(["agent", "user", "system", "meeting"]);
export const REACTIONS: { emoji: ChatReaction; label: string }[] = [
  { emoji: "👍", label: "норм" },
  { emoji: "👎", label: "не норм" },
  { emoji: "❓", label: "вопрос" },
];

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
};

export const EMPTY_CHAT: ChatState = {
  seq: -1, loaded: false, order: [], byId: {}, outbox: [], partial: {}, agent: null, seenAt: {}, clicked: {}, stale: false,
};

export type ChatAction =
  | { type: "snapshot"; snap: ChatSnapshot; now: number }
  | { type: "event"; event: ChatEvent; now: number }
  | { type: "partial"; partial: ChatPartial }
  | { type: "agent"; agent: AgentInfo }
  | { type: "queue"; out: Outgoing }
  | { type: "sent"; client_id: string; id: string }
  | { type: "failed"; client_id: string; error: string }
  | { type: "retry"; client_id: string }
  | { type: "click"; id: string; label: string | null }
  | { type: "react"; id: string; emoji: ChatReaction; on: boolean; at: number };

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
      const byId: Record<string, ChatMessage> = {};
      const order: string[] = [];
      for (const m of snap.messages) {
        if (!m || typeof m.id !== "string") continue;
        if (!byId[m.id]) order.push(m.id);
        byId[m.id] = m;
      }
      const partial: Record<string, string> = {};
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
      let { partial, seenAt } = state;
      if (next.status !== "writing" && (event.id in partial || event.id in seenAt)) {
        partial = { ...partial };
        delete partial[event.id];
        seenAt = { ...seenAt };
        delete seenAt[event.id];
      }
      return { ...state, seq: event.seq, byId, partial, seenAt };
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
  }
}

/** Ответ, который пишется, уже можно показать: пришёл его текст или ответ человеку пишется дольше REVEAL_MS. */
export function revealed(state: ChatState, m: ChatMessage, now: number): boolean {
  if (m.status !== "writing") return true;
  if (state.partial[m.id]?.trim()) return true;
  return m.mode === "reply" && now - (state.seenAt[m.id] ?? now) >= REVEAL_MS;
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

function shownInFeed(state: ChatState, m: ChatMessage, now: number): boolean {
  if (!FEED_KINDS.has(m.kind)) return false;
  if (m.kind !== "agent") return true;
  if (typeof m.status === "string" && HIDDEN_STATUSES.has(m.status)) return false;
  return revealed(state, m, now);
}

/** Пункт ленты: запись журнала или сообщение человека, которое ещё отправляется. */
export type FeedItem =
  | { type: "message"; message: ChatMessage }
  | { type: "outgoing"; out: Outgoing };

export function feedItems(state: ChatState, now: number): FeedItem[] {
  const items: FeedItem[] = [];
  for (const id of state.order) {
    const m = state.byId[id];
    if (m && shownInFeed(state, m, now)) items.push({ type: "message", message: m });
  }
  for (const out of state.outbox) items.push({ type: "outgoing", out });
  return items;
}

/** Готовое сообщение агента (не пишется): его считают новым и показывают в свёрнутой панели. */
export function isFinalAgent(m: ChatMessage): boolean {
  return m.kind === "agent" && (m.status === "shown" || m.status === "cancelled" || m.status === "failed")
    && !!(m.text?.trim() || m.error);
}

/**
 * Закреплённый вопрос к человеку (`pin`): последний такой, пока человек
 * после него ничего не написал и не нажал.
 */
export function pinnedOf(state: ChatState): ChatMessage | null {
  let pin: ChatMessage | null = null;
  for (const id of state.order) {
    const m = state.byId[id];
    if (!m) continue;
    if (m.kind === "agent" && m.pin && m.status === "shown") pin = m;
    else if (m.kind === "user" && pin) pin = null;
  }
  if (pin && state.outbox.length) return null;
  return pin;
}

/** Какую кнопку сообщения уже нажали (из журнала или только что). */
export function usedButton(state: ChatState, id: string): string | null {
  if (state.clicked[id]) return state.clicked[id]!;
  let label: string | null = null;
  for (const mid of state.order) {
    const m = state.byId[mid];
    if (m?.kind === "user" && m.via === "button" && m.re === id) label = m.text ?? "";
  }
  return label;
}

/** Ответ, который пишется и виден (для «Стоп»): его id, иначе null. */
export function writingShown(state: ChatState, now: number): string | null {
  for (const id of state.order) {
    const m = state.byId[id];
    if (m?.kind === "agent" && m.status === "writing" && revealed(state, m, now)) return id;
  }
  return null;
}
