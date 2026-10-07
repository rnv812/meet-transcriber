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
 *
 * Куда уходят действия — `backend` (`chatBackend.ts`): во время встречи —
 * `/live/chat*`, после встречи — чат записи (вкладка «Ассистент» карточки).
 *
 * Отклик на реакцию (`ack`): 👍 и 👎 — короткая заметка под сообщением сразу
 * после нажатия, на ACK_MS. Её пишет окно, не модель, и только в своём
 * состоянии: реакция уже лежит в журнале (кнопка остаётся нажатой), а заметка
 * ничего к ней не добавляет — ни агенту, ни `assistant_chat.md`. ❓ вместо
 * заметки — «Ассистент поясняет…» (`explaining`), пока в журнале нет ответа с
 * `explains` на это сообщение; это выводится из журнала, поэтому одинаково и
 * во время встречи, и на вкладке «Ассистент» после неё.
 *
 * Чипы-источники (`sources`): документы, которые агент упомянул (`sources.ts`);
 * список документов базы знаний спрашивается у резидента, только когда в
 * ленте есть сообщение, похожее на упоминание файла, и держится минуту.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { type Endpoint, getKbDocs, newChatClientId } from "../lib/api";
import { errorText } from "../lib/format";
import { openMaterial } from "../lib/shell";
import type {
  AgentFrequencyLabel, AgentInfo, AgentProfile, ChatAttachResult, ChatEvent, ChatMessage, ChatPartial, ChatReaction, ChatSnapshot,
  KbDocs,
} from "../lib/types";
import { type ChatBackend, LIVE_CHAT } from "./chatBackend";
import {
  type ChatState, EMPTY_CHAT, type FeedItem, REACTIONS, chatReducer, explainPending, feedItems, isFinalAgent,
  nextExplainExpiry, nextReveal, pinnedOf, usedButtons, writingShown,
} from "./chatModel";
import { profileOf } from "./profiles";
import { type KbIndex, type Source, findSources, kbIndex, mayMentionDocs } from "./sources";

/** Обработчики событий чата для `openLiveEvents` (их зовёт `useLive`). */
export type ChatSink = {
  /** `fetched` — снимок перечитан запросом: старее уже учтённых событий — не применяется. */
  onChatSnapshot: (s: ChatSnapshot, fetched?: boolean) => void;
  onChat: (e: ChatEvent) => void;
  onChatPartial: (p: ChatPartial) => void;
  onAgent: (a: AgentInfo) => void;
};

/** Сколько держать заметку о несработавшем действии. */
export const CHAT_NOTE_MS = 8000;
/** Сколько видна заметка-отклик на 👍 / 👎 (гаснет в нажатую кнопку). */
export const ACK_MS = 3500;
/** ❓ поставлен — ждём пояснения. */
export const EXPLAINING = "Ассистент поясняет…";
/** Повтор перечитывания ленты: от и до (мс). */
const RESYNC_MIN_MS = 1000;
const RESYNC_MAX_MS = 10_000;
/** Снимок живого чата — последние столько записей (`GET /chat` ребёнка). */
export const SNAPSHOT_LIMIT = 200;
/** «Показать раньше»: столько записей дочитать (предел ребёнка). */
export const HISTORY_LIMIT = 5000;
/** Миниатюр вставленных картинок держим не больше (object URL; старые отзываются). */
export const PREVIEW_MAX = 12;
/** Чип открыл не исходный файл, а текст, извлечённый Meet. */
export const COPY_OPENED = "Открыта копия текста — исходный файл вне базы и библиотеки";
/** Список документов базы знаний для чипов — свежий столько (мс). */
const KB_TTL_MS = 60_000;

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
  /** Отклик окна на только что поставленную 👍 / 👎 этого сообщения (ACK_MS); иначе null. */
  ack: (id: string) => string | null;
  /**
   * Что объявить экранному диктору после реакции («Учту: …», «Ассистент
   * поясняет…»): текст для постоянной вежливой live-области ленты.
   */
  announce: string;
  /** На это сообщение поставлен ❓, а пояснения ещё нет. */
  explaining: (id: string) => boolean;
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
  /** Профиль идущей сессии: сразу в шапке, на резиденте — `PUT /live/profile`. */
  setProfile: (profile: AgentProfile) => Promise<void>;
  paste: (blob: Blob, name?: string) => Promise<ChatAttachResult>;
  attach: (path: string) => Promise<ChatAttachResult>;
  /** Вложение убрали из строки ввода до отправки: у агента его не будет. */
  removeAttachment: (id: string) => Promise<void>;
  /** Убрать закреплённый вопрос («×»): и над лентой, и в свёрнутой панели. */
  hidePin: (id: string) => void;
  /** Документы, которые упомянуло сообщение агента (чипы-источники). */
  sources: (m: ChatMessage) => Source[];
  /** Открыть источник; не вышло — заметка. */
  open: (source: Source) => Promise<void>;
  /** Лента обрезана (последние SNAPSHOT_LIMIT): дочитать раньше; null — нечего. */
  more: (() => Promise<void>) | null;
  composer: ChatComposerState;
  sink: ChatSink;
};

/** Документы базы знаний по адресу резидента: один запрос на всех, свежий KB_TTL_MS. */
const kbCache = new Map<string, { at: number; got: Promise<KbDocs | null> }>();

function useKbDocs(ep: Endpoint | null, wanted: boolean): KbIndex | null {
  const [kb, setKb] = useState<KbIndex | null>(null);
  useEffect(() => {
    if (!ep || !wanted) return;
    let gone = false;
    let hit = kbCache.get(ep.base);
    if (!hit || Date.now() - hit.at > KB_TTL_MS) {
      hit = { at: Date.now(), got: getKbDocs(ep).catch(() => null) };
      kbCache.set(ep.base, hit);
    }
    void hit.got.then((docs) => { if (!gone) setKb(kbIndex(docs)); });
    return () => { gone = true; };
  }, [ep, wanted]);
  return kb;
}

/** Тестам: забыть список документов базы. */
export function resetKbDocs(): void {
  kbCache.clear();
}

/**
 * `opts.profile` — профиль сессии, когда живого агента нет (чат записи после встречи:
 * из `GET /recordings/{id}/chat`); у живого — `agent.profile`.
 */
export function useChat(ep: Endpoint | null, backend: ChatBackend = LIVE_CHAT,
  opts: { profile?: AgentProfile | null } = {}): Chat {
  const [state, dispatch] = useReducer(chatReducer, EMPTY_CHAT);
  const [now, setNow] = useState(() => Date.now());
  const [note, setNote] = useState<string | null>(null);
  const [acks, setAcks] = useState<Record<string, string>>({});
  const [announce, setAnnounce] = useState("");
  const stateRef = useRef(state);
  stateRef.current = state;
  const previews = useRef(new Map<string, string>());
  const [frequency, setFrequencyLocal] = useState<AgentFrequencyLabel | null>(null);
  const [profile, setProfileLocal] = useState<AgentProfile | null>(null);
  const [text, setText] = useState("");
  const [drafts, setDraftList] = useState<ChatDraft[]>([]);
  const dropped = useRef(new Set<string>());
  const [resync, setResync] = useState(0);
  /** Сколько записей пришло последним снимком (обрезан ли он) и дочитана ли история. */
  const [snapSize, setSnapSize] = useState(0);
  const [history, setHistory] = useState(false);

  const sink = useMemo<ChatSink>(() => ({
    onChatSnapshot: (snap, fetched) => {
      setNow(Date.now());
      // Снимок при подключении снова обрезан (последние 200) — «Показать раньше» снова нужна
      // (ревью after-chat, M6); перечитанный старее учтённого — не применяется и не в счёт.
      if (!fetched) setHistory(false);
      if (!fetched || snap.seq >= stateRef.current.seq) setSnapSize(snap.messages?.length ?? 0);
      dispatch({ type: "snapshot", snap, now: Date.now(), fetched });
    },
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

  // «Ассистент поясняет…» — не дольше EXPLAIN_WAIT_S: к сроку перерисоваться.
  const expiry = nextExplainExpiry(state, now);
  useEffect(() => {
    if (expiry === null) return;
    const t = setTimeout(() => setNow(Date.now()), Math.max(0, expiry - Date.now()));
    return () => clearTimeout(t);
  }, [expiry]);

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
    backend.get(ep).then((snap) => {
      if (gone) return;
      if (snap.seq < stateRef.current.seq) again();
      else dispatch({ type: "snapshot", snap, now: Date.now(), fetched: true });
    }).catch((e) => {
      console.warn("getChat:", e);
      if (!gone) again();
    });
    return () => { gone = true; clearTimeout(timer); };
  }, [state.stale, ep, resync, backend]);

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
      const reply = await backend.post(ep, { text: out.text, client_id: clientId, attachments: out.attachments });
      dispatch({ type: "sent", client_id: clientId, id: reply.id });
      return true;
    } catch (e) {
      dispatch({ type: "failed", client_id: clientId, error: errorText(e) });
      return false;
    }
  }, [ep, backend]);

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
      await backend.click(ep, id, label, newChatClientId());
    } catch (e) {
      dispatch({ type: "click", id, label: null });
      setNote(`Кнопка не сработала: ${errorText(e)}`);
    }
  }, [ep, backend]);

  const dropAck = useCallback((id: string, text: string) => setAcks((cur) => {
    if (cur[id] !== text) return cur;
    const next = { ...cur };
    delete next[id];
    return next;
  }), []);

  const react = useCallback(async (id: string, emoji: ChatReaction) => {
    if (!ep) return;
    const on = !stateRef.current.byId[id]?.reactions?.[emoji];
    const at = Date.now();
    dispatch({ type: "react", id, emoji, on, at: at / 1000 });
    setNow(at);
    // Отклик — сразу, от окна; снятая реакция — без отклика (и прежний гаснет).
    const ack = on ? REACTIONS.find((r) => r.emoji === emoji)?.ack ?? null : null;
    setAcks((cur) => {
      const next = { ...cur };
      if (ack) next[id] = ack;
      else delete next[id];
      return next;
    });
    if (ack) setTimeout(() => dropAck(id, ack), ACK_MS);
    // Live-область живёт всегда, меняется только текст: так его объявляют NVDA и JAWS.
    const said = ack ?? (on && emoji === "❓" ? EXPLAINING : "");
    if (said) setAnnounce(said);
    try {
      await backend.react(ep, id, emoji, on);
    } catch (e) {
      dispatch({ type: "react", id, emoji, on: !on, at: Date.now() / 1000 });
      if (ack) dropAck(id, ack);
      setNote(`Реакция не дошла: ${errorText(e)}`);
    }
  }, [ep, backend, dropAck]);

  const stop = useCallback(async () => {
    if (!ep) return;
    const id = writingShown(stateRef.current, Date.now());
    try {
      await backend.stop(ep, id ?? undefined);
    } catch (e) {
      setNote(`Не удалось остановить: ${errorText(e)}`);
    }
  }, [ep, backend]);

  const setFrequency = useCallback(async (label: AgentFrequencyLabel) => {
    if (!ep) return;
    setFrequencyLocal(label);
    try {
      await backend.frequency(ep, label);
    } catch (e) {
      setNote(`Частоту не удалось сменить: ${errorText(e)}`);
      setFrequencyLocal(null);
    }
  }, [ep, backend]);

  // Пришло состояние агента с той же частотой — своё значение больше не нужно.
  const agentFrequency = state.agent?.frequency;
  useEffect(() => {
    if (frequency && agentFrequency === frequency) setFrequencyLocal(null);
  }, [agentFrequency, frequency]);

  const setProfile = useCallback(async (next: AgentProfile) => {
    if (!ep) return;
    setProfileLocal(next);
    try {
      await backend.profile(ep, next);
    } catch (e) {
      setNote(`Профиль не удалось сменить: ${errorText(e)}`);
      setProfileLocal(null);
    }
  }, [ep, backend]);

  // Пришло состояние агента с тем же профилем — своё значение больше не нужно.
  const agentProfile = state.agent?.profile;
  useEffect(() => {
    if (profile && agentProfile === profile) setProfileLocal(null);
  }, [agentProfile, profile]);

  const paste = useCallback(async (blob: Blob, name?: string) => {
    if (!ep) throw new Error("Нет связи с ассистентом");
    const reply = await backend.paste(ep, blob, name);
    if (reply.status !== "failed" && typeof URL.createObjectURL === "function") {
      const keep = previews.current;
      keep.set(reply.id, URL.createObjectURL(blob));
      // Миниатюры живут, пока открыто окно: старые отзываем (ревью live-chat, M14) —
      // у их сообщений остаётся значок с названием.
      while (keep.size > PREVIEW_MAX) {
        const [oldest, url] = keep.entries().next().value as [string, string];
        URL.revokeObjectURL?.(url);
        keep.delete(oldest);
      }
    }
    return reply;
  }, [ep, backend]);

  const attach = useCallback(async (path: string) => {
    if (!ep) throw new Error("Нет связи с ассистентом");
    return backend.attach(ep, path);
  }, [ep, backend]);

  const removeAttachment = useCallback(async (id: string) => {
    if (!ep) return;
    const url = previews.current.get(id);
    if (url) {
      URL.revokeObjectURL?.(url);
      previews.current.delete(id);
    }
    try {
      await backend.remove(ep, id);
    } catch (e) {
      setNote(`Вложение не удалось убрать у ассистента: ${errorText(e)}`);
    }
  }, [ep, backend]);

  const hidePin = useCallback((id: string) => dispatch({ type: "hidePin", id }), []);
  const setDrafts = useCallback((fn: (cur: ChatDraft[]) => ChatDraft[]) => setDraftList(fn), []);
  const composer = useMemo<ChatComposerState>(
    () => ({ text, setText, drafts, setDrafts, dropped: dropped.current }), [text, drafts, setDrafts]);
  const used = useMemo(() => usedButtons(state), [state.order, state.byId, state.clicked]);

  // Чипы-источники: вложения журнала и документы базы знаний.
  const attachments = useMemo(
    () => state.order.map((id) => state.byId[id]).filter((m): m is ChatMessage => m?.kind === "attachment"),
    [state.order, state.byId],
  );
  const wantKb = useMemo(() => state.order.some((id) => {
    const m = state.byId[id];
    return m?.kind === "agent" && typeof m.text === "string" && mayMentionDocs(m.text);
  }), [state.order, state.byId]);
  // «Нейтральный»: базы знаний у сессии нет — и чипов её документов тоже (ревью M4):
  // имя из прежней истории чата не должно выглядеть предложением документа.
  const neutral = profileOf(profile ?? state.agent?.profile ?? opts.profile) === "neutral";
  const kbAll = useKbDocs(ep, wantKb && !neutral);
  const kb = neutral ? null : kbAll;
  const found = useMemo(() => new Map<string, Source[]>(), [attachments, kb]);
  const sources = useCallback((m: ChatMessage) => {
    if (m.kind !== "agent" || !m.text || m.status === "writing") return [];
    const key = `${m.id}\n${m.text}`;
    let got = found.get(key);
    if (!got) {
      got = findSources(m.text, attachments, kb);
      found.set(key, got);
    }
    return got;
  }, [attachments, kb, found]);
  const open = useCallback(async (source: Source) => {
    let error: unknown = null;
    for (const [k, path] of source.paths.entries()) {
      try {
        await openMaterial(path);
        // Исходник оболочка не пустила, открылась копия текста во встрече (ревью M5).
        if (k > 0) setNote(COPY_OPENED);
        return;
      } catch (e) {
        error = e;
      }
    }
    setNote(`Не удалось открыть «${source.label}»${error ? `: ${errorText(error)}` : ""}`);
  }, []);

  // «Показать раньше» (ревью live-chat, M13): снимок — последние SNAPSHOT_LIMIT записей.
  const loadMore = useCallback(async () => {
    if (!ep) return;
    try {
      const snap = await backend.get(ep, HISTORY_LIMIT);
      if (snap.seq < stateRef.current.seq) return;   // старее учтённого — кнопка остаётся
      setHistory(true);
      sink.onChatSnapshot(snap, true);
    } catch (e) {
      setNote(`Не удалось дочитать ленту: ${errorText(e)}`);
    }
  }, [ep, backend, sink]);
  const more = backend.paged && !history && snapSize >= SNAPSHOT_LIMIT ? loadMore : null;

  const items = useMemo(() => feedItems(state, now), [state, now]);
  const agent = useMemo(
    () => (state.agent && (frequency || profile)
      ? { ...state.agent, ...(frequency ? { frequency } : {}), ...(profile ? { profile } : {}) }
      : state.agent),
    [state.agent, frequency, profile],
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
    ack: (id) => acks[id] ?? null,
    announce,
    explaining: (id) => explainPending(state, id, now / 1000),
    attachment: (id) => state.byId[id],
    preview: (id) => previews.current.get(id),
    used: (id) => used.get(id) ?? null,
    send, retry, click, react, stop, setFrequency, setProfile, paste, attach, removeAttachment, hidePin, sources, open, more,
    composer, sink,
  };
}
