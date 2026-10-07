/**
 * Чат агента-участника: состояние (`chatModel`) и действия окна.
 *
 * Событий хук сам не слушает: поток `/live/events` один на окно, его держит
 * `useLive`, а сюда отдаёт события чата через `sink` (стабильный объект). При
 * каждом подключении ассистент шлёт `chat_snapshot` — после обрыва лента
 * перечитывается целиком, а сообщения, отправленные в обрыв, сверяются по
 * `client_id`. Правка неизвестной записи — перечитать ленту (`getChat`).
 *
 * «Пишет…» у ответа без текста появляется по таймеру (REVEAL_MS), поэтому хук
 * сам перерисовывается к нужному моменту.
 *
 * Хук живёт у владельца окна (панель, карточка), а не в рабочей области: текст
 * строки ввода, вложения до отправки и убранный «×» закреплённый вопрос
 * переживают сворачивание панели и смену раскладки.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import {
  type Endpoint, attachChatFile, clickChat, getChat, newChatClientId, pasteChatImage, postChat, reactChat,
  removeChatAttachment, setAgentFrequency, stopChat,
} from "../lib/api";
import { errorText } from "../lib/format";
import type {
  AgentFrequencyLabel, AgentInfo, ChatAttachResult, ChatEvent, ChatMessage, ChatPartial, ChatReaction, ChatSnapshot,
} from "../lib/types";
import {
  type ChatState, EMPTY_CHAT, type FeedItem, chatReducer, feedItems, isFinalAgent, nextReveal, pinnedOf, usedButtons,
  writingShown,
} from "./chatModel";

/** Обработчики событий чата для `openLiveEvents` (их зовёт `useLive`). */
export type ChatSink = {
  onChatSnapshot: (s: ChatSnapshot) => void;
  onChat: (e: ChatEvent) => void;
  onChatPartial: (p: ChatPartial) => void;
  onAgent: (a: AgentInfo) => void;
};

/** Сколько держать заметку о несработавшем действии. */
export const CHAT_NOTE_MS = 8000;
/** Повтор перечитывания ленты: от и до (мс). */
const RESYNC_MIN_MS = 1000;
const RESYNC_MAX_MS = 10_000;

/** Вложение в строке ввода до отправки. */
export type ChatDraft = {
  key: string;
  name: string;
  kind: "image" | "doc";
  status: "uploading" | "ready" | "failed";
  id?: string;
  error?: string;
  /** Миниатюра вставленной картинки (object URL). */
  preview?: string;
};

/** Строка ввода: текст и вложения — у хука, чтобы пережить сворачивание и смену раскладки. */
export type ChatComposerState = {
  text: string;
  setText: (text: string) => void;
  drafts: ChatDraft[];
  setDrafts: (fn: (cur: ChatDraft[]) => ChatDraft[]) => void;
  /** Ключи вложений, убранных, пока они загружались. */
  dropped: Set<string>;
};

export type Chat = {
  state: ChatState;
  /** Лента: что видно, по порядку. */
  items: FeedItem[];
  agent: AgentInfo | null;
  /** Хоть один снимок пришёл. */
  loaded: boolean;
  /** Закреплённый вопрос к человеку. */
  pinned: ChatMessage | null;
  /** Ответ, который пишется и виден (для «Стоп»). */
  writing: string | null;
  /** Последнее готовое сообщение агента (свёрнутая панель). */
  lastAgent: ChatMessage | null;
  /** Не сработало действие (реакция, кнопка, частота): текст на CHAT_NOTE_MS. */
  note: string | null;
  attachment: (id: string) => ChatMessage | undefined;
  /** Миниатюра вставленной картинки (только в этом окне и сеансе). */
  preview: (id: string) => string | undefined;
  used: (id: string) => string | null;
  send: (text: string, attachments?: string[]) => Promise<boolean>;
  retry: (clientId: string) => Promise<boolean>;
  click: (id: string, label: string) => Promise<void>;
  react: (id: string, emoji: ChatReaction) => Promise<void>;
  stop: () => Promise<void>;
  setFrequency: (label: AgentFrequencyLabel) => Promise<void>;
  paste: (blob: Blob, name?: string) => Promise<ChatAttachResult>;
  attach: (path: string) => Promise<ChatAttachResult>;
  /** Вложение убрали из строки ввода до отправки: у агента его не будет. */
  removeAttachment: (id: string) => Promise<void>;
  /** Убрать закреплённый вопрос («×»): и над лентой, и в свёрнутой панели. */
  hidePin: (id: string) => void;
  composer: ChatComposerState;
  sink: ChatSink;
};

export function useChat(ep: Endpoint | null): Chat {
  const [state, dispatch] = useReducer(chatReducer, EMPTY_CHAT);
  const [now, setNow] = useState(() => Date.now());
  const [note, setNote] = useState<string | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const previews = useRef(new Map<string, string>());
  const [frequency, setFrequencyLocal] = useState<AgentFrequencyLabel | null>(null);
  const [text, setText] = useState("");
  const [drafts, setDraftList] = useState<ChatDraft[]>([]);
  const dropped = useRef(new Set<string>());
  const [resync, setResync] = useState(0);

  const sink = useMemo<ChatSink>(() => ({
    onChatSnapshot: (snap) => { setNow(Date.now()); dispatch({ type: "snapshot", snap, now: Date.now() }); },
    onChat: (event) => { setNow(Date.now()); dispatch({ type: "event", event, now: Date.now() }); },
    onChatPartial: (partial) => dispatch({ type: "partial", partial }),
    onAgent: (agent) => dispatch({ type: "agent", agent }),
  }), []);

  // Пузырь «Пишет…» у ответа человеку — к сроку REVEAL_MS.
  const due = nextReveal(state, now);
  useEffect(() => {
    if (due === null) return;
    const t = setTimeout(() => setNow(Date.now()), Math.max(0, due - Date.now()));
    return () => clearTimeout(t);
  }, [due]);

  // Правка неизвестной записи: ленту — заново. Снимок старее уже учтённых
  // событий (они пришли, пока шёл запрос) или ошибка — повтор с растущей паузой.
  useEffect(() => {
    if (!state.stale || !ep) return;
    let gone = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const again = () => {
      const delay = Math.min(RESYNC_MAX_MS, RESYNC_MIN_MS * 2 ** Math.min(resync, 4));
      timer = setTimeout(() => setResync((n) => n + 1), delay);
    };
    getChat(ep).then((snap) => {
      if (gone) return;
      if (snap.seq < stateRef.current.seq) again();
      else dispatch({ type: "snapshot", snap, now: Date.now(), fetched: true });
    }).catch((e) => {
      console.warn("getChat:", e);
      if (!gone) again();
    });
    return () => { gone = true; clearTimeout(timer); };
  }, [state.stale, ep, resync]);

  useEffect(() => {
    if (!note) return;
    const t = setTimeout(() => setNote(null), CHAT_NOTE_MS);
    return () => clearTimeout(t);
  }, [note]);

  useEffect(() => () => {
    for (const url of previews.current.values()) URL.revokeObjectURL?.(url);
  }, []);

  const post = useCallback(async (clientId: string) => {
    const out = stateRef.current.outbox.find((o) => o.client_id === clientId);
    if (!ep || !out) return false;
    try {
      const reply = await postChat(ep, { text: out.text, client_id: clientId, attachments: out.attachments });
      dispatch({ type: "sent", client_id: clientId, id: reply.id });
      return true;
    } catch (e) {
      dispatch({ type: "failed", client_id: clientId, error: errorText(e) });
      return false;
    }
  }, [ep]);

  const send = useCallback(async (text: string, attachments: string[] = []) => {
    if (!ep || (!text.trim() && !attachments.length)) return false;
    const clientId = newChatClientId();
    const out = { client_id: clientId, text: text.trim(), attachments, at: Date.now(), state: "sending" as const };
    dispatch({ type: "queue", out });
    stateRef.current = { ...stateRef.current, outbox: [...stateRef.current.outbox, out] };
    return post(clientId);
  }, [ep, post]);

  const retry = useCallback(async (clientId: string) => {
    dispatch({ type: "retry", client_id: clientId });
    return post(clientId);
  }, [post]);

  const click = useCallback(async (id: string, label: string) => {
    if (!ep) return;
    dispatch({ type: "click", id, label });
    try {
      await clickChat(ep, id, label, newChatClientId());
    } catch (e) {
      dispatch({ type: "click", id, label: null });
      setNote(`Кнопка не сработала: ${errorText(e)}`);
    }
  }, [ep]);

  const react = useCallback(async (id: string, emoji: ChatReaction) => {
    if (!ep) return;
    const on = !stateRef.current.byId[id]?.reactions?.[emoji];
    dispatch({ type: "react", id, emoji, on, at: Date.now() / 1000 });
    try {
      await reactChat(ep, id, emoji, on);
    } catch (e) {
      dispatch({ type: "react", id, emoji, on: !on, at: Date.now() / 1000 });
      setNote(`Реакция не дошла: ${errorText(e)}`);
    }
  }, [ep]);

  const stop = useCallback(async () => {
    if (!ep) return;
    const id = writingShown(stateRef.current, Date.now());
    try {
      await stopChat(ep, id ?? undefined);
    } catch (e) {
      setNote(`Не удалось остановить: ${errorText(e)}`);
    }
  }, [ep]);

  const setFrequency = useCallback(async (label: AgentFrequencyLabel) => {
    if (!ep) return;
    setFrequencyLocal(label);
    try {
      await setAgentFrequency(ep, label);
    } catch (e) {
      setNote(`Частоту не удалось сменить: ${errorText(e)}`);
      setFrequencyLocal(null);
    }
  }, [ep]);

  // Пришло состояние агента с той же частотой — своё значение больше не нужно.
  const agentFrequency = state.agent?.frequency;
  useEffect(() => {
    if (frequency && agentFrequency === frequency) setFrequencyLocal(null);
  }, [agentFrequency, frequency]);

  const paste = useCallback(async (blob: Blob, name?: string) => {
    if (!ep) throw new Error("Нет связи с ассистентом");
    const reply = await pasteChatImage(ep, blob, name);
    if (reply.status !== "failed" && typeof URL.createObjectURL === "function") {
      previews.current.set(reply.id, URL.createObjectURL(blob));
    }
    return reply;
  }, [ep]);

  const attach = useCallback(async (path: string) => {
    if (!ep) throw new Error("Нет связи с ассистентом");
    return attachChatFile(ep, path);
  }, [ep]);

  const removeAttachment = useCallback(async (id: string) => {
    if (!ep) return;
    const url = previews.current.get(id);
    if (url) {
      URL.revokeObjectURL?.(url);
      previews.current.delete(id);
    }
    try {
      await removeChatAttachment(ep, id);
    } catch (e) {
      setNote(`Вложение не удалось убрать у ассистента: ${errorText(e)}`);
    }
  }, [ep]);

  const hidePin = useCallback((id: string) => dispatch({ type: "hidePin", id }), []);
  const setDrafts = useCallback((fn: (cur: ChatDraft[]) => ChatDraft[]) => setDraftList(fn), []);
  const composer = useMemo<ChatComposerState>(
    () => ({ text, setText, drafts, setDrafts, dropped: dropped.current }), [text, drafts, setDrafts]);
  const used = useMemo(() => usedButtons(state), [state.order, state.byId, state.clicked]);

  const items = useMemo(() => feedItems(state, now), [state, now]);
  const agent = useMemo(
    () => (state.agent && frequency ? { ...state.agent, frequency } : state.agent),
    [state.agent, frequency],
  );
  const lastAgent = useMemo(() => {
    for (let k = state.order.length - 1; k >= 0; k--) {
      const m = state.byId[state.order[k]!];
      if (m && isFinalAgent(m)) return m;
    }
    return null;
  }, [state.order, state.byId]);

  return {
    state, items, agent, loaded: state.loaded, pinned: pinnedOf(state), writing: writingShown(state, now), lastAgent, note,
    attachment: (id) => state.byId[id],
    preview: (id) => previews.current.get(id),
    used: (id) => used.get(id) ?? null,
    send, retry, click, react, stop, setFrequency, paste, attach, removeAttachment, hidePin, composer, sink,
  };
}
