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
 * `off`, но закреплённый вопрос объявляется и тогда). Клавиатура — «бегущий»
 * tabindex (ревью live-chat, M12): в порядке Tab одно сообщение ленты (по
 * умолчанию последнее) и его действия — кнопки агента, реакции, копирование,
 * источники; ↑ / ↓ / Home / End переходят между сообщениями. Так строка ввода
 * в долгой встрече не прячется за десятками кнопок. Реакции видны при
 * наведении и фокусе, поставленные — всегда.
 *
 * Реакции 👍 «Полезно», 👎 «Не по теме», ❓ «Поясни» (эмодзи — ключи протокола;
 * на экране — значки Lucide): в подсказке — что будет, при наведении и фокусе
 * рядом со значком — подпись (в узкой — только значок и подсказка). После нажатия 👍 / 👎 — заметка окна «Учту: …» (`chat.ack`),
 * гаснет в нажатую кнопку; ❓ — «Ассистент поясняет…», пока не пришло
 * пояснение. У пояснения — метка «пояснение» и ссылка «к сообщению …»:
 * щелчок — к поясняемому сообщению.
 *
 * Источники: документ, который упомянул агент (вложение чата или документ
 * базы знаний — `sources.ts`), — чип под сообщением; щелчок открывает его
 * (`open_material`). В узкой панели у чипа только значок.
 *
 * Узкая панель (`compact`): время сообщения — в подсказке, вложения
 * сообщения — счётчиком (скрепка и число; ревью live-chat, M7).
 *
 * Вид — Atlas Aurora (0.4): сообщение агента — карточка `.card.aurora-wash` со
 * знаком агента, его кнопки — `.filter` (aria-pressed), реакции, источники и
 * действия — кнопки Aurora (`Button`).
 */

import {
  BookOpen, CircleHelp, Copy, CornerDownRight, FileText, Image as ImageIcon, type LucideIcon, Paperclip, ThumbsDown, ThumbsUp, X,
} from "lucide-react";
import { type KeyboardEvent, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

import { plainMarkdown } from "../lib/agentRef";
import { clock } from "../lib/format";
import { Markdown } from "../lib/markdown";
import type { ChatMessage, ChatReaction } from "../lib/types";
import { AgentMark } from "../ui/AgentMark";
import { BADGE_CLASS } from "../ui/badge";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { IconButton } from "../ui/IconButton";
import { type FeedItem, type Outgoing, REACTIONS, isFinalAgent } from "./chatModel";
import type { Source } from "./sources";
import { type Chat, EXPLAINING } from "./useChat";
import "./chat.css";

/** Значки реакций (Lucide): ключ протокола — эмодзи, на экране — значок и подпись. */
const REACTION_ICON: Record<ChatReaction, LucideIcon> = { "👍": ThumbsUp, "👎": ThumbsDown, "❓": CircleHelp };

/** Насколько от низа ещё считается «внизу». */
const BOTTOM_SLACK_PX = 24;
/** Действия внутри сообщения ленты (их tabindex «бежит» вместе с сообщением). */
const ACTIONS = "button, a[href], input, select, textarea, [tabindex]:not(li)";
/** Сколько держится «Скопировано». */
export const COPIED_MS = 1500;
/** Сколько подсвечено сообщение, к которому перешли по «к сообщению …». */
const FLASH_MS = 1600;

/** Подсказка у строки «Ассистент хотел … — запрос заблокирован». */
export const GATE_TITLE = "Ассистент действует вне этой встречи только с вашего согласия — Meet заблокировал запрос без него";
/** Решение по карточке подтверждения — словом. */
export const CARD_DECIDED: Record<string, string> = {
  allow: "Разрешено один раз", allow_meeting: "Разрешено до конца встречи", deny: "Отклонено", timeout: "Время вышло — не выполнено",
  cancelled: "Отменено", expired: "Не дождались ответа — не выполнено",
};
/**
 * Карточка подтверждения Meet: что агент хочет выполнить — из настоящего вызова, не из его текста.
 * Резидент присылает вызов целиком (`args`, пробелы и переводы строк — видимыми пометками, невидимые
 * символы запрещены) и для длинного — начало и конец (`preview`, середина — пометкой «скрыто: …»),
 * так что хвост виден всегда. «Показать полностью» — по желанию. Кнопки: «Разрешить один раз»,
 * «Разрешать такое до конца встречи» (если Meet её предлагает) и «Отклонить» (Esc).
 */
function ConfirmCard({ m, chat, disabled, pinned = false }: {
  m: ChatMessage; chat: Chat; disabled: boolean; pinned?: boolean;
}) {
  const [full, setFull] = useState(false);
  const [busy, setBusy] = useState(false);
  const args = m.args ?? "";
  const open = pinned && !m.decision;
  const decide = async (allow: boolean, meeting = false) => {
    if (busy) return;
    setBusy(true);
    try { await chat.confirm(m.id, allow, meeting); } finally { setBusy(false); }
  };
  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    if (open && e.key === "Escape") { e.preventDefault(); e.stopPropagation(); void decide(false); }
  };
  return (
    <section className={`chat-card${open ? " chat-card--open" : ""}`} role="group"
      aria-label={`Ассистент хочет выполнить: ${m.title ?? m.tool ?? ""}`} onKeyDown={onKey}>
      <div className="chat-card__title">
        Ассистент хочет выполнить: <b>{m.title ?? m.tool}</b>
        {m.size && <span className="chat-card__size"> · {m.size}</span>}
      </div>
      {(m.warnings ?? []).map((w) => <div key={w} className="chat-card__warn" role="note">{w}</div>)}
      {args && <pre className="chat-card__args" dir="ltr">{m.preview && !full ? m.preview : args}</pre>}
      {m.preview && (
        <Button variant="link" className="chat-card__more" aria-expanded={full} onClick={() => setFull(!full)}>
          {full ? "Свернуть" : "Показать полностью"}
        </Button>
      )}
      {open ? (
        // «Разрешить один раз» — сильная без цвета; «до конца встречи» (шире всего) — тише; «Отклонить» (Esc) — контур.
        <div className="chat-card__actions">
          <Button variant="mono" size="xs" className="chat-card__allow" disabled={disabled || busy}
            onClick={() => void decide(true)}>
            Разрешить один раз
          </Button>
          {m.grant && (
            <Button variant="ghost" size="xs" className="chat-card__allow-meeting" disabled={disabled || busy}
              title={`Дальше до конца встречи без вопросов: ${m.grant.label}`} onClick={() => void decide(true, true)}>
              Разрешать такое до конца встречи
            </Button>
          )}
          <Button size="xs" className="chat-card__deny" disabled={disabled || busy} onClick={() => void decide(false)}>
            Отклонить
          </Button>
        </div>
      ) : (
        <div className="chat-card__done">{m.decision ? CARD_DECIDED[m.decision] ?? m.decision : "Ждёт вашего решения — над лентой"}</div>
      )}
    </section>
  );
}

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
    <IconButton icon={Copy} size="xs" label={copied ? "Скопировано" : "Копировать"} className="chat-msg__copy"
      onClick={() => {
        if (!navigator.clipboard) return;
        void navigator.clipboard.writeText(text).then(() => setCopied(true), () => {});
      }} />
  );
}

/** Кнопки сообщения агента (`.filter` Aurora): нажатие — ваш ответ; нажатая — aria-pressed, остальные гаснут. */
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
          <button key={label} type="button" className="filter" aria-pressed={on}
            aria-disabled={off || undefined}
            title={on ? "Вы ответили этим" : used !== null ? "Уже ответили" : undefined}
            onClick={() => { if (!off) void chat.click(m.id, label); }}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

function Reactions({ m, chat, disabled, compact }: { m: ChatMessage; chat: Chat; disabled: boolean; compact: boolean }) {
  return (
    <span className="chat-react" role="group" aria-label="Реакция">
      {REACTIONS.map(({ emoji, label, hint }) => {
        const on = !!m.reactions?.[emoji];
        return (
          <Button key={emoji} variant="ghost" size="xs" icon={REACTION_ICON[emoji]}
            className={`chat-react__btn${on ? " is-on" : ""}${compact ? " btn--icon" : ""}`} aria-pressed={on}
            aria-label={label} title={hint} disabled={disabled}
            onClick={() => void chat.react(m.id, emoji)}>
            {!compact && <span className="chat-react__label" aria-hidden="true">{label}</span>}
          </Button>
        );
      })}
    </span>
  );
}

/** Короткая цитата сообщения для ссылки «к сообщению «…»». */
const QUOTE_MAX = 40;
function quoteOf(m: ChatMessage | undefined): string {
  const flat = plainMarkdown(m?.text ?? "").split(/\s+/).join(" ").trim();
  if (!flat) return "";
  return `«${flat.length > QUOTE_MAX ? `${flat.slice(0, QUOTE_MAX - 1).trimEnd()}…` : flat}»`;
}

/** Пояснение (ответ на ❓): метка и ссылка к поясняемому сообщению. */
function ExplainsRef({ id, chat, onShow }: { id: string; chat: Chat; onShow: (id: string) => void }) {
  const quote = quoteOf(chat.state.byId[id]);
  return (
    <>
      <span className={`${BADGE_CLASS.plain} chat-msg__tag`}>пояснение</span>
      <button type="button" className="chat-msg__ref" title="Показать сообщение, которое поясняет ассистент"
        onClick={() => onShow(id)}>
        к сообщению{quote ? ` ${quote}` : ""}
      </button>
    </>
  );
}

/** Документы, которые упомянуло сообщение: щелчок — открыть. */
function Sources({ sources, chat, compact }: { sources: Source[]; chat: Chat; compact: boolean }) {
  if (!sources.length) return null;
  return (
    <div className="chat-msg__sources" role="group" aria-label="Источники">
      {sources.map((s) => (
        <Button key={s.key} size="xs" icon={s.kind === "image" ? ImageIcon : s.kind === "kb" ? BookOpen : FileText}
          className={`chat-src${compact ? " btn--icon" : ""}`} aria-label={`Источник: ${s.label}`}
          title={`Открыть «${s.label}»`} onClick={() => void chat.open(s)}>
          {!compact && <span className="chat-src__name">{s.label}</span>}
        </Button>
      ))}
    </div>
  );
}

function AgentMessage({ m, chat, onTime, onShow, compact, disabled }: {
  m: ChatMessage; chat: Chat; onTime?: (t: number) => void; onShow: (id: string) => void; compact: boolean; disabled: boolean;
}) {
  const writing = m.status === "writing";
  const partial = chat.state.partial[m.id];
  const text = m.text ?? "";
  const time = typeof m.t === "number" ? clock(m.t) : null;
  const reacted = REACTIONS.some((r) => m.reactions?.[r.emoji]);
  const ack = chat.ack(m.id);
  const explaining = !disabled && !writing && chat.explaining(m.id);
  return (
    // Карточка Aurora с отсветом сияния (вывод ИИ) и знаком агента; пока пишет — знак «пишет».
    <li className={`chat-msg chat-msg--agent card aurora-wash${m.pin ? " is-pin" : ""}${writing ? " is-writing" : ""}${reacted ? " has-reaction" : ""}`}
      data-id={m.id} data-key={m.id} aria-busy={writing || undefined} title={compact && time ? time : undefined}>
      <div className="chat-msg__head">
        <AgentMark state={writing ? "write" : "rest"} size={14} />
        <span className="chat-msg__who">Ассистент</span>
        {time && !compact && <span className="chat-msg__time num">{time}</span>}
        {m.pin && <span className={`${BADGE_CLASS.run} badge--plain chat-msg__tag`}>вопрос вам</span>}
        {typeof m.explains === "string" && <ExplainsRef id={m.explains} chat={chat} onShow={onShow} />}
        {writing && partial?.trim() && <span className="chat-msg__writing">пишет…</span>}
      </div>
      {writing ? (
        partial?.trim()
          ? <div className="chat-msg__text chat-msg__text--streaming">{partial}</div>
          : <div className="chat-typing"><span className="chat-typing__dots" aria-hidden="true" />Пишет…</div>
      ) : text.trim() ? (
        <Markdown source={text} className="chat-msg__text" onTime={onTime} />
      ) : null}
      {m.status === "cancelled" && <div className="chat-msg__note">Остановлено</div>}
      {m.status === "failed" && <div className="chat-msg__error">{m.error || "Ассистент не смог ответить"}</div>}
      {!writing && <Sources sources={chat.sources(m)} chat={chat} compact={compact} />}
      {!writing && <AgentButtons m={m} chat={chat} disabled={disabled} />}
      {!writing && text.trim() && (
        <div className="chat-msg__tools">
          <Reactions m={m} chat={chat} disabled={disabled} compact={compact} />
          <CopyButton text={plainMarkdown(text)} />
        </div>
      )}
      {/* Видимые заметки; диктору их объявляет постоянная live-область ленты (`chat.announce`). */}
      {ack && <div key={ack} className="chat-msg__ack">{ack}</div>}
      {explaining && (
        <div className="chat-msg__pending">
          <span className="chat-typing__dots" aria-hidden="true" />{EXPLAINING}
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

function UserMessage({ m, chat, out, compact = false }: { m?: ChatMessage; chat: Chat; out?: Outgoing; compact?: boolean }) {
  const text = m?.text ?? out?.text ?? "";
  const atts = m?.attachments ?? out?.attachments ?? [];
  const time = typeof m?.t === "number" ? clock(m.t) : null;
  const names = atts.map((id) => chat.attachment(id)?.name || "вложение").join(", ");
  return (
    <li className={`chat-msg chat-msg--user${out ? ` is-${out.state}` : ""}`} data-id={m?.id}
      data-key={m?.id ?? `out:${out?.client_id}`} title={compact && time ? time : undefined}>
      {m?.via === "button" && <div className="chat-msg__via">кнопка</div>}
      {m?.via === "reaction" && <div className="chat-msg__via">реакция</div>}
      {text && <div className="chat-msg__text chat-msg__text--plain">{text}</div>}
      {atts.length > 0 && (compact ? (
        <span className="chat-msg__att-count" title={names} aria-label={`Вложения: ${names}`}>
          <Icon as={Paperclip} size="sm" />{atts.length}
        </span>
      ) : (
        <div className="chat-msg__atts">{atts.map((id) => <AttachmentChip key={id} id={id} chat={chat} />)}</div>
      ))}
      {time && !compact && <span className="chat-msg__time num">{time}</span>}
      {out?.state === "sending" && <div className="chat-msg__note">отправляется…</div>}
      {out?.state === "failed" && (
        <div className="chat-msg__error" role="alert">
          Не отправлено: {out.error}{" "}
          <Button variant="link" onClick={() => void chat.retry(out.client_id)}>Повторить</Button>
        </div>
      )}
    </li>
  );
}

function Item({ it, chat, onTime, onShow, compact, disabled }: {
  it: FeedItem; chat: Chat; onTime?: (t: number) => void; onShow: (id: string) => void; compact: boolean; disabled: boolean;
}) {
  if (it.type === "outgoing") return <UserMessage chat={chat} out={it.out} compact={compact} />;
  const m = it.message;
  if (m.kind === "agent") {
    return <AgentMessage m={m} chat={chat} onTime={onTime} onShow={onShow} compact={compact} disabled={disabled} />;
  }
  if (m.kind === "user") return <UserMessage m={m} chat={chat} compact={compact} />;
  if (m.kind === "meeting") {
    return <li className="chat-divider" data-id={m.id} data-key={m.id}><span>{m.text || "встреча"}</span></li>;
  }
  if (m.card === "confirm") {
    return <li className="chat-sys chat-sys--card" data-id={m.id} data-key={m.id}><ConfirmCard m={m} chat={chat} disabled={disabled} /></li>;
  }
  if (m.gate) {
    // Ворота согласия заблокировали вызов агента (0.3.7): та же тихая строка, с пояснением.
    return <li className="chat-sys chat-sys--gate" data-id={m.id} data-key={m.id} title={GATE_TITLE}>{m.text}</li>;
  }
  return <li className="chat-sys" data-id={m.id} data-key={m.id}>{m.text}</li>;
}

/** Закреплённый вопрос агента — над лентой (макет MeetLive): метка, «Показать в ленте», «×»; текст и кнопки агента. */
function Pinned({ m, chat, onTime, onShow, onHide, disabled }: {
  m: ChatMessage; chat: Chat; onTime?: (t: number) => void; onShow: () => void; onHide: () => void; disabled: boolean;
}) {
  return (
    <section className="chat-pin" aria-label="Вопрос вам">
      <div className="chat-pin__head">
        <span className={`${BADGE_CLASS.run} badge--plain chat-pin__badge`}><Icon as={CircleHelp} size="sm" />Вопрос вам</span>
        <span className="chat-pin__gap" />
        <Button variant="ghost" size="xs" icon={CornerDownRight} onClick={onShow}>Показать в ленте</Button>
        <IconButton icon={X} size="xs" label="Убрать из закреплённых" onClick={onHide} />
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
  const list = useRef<HTMLOListElement>(null);
  /** Сообщение, которое сейчас в порядке Tab (null — последнее). */
  const [active, setActive] = useState<string | null>(null);
  const follow = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  const seen = useRef<Set<string> | null>(null);
  const ids = agentIds(chat.items);
  const outCount = chat.state.outbox.length;
  const last = chat.items.at(-1);
  const sig = `${chat.items.length}:${last?.type === "message" ? `${last.message.id}:${last.message.status}:${last.message.text?.length ?? 0}` : "o"}:${
    Object.values(chat.state.partial).reduce((n, t) => n + t.length, 0)}`;

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

  // «Бегущий» tabindex: в порядке Tab — одно сообщение и его действия. Через DOM:
  // действия рисуют и Markdown (таймкоды), и вложенные компоненты.
  const rows = () => Array.from(list.current?.querySelectorAll<HTMLElement>(":scope > li[data-key]") ?? []);
  // Только когда сменились сообщения, их состояние (появились кнопки, реакции) или
  // выбранное: не на каждый кусок текста ответа (ревью after-chat, M7).
  const shape = chat.items.map((it) => (it.type === "message"
    ? `${it.message.id}:${it.message.status ?? ""}:${it.message.buttons?.length ?? 0}:${it.message.text ? 1 : 0}`
    : `o:${it.out.client_id}:${it.out.state}`)).join("|");
  useLayoutEffect(() => {
    const all = rows();
    if (!all.length) return;
    const key = active && all.some((li) => li.dataset.key === active) ? active : all[all.length - 1]!.dataset.key;
    for (const li of all) {
      const on = li.dataset.key === key;
      li.tabIndex = on ? 0 : -1;
      for (const el of li.querySelectorAll<HTMLElement>(ACTIONS)) el.tabIndex = on ? 0 : -1;
    }
  }, [shape, active]);
  const onListKey = (e: KeyboardEvent<HTMLOListElement>) => {
    const step = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : e.key === "Home" ? -Infinity
      : e.key === "End" ? Infinity : 0;
    if (!step || e.altKey || e.ctrlKey || e.metaKey) return;
    const row = (e.target as Element).closest<HTMLElement>("li[data-key]");
    if (!row) return;
    const all = rows();
    const at = all.indexOf(row);
    const next = all[step === Infinity ? all.length - 1 : step === -Infinity ? 0 : Math.max(0, Math.min(all.length - 1, at + step))];
    if (!next) return;
    e.preventDefault();
    setActive(next.dataset.key ?? null);
    next.tabIndex = 0;
    next.focus();
    next.scrollIntoView?.({ block: "nearest" });
  };
  const onListFocus = (e: { target: EventTarget }) => {
    const row = (e.target as Element).closest?.<HTMLElement>("li[data-key]");
    if (row?.dataset.key && row.dataset.key !== active) setActive(row.dataset.key);
  };

  const unread = atBottom || quiet || !seen.current ? 0 : ids.filter((id) => !seen.current!.has(id)).length;
  const pinned = chat.pinned;
  const hidePin = (id: string) => {
    chat.hidePin(id);
    // Кнопка «×» ушла вместе с карточкой — фокус в строку ввода, а не на <body>.
    box.current?.closest(".chat-ws__main")?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
  };
  // «к сообщению …» у пояснения: к поясняемому сообщению — прокрутить, подсветить, фокус.
  const showMessage = (id: string) => {
    const row = list.current?.querySelector<HTMLElement>(`:scope > li[data-id="${id}"]`);
    if (!row) return;
    row.scrollIntoView?.({ block: "center" });
    setActive(row.dataset.key ?? null);
    row.tabIndex = 0;
    row.focus({ preventScroll: true });
    row.classList.add("is-flash");
    setTimeout(() => row.classList.remove("is-flash"), FLASH_MS);
  };
  // «Показать в ленте» у закреплённого вопроса — как «к сообщению …»: прокрутить, подсветить, фокус.
  const showPinned = () => { if (pinned) showMessage(pinned.id); };

  return (
    <div className={`chat${compact ? " chat--compact" : ""}`}>
      {chat.cards.length > 0 && (
        // Карточки, которые ждут решения, — над лентой: их видно и при прокрученной вверх ленте.
        <div className="chat-cards" role="region" aria-label="Подтверждение действия">
          {chat.cards.map((m) => <ConfirmCard key={m.id} m={m} chat={chat} disabled={disabled} pinned />)}
        </div>
      )}
      {/* Новая карточка объявляется и при «Не отвлекать»: ассистент ждёт решения. */}
      {chat.cards.length > 0 && (
        <span className="sr-only" role="alert">
          {`Ассистент ждёт подтверждения: ${chat.cards[chat.cards.length - 1]!.title ?? ""}`}
        </span>
      )}
      {pinned && (
        <Pinned m={pinned} chat={chat} onTime={onTime} onShow={showPinned} onHide={() => hidePin(pinned.id)}
          disabled={disabled} />
      )}
      {/* «Не отвлекать» глушит ленту, но вопрос к вам объявляется и тогда (ревью live-chat, M15). */}
      {quiet && <span className="sr-only" role="status">{pinned ? `Вопрос вам: ${plainMarkdown(pinned.text ?? "")}` : ""}</span>}
      {/* Отклик на реакцию — в одной постоянной области: вставленную сразу с текстом диктор часто молчит. */}
      <span className="sr-only" role="status" aria-live="polite" data-chat-announce="">{chat.announce}</span>
      <div ref={box} className="chat__scroll" onScroll={onScroll}>
        {chat.more && (
          <button type="button" className="chat__more" onClick={() => void chat.more?.()}>
            Показать более ранние сообщения
          </button>
        )}
        <ol ref={list} className="chat__list" role="log" aria-live={quiet ? "off" : "polite"} aria-relevant="additions"
          aria-label="Чат с ассистентом" onKeyDown={onListKey} onFocus={onListFocus}>
          {chat.items.length === 0 && (
            <li className="chat__empty muted">
              {empty ?? "Ассистент слушает встречу и напишет, когда будет что сказать. Можно написать ему и самому."}
            </li>
          )}
          {chat.items.map((it) => (
            <Item key={it.type === "message" ? it.message.id : `out:${it.out.client_id}`} it={it} chat={chat}
              onTime={onTime} onShow={showMessage} compact={compact} disabled={disabled} />
          ))}
        </ol>
      </div>
      {unread > 0 && (
        <Button variant="deep" size="xs" className="chat__new" onClick={toBottom}>
          ↓ {unread} {unread === 1 ? "новое" : "новых"}
        </Button>
      )}
    </div>
  );
}
