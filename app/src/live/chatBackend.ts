/**
 * Куда уходят действия чата с агентом-участником. Лента, строка ввода,
 * кнопки, реакции и копирование — одни и те же (`useChat`, `LiveChat`,
 * `ChatComposer`); разные только адреса:
 *
 * - **во время встречи** (`LIVE_CHAT`) — `/live/chat*`: резидент передаёт их
 *   ребёнку `meet assist`, лента приходит потоком `/live/events`;
 * - **после встречи** (`recordingChatBackend`) — чат записи
 *   `/recordings/{id}/chat*`: сообщение и нажатие кнопки ставят задачу ответа
 *   («Продолжить разговор»), вложения разбирает резидент, «Стоп» снимает
 *   задачу; ленту вкладка перечитывает по событию `chat.updated`.
 */

import {
  type Endpoint, attachChatFile, cancelJob, clickChat, confirmChat, revokeChatGrant, continueChat, getChat, getRecordingChat,
  pasteChatImage, postChat, reactChat, recordingChatAttach, recordingChatClick, recordingChatConfirm,
  recordingChatPaste, recordingChatReact,
  recordingChatRemove, removeChatAttachment, setAgentFrequency, setAgentProfile, stopChat,
} from "../lib/api";
import type {
  AgentFrequencyLabel, AgentProfile, ChatAttachResult, ChatPost, ChatReaction, ChatSnapshot, Job, RecordingChat,
} from "../lib/types";

export type ChatBackend = {
  /** Лента целиком (перечитать); `limit` — сколько последних записей. */
  get: (ep: Endpoint, limit?: number) => Promise<ChatSnapshot>;
  /** Сообщение человека → id его записи в журнале. */
  post: (ep: Endpoint, msg: ChatPost) => Promise<{ id: string }>;
  click: (ep: Endpoint, id: string, label: string, clientId: string) => Promise<unknown>;
  react: (ep: Endpoint, id: string, emoji: ChatReaction, on: boolean) => Promise<unknown>;
  /** Карточка подтверждения Meet: разрешить один раз, до конца встречи (`meeting`) или отклонить. */
  confirm: (ep: Endpoint, id: string, allow: boolean, meeting?: boolean) => Promise<unknown>;
  /** Отозвать разрешение «до конца встречи». */
  revoke: (ep: Endpoint, id: string) => Promise<unknown>;
  /** «Стоп» у ответа, который пишется. */
  stop: (ep: Endpoint, id?: string) => Promise<unknown>;
  frequency: (ep: Endpoint, label: AgentFrequencyLabel) => Promise<unknown>;
  /** Профиль идущей сессии (после встречи — ничего: профиль у сессии уже был). */
  profile: (ep: Endpoint, profile: AgentProfile) => Promise<unknown>;
  paste: (ep: Endpoint, blob: Blob, name?: string) => Promise<ChatAttachResult>;
  attach: (ep: Endpoint, path: string) => Promise<ChatAttachResult>;
  remove: (ep: Endpoint, id: string) => Promise<unknown>;
  /** Снимок обрезан по числу записей (живой — последние 200): можно дочитать раньше. */
  paged: boolean;
};

/** Во время встречи: `/live/chat*` (функции зовутся через стрелки — тесты их подменяют). */
export const LIVE_CHAT: ChatBackend = {
  get: (ep, limit) => (limit === undefined ? getChat(ep) : getChat(ep, limit)),
  post: (ep, msg) => postChat(ep, msg),
  click: (ep, id, label, clientId) => clickChat(ep, id, label, clientId),
  react: (ep, id, emoji, on) => reactChat(ep, id, emoji, on),
  confirm: (ep, id, allow, meeting) => confirmChat(ep, id, allow, meeting),
  revoke: (ep, id) => revokeChatGrant(ep, id),
  stop: (ep, id) => stopChat(ep, id),
  frequency: (ep, label) => setAgentFrequency(ep, label),
  profile: (ep, profile) => setAgentProfile(ep, profile),
  paste: (ep, blob, name) => pasteChatImage(ep, blob, name),
  attach: (ep, path) => attachChatFile(ep, path),
  remove: (ep, id) => removeChatAttachment(ep, id),
  paged: true,
};

/**
 * После встречи: чат записи `id`. `job` — идущая задача ответа (её снимает
 * «Стоп»); `onLoaded` — полный ответ `GET /recordings/{id}/chat` (прежние
 * подсказки, задача, включён ли чат), когда ленту перечитали.
 */
export function recordingChatBackend(id: string, job: () => Job | null,
  onLoaded?: (chat: RecordingChat) => void): ChatBackend {
  return {
    get: async (ep) => {
      const chat = await getRecordingChat(ep, id);
      onLoaded?.(chat);
      return { messages: chat.messages ?? [], seq: chat.seq ?? 0 };
    },
    post: async (ep, msg) => ({ id: (await continueChat(ep, id, msg)).message.id }),
    click: (ep, mid, label, clientId) => recordingChatClick(ep, id, mid, label, clientId),
    react: (ep, mid, emoji, on) => recordingChatReact(ep, id, mid, emoji, on),
    confirm: (ep, mid, allow, meeting) => recordingChatConfirm(ep, id, mid, allow, meeting),
    revoke: async () => undefined,
    stop: async (ep) => {
      const running = job();
      if (!running) throw new Error("ответ уже не готовится");
      await cancelJob(ep, running.id);
    },
    frequency: async () => undefined,
    profile: async () => undefined,
    paste: (ep, blob, name) => recordingChatPaste(ep, id, blob, name),
    attach: (ep, path) => recordingChatAttach(ep, id, path),
    remove: (ep, aid) => recordingChatRemove(ep, id, aid),
    paged: false,
  };
}
