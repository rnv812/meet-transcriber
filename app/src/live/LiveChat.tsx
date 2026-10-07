/**
 * Лента чата с агентом-участником: его сообщения (Markdown, таймкоды — к
 * моменту в расшифровке), его кнопки, реакции 👍 👎 ❓, копирование; ваши
 * сообщения с вложениями; строки встречи и системы. Сверху — закреплённый
 * вопрос к вам (`pin`), пока вы после него ничего не написали.
 *
 * Лента следит за низом, пока человек сам не прокрутил вверх; тогда новые
 * сообщения агента копятся в плашке «↓ N новых». Своё отправленное сообщение
 * всегда возвращает к низу.
 *
 * Доступность: лента — `role="log"` (`aria-live="polite"`, в «Не отвлекать» —
 * `off`); реакции и кнопки — обычные кнопки в порядке Tab (реакции видны при
 * наведении и фокусе, поставленные — всегда).
 */

import { Copy, FileText, Image as ImageIcon, X } from "lucide-react";
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

import { plainMarkdown } from "../lib/agentRef";
import { clock } from "../lib/format";
import { Markdown } from "../lib/markdown";
import type { ChatMessage } from "../lib/types";
import { Icon } from "../ui/Icon";
import { IconButton } from "../ui/IconButton";
import { type FeedItem, type Outgoing, REACTIONS, isFinalAgent } from "./chatModel";
import type { Chat } from "./useChat";
import "./chat.css";

/** Насколько от низа ещё считается «внизу». */
const BOTTOM_SLACK_PX = 24;
/** Сколько держится «Скопировано». */
export const COPIED_MS = 1500;

/** Новые готовые сообщения агента в ленте (их считает «↓ N новых»). */
function agentIds(items: FeedItem[]): string[] {
  const ids: string[] = [];
  for (const it of items) if (it.type === "message" && isFinalAgent(it.message)) ids.push(it.message.id);
  return ids;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(t);
  }, [copied]);
  return (
    <IconButton icon={Copy} size="sm" label={copied ? "Скопировано" : "Копировать"} className="chat-msg__copy"
      onClick={() => {
        if (!navigator.clipboard) return;
        void navigator.clipboard.writeText(text).then(() => setCopied(true), () => {});
      }} />
  );
}

/** Кнопки сообщения агента: нажатие — ваш ответ; после — видно, какую нажали. */
function AgentButtons({ m, chat, disabled }: { m: ChatMessage; chat: Chat; disabled: boolean }) {
  const buttons = (m.buttons ?? []).filter((b) => typeof b === "string" && b.trim()).slice(0, 3);
  if (!buttons.length) return null;
  const used = chat.used(m.id);
  return (
    <div className="chat-msg__buttons" role="group" aria-label="Ответить ассистенту">
      {buttons.map((label) => {
        const on = used === label;
        // aria-disabled, а не disabled: фокус остаётся на нажатой кнопке.
        const off = used !== null || disabled;
        return (
          <button key={label} type="button" className={`chat-btn${on ? " is-used" : ""}`} aria-pressed={on}
            aria-disabled={off || undefined}
            title={on ? "Вы ответили этим" : used !== null ? "Уже ответили" : undefined}
            onClick={() => { if (!off) void chat.click(m.id, label); }}>
            {on && <span aria-hidden="true">✓ </span>}{label}
          </button>
        );
      })}
    </div>
  );
}

function Reactions({ m, chat, disabled }: { m: ChatMessage; chat: Chat; disabled: boolean }) {
  return (
    <span className="chat-react" role="group" aria-label="Реакция">
      {REACTIONS.map(({ emoji, label }) => {
        const on = !!m.reactions?.[emoji];
        return (
          <button key={emoji} type="button" className={`chat-react__btn${on ? " is-on" : ""}`} aria-pressed={on}
            aria-label={`${emoji} ${label}`} title={label} disabled={disabled}
            onClick={() => void chat.react(m.id, emoji)}>
            <span aria-hidden="true">{emoji}</span>
          </button>
        );
      })}
    </span>
  );
}

function AgentMessage({ m, chat, onTime, compact, disabled }: {
  m: ChatMessage; chat: Chat; onTime?: (t: number) => void; compact: boolean; disabled: boolean;
}) {
  const writing = m.status === "writing";
  const partial = chat.state.partial[m.id];
  const text = m.text ?? "";
  const time = typeof m.t === "number" ? clock(m.t) : null;
  const reacted = REACTIONS.some((r) => m.reactions?.[r.emoji]);
  return (
    <li className={`chat-msg chat-msg--agent${m.pin ? " is-pin" : ""}${writing ? " is-writing" : ""}${reacted ? " has-reaction" : ""}`}
      data-id={m.id} aria-busy={writing || undefined}>
      <div className="chat-msg__head">
        <span className="chat-msg__who">Ассистент</span>
        {time && !compact && <span className="chat-msg__time num">{time}</span>}
        {m.pin && <span className="chat-msg__tag">вопрос вам</span>}
      </div>
      {writing ? (
        partial?.trim()
          ? <div className="chat-msg__text chat-msg__text--streaming">{partial}</div>
          : <div className="chat-typing" role="status"><span className="chat-typing__dots" aria-hidden="true" />Пишет…</div>
      ) : text.trim() ? (
        <Markdown source={text} className="chat-msg__text" onTime={onTime} />
      ) : null}
      {m.status === "cancelled" && <div className="chat-msg__note">Остановлено</div>}
      {m.status === "failed" && <div className="chat-msg__error">{m.error || "Ассистент не смог ответить"}</div>}
      {!writing && <AgentButtons m={m} chat={chat} disabled={disabled} />}
      {!writing && text.trim() && (
        <div className="chat-msg__tools">
          <Reactions m={m} chat={chat} disabled={disabled} />
          <CopyButton text={plainMarkdown(text)} />
        </div>
      )}
    </li>
  );
}

function AttachmentChip({ id, chat }: { id: string; chat: Chat }) {
  const a = chat.attachment(id);
  const preview = chat.preview(id);
  const name = a?.name || "вложение";
  const failed = a?.status === "failed";
  const note = failed ? `не разобрано${a?.error ? `: ${a.error}` : ""}` : a?.note;
  return (
    <span className={`chat-att${failed ? " is-failed" : ""}`} title={note || name}>
      {preview && a?.type === "image"
        ? <img className="chat-att__thumb" src={preview} alt={name} />
        : <Icon as={a?.type === "image" ? ImageIcon : FileText} size="sm" />}
      <span className="chat-att__name">{name}</span>
      {note && <span className="chat-att__note">{note}</span>}
    </span>
  );
}

function UserMessage({ m, chat, out }: { m?: ChatMessage; chat: Chat; out?: Outgoing }) {
  const text = m?.text ?? out?.text ?? "";
  const atts = m?.attachments ?? out?.attachments ?? [];
  const time = typeof m?.t === "number" ? clock(m.t) : null;
  return (
    <li className={`chat-msg chat-msg--user${out ? ` is-${out.state}` : ""}`} data-id={m?.id}>
      {m?.via === "button" && <div className="chat-msg__via">кнопка</div>}
      {text && <div className="chat-msg__text chat-msg__text--plain">{text}</div>}
      {atts.length > 0 && (
        <div className="chat-msg__atts">{atts.map((id) => <AttachmentChip key={id} id={id} chat={chat} />)}</div>
      )}
      {time && <span className="chat-msg__time num">{time}</span>}
      {out?.state === "sending" && <div className="chat-msg__note">отправляется…</div>}
      {out?.state === "failed" && (
        <div className="chat-msg__error" role="alert">
          Не отправлено: {out.error}{" "}
          <button type="button" className="link" onClick={() => void chat.retry(out.client_id)}>Повторить</button>
        </div>
      )}
    </li>
  );
}

function Item({ it, chat, onTime, compact, disabled }: {
  it: FeedItem; chat: Chat; onTime?: (t: number) => void; compact: boolean; disabled: boolean;
}) {
  if (it.type === "outgoing") return <UserMessage chat={chat} out={it.out} />;
  const m = it.message;
  if (m.kind === "agent") return <AgentMessage m={m} chat={chat} onTime={onTime} compact={compact} disabled={disabled} />;
  if (m.kind === "user") return <UserMessage m={m} chat={chat} />;
  if (m.kind === "meeting") return <li className="chat-divider" data-id={m.id}><span>{m.text || "встреча"}</span></li>;
  return <li className="chat-sys" data-id={m.id}>{m.text}</li>;
}

/** Закреплённый вопрос агента — над лентой; щелчок по тексту — к сообщению в ленте. */
function Pinned({ m, chat, onTime, onShow, onHide, disabled }: {
  m: ChatMessage; chat: Chat; onTime?: (t: number) => void; onShow: () => void; onHide: () => void; disabled: boolean;
}) {
  return (
    <section className="chat-pin" aria-label="Вопрос вам">
      <div className="chat-pin__head">
        <button type="button" className="chat-pin__title" onClick={onShow} title="Показать в ленте">Вопрос вам</button>
        <IconButton icon={X} size="sm" label="Убрать из закреплённых" onClick={onHide} />
      </div>
      <Markdown source={m.text ?? ""} className="chat-pin__text" onTime={onTime} />
      <AgentButtons m={m} chat={chat} disabled={disabled} />
    </section>
  );
}

export function LiveChat({ chat, onTime, quiet = false, compact = false, disabled = false, empty }: {
  chat: Chat;
  onTime?: (t: number) => void;
  /** «Не отвлекать»: лента не объявляет новое и не считает его. */
  quiet?: boolean;
  /** Узкая панель: без времени у сообщений. */
  compact?: boolean;
  /** Писать нельзя (агент выключен, запись кончается): кнопки и реакции недоступны. */
  disabled?: boolean;
  /** Текст пустой ленты. */
  empty?: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  const seen = useRef<Set<string> | null>(null);
  const [hidden, setHidden] = useState<string | null>(null);
  const ids = agentIds(chat.items);
  const outCount = chat.state.outbox.length;
  const last = chat.items.at(-1);
  const sig = `${chat.items.length}:${last?.type === "message" ? `${last.message.id}:${last.message.status}:${last.message.text?.length ?? 0}` : "o"}:${
    last?.type === "message" ? chat.state.partial[last.message.id]?.length ?? 0 : 0}`;

  const toBottom = () => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
    follow.current = true;
    seen.current = null;
    setAtBottom(true);
  };

  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK_PX;
    follow.current = bottom;
    if (bottom) seen.current = null;
    else if (seen.current === null) seen.current = new Set(ids);
    setAtBottom(bottom);
  };

  useLayoutEffect(() => {
    const el = box.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [sig]);

  // Своё сообщение — всегда к низу: человек ждёт его увидеть.
  const prevOut = useRef(outCount);
  useLayoutEffect(() => {
    if (outCount > prevOut.current) toBottom();
    prevOut.current = outCount;
  }, [outCount]);

  // Ленту сузили или сделали ниже: следящая остаётся внизу.
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(() => { if (follow.current) el.scrollTop = el.scrollHeight; });
    watch.observe(el);
    return () => watch.disconnect();
  }, []);

  const unread = atBottom || quiet || !seen.current ? 0 : ids.filter((id) => !seen.current!.has(id)).length;
  const pinned = chat.pinned && chat.pinned.id !== hidden ? chat.pinned : null;
  const showPinned = () => {
    if (!pinned) return;
    const row = box.current?.querySelector<HTMLElement>(`[data-id="${pinned.id}"]`);
    row?.scrollIntoView?.({ block: "center" });
  };

  return (
    <div className={`chat${compact ? " chat--compact" : ""}`}>
      {pinned && (
        <Pinned m={pinned} chat={chat} onTime={onTime} onShow={showPinned} onHide={() => setHidden(pinned.id)}
          disabled={disabled} />
      )}
      <div ref={box} className="chat__scroll" onScroll={onScroll}>
        <ol className="chat__list" role="log" aria-live={quiet ? "off" : "polite"} aria-relevant="additions"
          aria-label="Чат с ассистентом">
          {chat.items.length === 0 && (
            <li className="chat__empty muted">
              {empty ?? "Ассистент слушает встречу и напишет, когда будет что сказать. Можно написать ему и самому."}
            </li>
          )}
          {chat.items.map((it) => (
            <Item key={it.type === "message" ? it.message.id : `out:${it.out.client_id}`} it={it} chat={chat}
              onTime={onTime} compact={compact} disabled={disabled} />
          ))}
        </ol>
      </div>
      {unread > 0 && (
        <button type="button" className="chat__new" onClick={toBottom}>
          ↓ {unread} {unread === 1 ? "новое" : "новых"}
        </button>
      )}
    </div>
  );
}
