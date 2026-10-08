/**
 * Лента чата с агентом-участником: его сообщения (Markdown, таймкоды — к
 * моменту в расшифровке), его кнопки, реакции 👍 👎 ❓, копирование; ваши
 * сообщения с вложениями; строки встречи и системы. Сверху — закреплённый
 * вопрос к вам (`pin`), пока вы после него ничего не написали: одной строкой
 * (бейдж, текст с многоточием — щелчок показывает целиком, «Показать в ленте», «×»).
 *
 * Карточка подтверждения Meet («Ассистент хочет выполнить: …») решается прямо в
 * ленте, своими кнопками — и во время встречи, и после неё (вкладка «Ассистент»
 * карточки записи): отдельной области над лентой нет. Кнопки ждущей карточки
 * всегда в порядке Tab, Esc в ней — «Отклонить».
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
 * Вид — Atlas Aurora (0.4): сообщение агента — плоская карточка (0.5) со
 * знаком агента, его кнопки — `.filter` (aria-pressed), реакции, источники и
 * действия — кнопки Aurora (`Button`).
 */

import {
  BookOpen, CircleHelp, Copy, CornerDownRight, FileText, Image as ImageIcon, Info, type LucideIcon, Mic, Paperclip,
  ThumbsDown, ThumbsUp, X,
} from "lucide-react";
import { type KeyboardEvent, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

import { plainMarkdown } from "../lib/agentRef";
import { clock } from "../lib/format";
import { Markdown } from "../lib/markdown";
import { inTauri, openFolder, openMaterial, openUserPath, trayPanelOpen } from "../lib/shell";
import type { SettingsLinks } from "../lib/markdown";
import { MENU, sectionTitle, type SectionId } from "../features/settings/settingsIndex";
import type { PathActions } from "../ui/PathLink";
import type { ChatMessage, ChatReaction } from "../lib/types";
import { AgentMark } from "../ui/AgentMark";
import { BADGE_CLASS } from "../ui/badge";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { IconButton } from "../ui/IconButton";
import { Lightbox } from "../ui/Lightbox";
import { Tip } from "../ui/Tip";
import { ConfirmCard } from "./ConfirmCard";
import { type FeedItem, type Outgoing, REACTIONS, type ToolItem, isFinalAgent } from "./chatModel";
import type { Source } from "./sources";
import { ToolRows } from "./ToolRows";
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
/** Пути в ответах ассистента (0.5): открыть или показать в папке — оболочкой. */
const PATH_ACTIONS: PathActions = {
  open: (path) => openUserPath(path, false),
  reveal: (path) => openUserPath(path, true),
};
/** Ссылки `meet://settings/раздел` в ответах (0.5): кнопка открывает раздел настроек главного окна. */
const SETTINGS_LINKS: SettingsLinks = {
  open: (section) => { void trayPanelOpen({ section }).catch(() => {}); },
  title: (section) => (MENU.some((m) => m.id === section) ? sectionTitle(section as SectionId) : null),
};

/** Подсказка у строки «Ассистент хотел … — запрос заблокирован». */
export const GATE_TITLE = "Ассистент действует вне этой встречи только с вашего согласия — Meet заблокировал запрос без него";
export { CARD_DECIDED } from "./ConfirmCard";

/** Новые готовые сообщения агента и его карточки подтверждения в ленте — и в строках вызовов (их считает «↓ N новых»). */
function agentIds(items: FeedItem[]): string[] {
  const ids: string[] = [];
  for (const it of items) {
    if (it.type !== "message") continue;
    if (isFinalAgent(it.message) || it.message.card === "confirm") ids.push(it.message.id);
    for (const t of it.tools ?? []) if (t.card) ids.push(t.card.id);
  }
  return ids;
}

/**
 * Ответ слэш-команды Meet (0.4, `card: "command"`): список команд (`/help`), MCP-серверы (`/mcp` —
 * у сбойных «Переподключить»), «Нет команды … — /help» с «Отправить как текст» (`//…`), иначе — текст.
 */
function CommandLine({ m, chat, disabled }: { m: ChatMessage; chat: Chat; disabled: boolean }) {
  const items = Array.isArray(m.items) ? m.items : [];
  const servers = Array.isArray(m.servers) ? m.servers : [];
  const error = m.level === "error";
  return (
    <div className={`chat-cmd${error ? " chat-cmd--error" : ""}`} role={error ? "alert" : undefined}>
      {m.command && <span className="chat-cmd__name">/{m.command}</span>}
      {items.length > 0 ? (
        <ul className="chat-cmd__list">
          {items.map((c) => (
            <li key={`${c.source}:${c.name}`}>
              <code className="chat-cmd__code">/{c.name}{c.hint ? ` ${c.hint}` : ""}</code>
              {c.description && <span className="chat-cmd__desc">{c.description}</span>}
              {c.source !== "meet" && (
                <span className={`${BADGE_CLASS.plain} chat-cmd__src`}>{c.source === "skill" ? "навык" : "CLI"}</span>
              )}
            </li>
          ))}
        </ul>
      ) : servers.length > 0 ? (
        <>
          <ul className="chat-cmd__list">
            {servers.map((s) => (
              <li key={s.name} className={`chat-cmd__server is-${s.status}`}>
                <code className="chat-cmd__code">{s.name}</code>
                <span className="chat-cmd__desc">{SERVER_STATUS[s.status] ?? s.status}{s.error ? `: ${s.error}` : ""}</span>
                {s.status === "failed" && (
                  <Button size="xs" disabled={disabled} onClick={() => void chat.send(`/mcp reconnect ${s.name}`)}>
                    Переподключить
                  </Button>
                )}
              </li>
            ))}
          </ul>
          {extraLines(m.text ?? "", servers) && <div className="chat-cmd__text">{extraLines(m.text ?? "", servers)}</div>}
        </>
      ) : (
        <div className="chat-cmd__text">{m.text}</div>
      )}
      {typeof m.unknown === "string" && m.unknown && (
        <Button variant="link" disabled={disabled} onClick={() => void chat.send(`/${m.unknown}`)}>
          Отправить как текст
        </Button>
      )}
    </div>
  );
}

/** Состояние MCP-сервера словом (как у ассистента). */
const SERVER_STATUS: Record<string, string> = {
  connected: "подключён", failed: "ошибка", "needs-auth": "нужен вход — claude /mcp в терминале",
  pending: "подключается", disabled: "выключен",
};
const STATUS_WORDS = ["подключён", "ошибка", "нужен вход", "подключается", "выключен"];

/** Строки ответа `/mcp`, которых нет в таблице серверов (итог переподключения, пояснения). */
function extraLines(text: string, servers: { name: string }[]): string {
  return text.split("\n").filter((line) => !servers.some((s) => STATUS_WORDS.some((w) => line.startsWith(`${s.name} — ${w}`))))
    .join("\n").trim();
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
          <Tip key={label} content={on ? "Вы ответили этим" : used !== null ? "Уже ответили" : undefined}>
            <button type="button" className="filter" aria-pressed={on} aria-disabled={off || undefined}
              onClick={() => { if (!off) void chat.click(m.id, label); }}>
              {label}
            </button>
          </Tip>
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
          <Tip key={emoji} content={hint}>
            <Button variant="ghost" size="sm" icon={REACTION_ICON[emoji]}
              className={`chat-react__btn${on ? " is-on" : ""}${compact ? " btn--icon" : ""}`} aria-pressed={on}
              aria-label={label} disabled={disabled}
              onClick={() => void chat.react(m.id, emoji)}>
              {!compact && <span className="chat-react__label" aria-hidden="true">{label}</span>}
            </Button>
          </Tip>
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
      <Tip content="Показать сообщение, которое поясняет ассистент">
        <button type="button" className="chat-msg__ref" onClick={() => onShow(id)}>
          к сообщению{quote ? ` ${quote}` : ""}
        </button>
      </Tip>
    </>
  );
}

/** Документы, которые упомянуло сообщение: щелчок — открыть. */
function Sources({ sources, chat, compact }: { sources: Source[]; chat: Chat; compact: boolean }) {
  if (!sources.length) return null;
  return (
    <div className="chat-msg__sources" role="group" aria-label="Источники">
      {sources.map((s) => (
        <Tip key={s.key} content={`Открыть «${s.label}»`}>
          <Button size="sm" icon={s.kind === "image" ? ImageIcon : s.kind === "kb" ? BookOpen : FileText}
            className={`chat-src${compact ? " btn--icon" : ""}`} aria-label={`Источник: ${s.label}`}
            onClick={() => void chat.open(s)}>
            {!compact && <span className="chat-src__name">{s.label}</span>}
          </Button>
        </Tip>
      ))}
    </div>
  );
}

function AgentMessage({ m, tools = [], more, chat, onTime, onShow, compact, disabled }: {
  m: ChatMessage; tools?: ToolItem[];
  /** Ход, который допишется к этому сообщению (0.5): его текст — продолжением под ним. */
  more?: ChatMessage;
  chat: Chat; onTime?: (t: number) => void; onShow: (id: string) => void;
  compact: boolean; disabled: boolean;
}) {
  const writing = m.status === "writing";
  const partial = chat.state.partial[m.id];
  const continuing = more ? chat.state.partial[more.id]?.trim() : undefined;
  const text = m.text ?? "";
  const time = typeof m.t === "number" ? clock(m.t) : null;
  const reacted = REACTIONS.some((r) => m.reactions?.[r.emoji]);
  const ack = chat.ack(m.id);
  const explaining = !disabled && !writing && chat.explaining(m.id);
  const sources = writing ? [] : chat.sources(m);
  const hasText = !!text.trim();
  return (
    // Плоская карточка (0.5) со знаком агента в заголовке; пока пишет — знак «пишет».
    // В узкой ленте время — в подсказке Aurora (ui/Tip).
    <Tip content={compact && time ? time : ""}>
    <li className={`chat-msg chat-msg--agent${m.pin ? " is-pin" : ""}${writing ? " is-writing" : ""}${reacted ? " has-reaction" : ""}`}
      data-id={m.id} data-key={m.id} aria-busy={writing || undefined}>
      <div className="chat-msg__head">
        <AgentMark state={writing ? "write" : "rest"} size={14} />
        <span className="chat-msg__who">Ассистент</span>
        {time && !compact && <span className="chat-msg__time num">{time}</span>}
        {m.pin && <span className={`${BADGE_CLASS.run} badge--plain chat-msg__tag`}>вопрос вам</span>}
        {typeof m.explains === "string" && <ExplainsRef id={m.explains} chat={chat} onShow={onShow} />}
        {m.via === "command" && <span className={`${BADGE_CLASS.plain} chat-msg__tag`}>команда</span>}
        {writing && partial?.trim() && <span className="chat-msg__writing">пишет…</span>}
        {continuing && <span className="chat-msg__writing">дописывает…</span>}
      </div>
      {/* Ход работы (0.4): вызовы инструментов этого хода — строками, как в Claude CLI. */}
      <ToolRows items={tools} chat={chat} disabled={disabled} />
      {writing ? (
        partial?.trim()
          ? <div className="chat-msg__text chat-msg__text--streaming">{partial}</div>
          : tools.length ? null
            : <div className="chat-typing"><span className="chat-typing__dots" aria-hidden="true" />Пишет…</div>
      ) : text.trim() ? (
        <Markdown source={text} className="chat-msg__text" onTime={onTime} paths={inTauri() ? PATH_ACTIONS : null}
          settings={inTauri() ? SETTINGS_LINKS : null} />
      ) : null}
      {continuing && <div className="chat-msg__text chat-msg__text--streaming chat-msg__more">{continuing}</div>}
      {m.status === "cancelled" && <div className="chat-msg__note">Остановлено</div>}
      {m.status === "failed" && <div className="chat-msg__error">{m.error || "Ассистент не смог ответить"}</div>}
      {/* Низ карточки (макет MeetLive) — одна строка: источники, промежуток, реакции и копирование.
          Строка не схлопывается вне наведения: лента не прыгает. Кнопки ответа агента — под ней. */}
      {!writing && (sources.length > 0 || hasText) && (
        <div className="chat-msg__foot">
          <Sources sources={sources} chat={chat} compact={compact} />
          <span className="chat-msg__gap" />
          {hasText && (
            <div className="chat-msg__tools">
              <Reactions m={m} chat={chat} disabled={disabled} compact={compact} />
              <CopyButton text={plainMarkdown(text)} />
            </div>
          )}
        </div>
      )}
      {!writing && <AgentButtons m={m} chat={chat} disabled={disabled} />}
      {/* Видимые заметки; диктору их объявляет постоянная live-область ленты (`chat.announce`). */}
      {ack && <div key={ack} className="chat-msg__ack">{ack}</div>}
      {explaining && (
        <div className="chat-msg__pending">
          <span className="chat-typing__dots" aria-hidden="true" />{EXPLAINING}
        </div>
      )}
    </li>
    </Tip>
  );
}

/**
 * Нажатие кнопки голосом (0.5): «Засчитано голосом: «…»» и «Отменить» с отсчётом,
 * пока ждёт (10 с); потом — «Нажато голосом» или «отменено».
 */
function VoiceRow({ m, chat, disabled }: { m: ChatMessage; chat: Chat; disabled: boolean }) {
  const label = m.voice?.label ?? "";
  const pending = m.state === "pending";
  const due = (typeof m.at === "number" ? m.at * 1000 : Date.now()) + (m.undo_s ?? 10) * 1000;
  const [left, setLeft] = useState(() => Math.max(0, Math.ceil((due - Date.now()) / 1000)));
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => setLeft(Math.max(0, Math.ceil((due - Date.now()) / 1000))), 1000);
    return () => clearInterval(t);
  }, [pending, due]);
  const text = m.state === "pressed" ? `Нажато голосом: «${label}»`
    : m.state === "cancelled" ? "Голосовое нажатие отменено"
      : m.state === "missed" ? `Кнопку «${label}» уже нажали` : m.text;
  return (
    <li className={`chat-sys chat-sys--voice${pending ? " is-pending" : ""}`} data-id={m.id} data-key={m.id}>
      <Icon as={Mic} size="sm" className="chat-sys__icon" />{text}
      {pending && (
        <Button variant="ghost" size="xs" className="chat-sys__undo" disabled={disabled}
          onClick={() => void chat.cancelVoice(m.id)}>
          Отменить{left > 0 && <span className="chat-sys__left" aria-hidden="true"> · {left}</span>}
        </Button>
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
  // Превью картинки (0.5) — кнопка: раскрыть поверх окна; с файлом на диске — «Открыть файл», «Показать в папке».
  const [open, setOpen] = useState(false);
  const path = a?.path;
  const folder = path ? path.replace(/[\\/][^\\/]*$/, "") : "";
  return (
    <>
      <Tip content={note || name} describe={false}>
        <span className={`${BADGE_CLASS.plain} chat-att${failed ? " is-failed" : ""}`}>
          {preview && a?.type === "image" ? (
            <button type="button" className="chat-att__open" aria-label={`Открыть изображение ${name}`}
              onClick={() => setOpen(true)}>
              <img className="chat-att__thumb" src={preview} alt="" />
            </button>
          ) : <Icon as={a?.type === "image" ? ImageIcon : FileText} size="sm" />}
          <span className="chat-att__name">{name}</span>
          {note && <span className="chat-att__note">{note}</span>}
        </span>
      </Tip>
      {open && preview && (
        <Lightbox src={preview} name={name} onClose={() => setOpen(false)}
          onOpen={path && inTauri() ? () => openMaterial(path) : undefined}
          onReveal={folder && inTauri() ? () => openFolder(folder) : undefined} />
      )}
    </>
  );
}

function UserMessage({ m, chat, out, compact = false }: { m?: ChatMessage; chat: Chat; out?: Outgoing; compact?: boolean }) {
  const text = m?.text ?? out?.text ?? "";
  const atts = m?.attachments ?? out?.attachments ?? [];
  const time = typeof m?.t === "number" ? clock(m.t) : null;
  const names = atts.map((id) => chat.attachment(id)?.name || "вложение").join(", ");
  return (
    <Tip content={compact && time ? time : ""}>
    <li className={`chat-msg chat-msg--user${out ? ` is-${out.state}` : ""}`} data-id={m?.id}
      data-key={m?.id ?? `out:${out?.client_id}`}>
      {m?.via === "button" && <div className="chat-msg__via">кнопка</div>}
      {m?.via === "reaction" && <div className="chat-msg__via">реакция</div>}
      {m?.via === "command" && <div className="chat-msg__via">команда</div>}
      {text && <div className="chat-msg__text chat-msg__text--plain">{text}</div>}
      {atts.length > 0 && (compact ? (
        <Tip content={names} describe={false}>
          <span className="chat-msg__att-count" aria-label={`Вложения: ${names}`}>
            <Icon as={Paperclip} size="sm" />{atts.length}
          </span>
        </Tip>
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
    </Tip>
  );
}

function Item({ it, chat, onTime, onShow, compact, disabled }: {
  it: FeedItem; chat: Chat; onTime?: (t: number) => void; onShow: (id: string) => void; compact: boolean; disabled: boolean;
}) {
  if (it.type === "outgoing") return <UserMessage chat={chat} out={it.out} compact={compact} />;
  const m = it.message;
  if (m.kind === "agent") {
    return (
      <AgentMessage m={m} tools={it.tools} more={it.more} chat={chat} onTime={onTime} onShow={onShow} compact={compact}
        disabled={disabled} />
    );
  }
  if (m.card === "command") {
    return (
      <li className="chat-sys chat-sys--command" data-id={m.id} data-key={m.id}>
        <CommandLine m={m} chat={chat} disabled={disabled} />
      </li>
    );
  }
  if (m.notice) {
    // Однократная строка (0.4): что изменилось у ассистента и где это вернуть.
    return (
      <li className="chat-sys chat-sys--notice" data-id={m.id} data-key={m.id}>
        <Icon as={Info} size="sm" className="chat-sys__icon" />{m.text}
      </li>
    );
  }
  if (m.kind === "user") return <UserMessage m={m} chat={chat} compact={compact} />;
  if (m.kind === "meeting") {
    return <li className="chat-divider" data-id={m.id} data-key={m.id}><span>{m.text || "встреча"}</span></li>;
  }
  if (m.card === "confirm") {
    const open = chat.cards.some((c) => c.id === m.id);
    return (
      <li className="chat-sys chat-sys--card" data-id={m.id} data-key={m.id}>
        <ConfirmCard m={m} chat={chat} disabled={disabled} open={open} />
      </li>
    );
  }
  if (m.voice) return <VoiceRow m={m} chat={chat} disabled={disabled} />;
  if (m.gate) {
    // Ворота согласия заблокировали вызов агента (0.3.7): та же тихая строка, с пояснением.
    return <Tip content={GATE_TITLE}><li className="chat-sys chat-sys--gate" data-id={m.id} data-key={m.id}>{m.text}</li></Tip>;
  }
  return <li className="chat-sys" data-id={m.id} data-key={m.id}>{m.text}</li>;
}

/**
 * Закреплённый вопрос агента — над лентой одной строкой (макет MeetLive): бейдж «Вопрос вам»,
 * текст без разметки с многоточием (щелчок — целиком и обратно), «Показать в ленте» и «×» —
 * значками (место — тексту вопроса). Кнопки ответа агента — под строкой: ответить, не листая ленту.
 */
function Pinned({ m, chat, onShow, onHide, disabled }: {
  m: ChatMessage; chat: Chat; onShow: () => void; onHide: () => void; disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const text = plainMarkdown(m.text ?? "");
  return (
    <section className={`chat-pin${open ? " is-open" : ""}`} aria-label="Вопрос вам">
      <div className="chat-pin__row">
        <span className={`${BADGE_CLASS.run} badge--plain chat-pin__badge`}><Icon as={CircleHelp} size="sm" />Вопрос вам</span>
        <Tip content={open ? "Свернуть вопрос" : "Показать вопрос целиком"}>
          <button type="button" className="chat-pin__line" aria-expanded={open} onClick={() => setOpen(!open)}>
            {text}
          </button>
        </Tip>
        <IconButton icon={CornerDownRight} label="Показать в ленте" tooltip="Показать вопрос в ленте" onClick={onShow} />
        <IconButton icon={X} label="Убрать из закреплённых" onClick={onHide} />
      </div>
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
  // Строки хода работы у ответов: появилась строка, сменилось состояние, пришла карточка.
  const tools = chat.items.map((it) => (it.type === "message" && it.tools
    ? it.tools.map((t) => `${t.row.status ?? ""}${t.card ? `/${t.card.decision ?? "?"}` : ""}`).join(",") : "")).join("|");
  const sig = `${chat.items.length}:${last?.type === "message" ? `${last.message.id}:${last.message.status}:${last.message.text?.length ?? 0}` : "o"}:${
    Object.values(chat.state.partial).reduce((n, t) => n + t.length, 0)}:${tools}`;

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
    ? `${it.message.id}:${it.message.status ?? ""}:${it.message.buttons?.length ?? 0}:${it.message.text ? 1 : 0}:${it.message.decision ?? ""}`
    : `o:${it.out.client_id}:${it.out.state}`)).join("|") + tools;
  useLayoutEffect(() => {
    const all = rows();
    if (!all.length) return;
    const key = active && all.some((li) => li.dataset.key === active) ? active : all[all.length - 1]!.dataset.key;
    for (const li of all) {
      const on = li.dataset.key === key;
      li.tabIndex = on ? 0 : -1;
      for (const el of li.querySelectorAll<HTMLElement>(ACTIONS)) el.tabIndex = on ? 0 : -1;
    }
    // Карточка, что ждёт решения, — в порядке Tab всегда: ассистент стоит, пока её не решат.
    for (const el of list.current?.querySelectorAll<HTMLElement>(`.chat-card--open :is(${ACTIONS})`) ?? []) el.tabIndex = 0;
  }, [shape, active, chat.cards.length]);
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
    // Кнопка «×» ушла вместе с карточкой — фокус в строку ввода (док рабочей области), а не на <body>.
    const area = box.current?.closest(".chat-ws") ?? box.current?.closest(".chat-ws__main");
    area?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
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
      {/* Новая карточка объявляется и при «Не отвлекать»: ассистент ждёт решения (кнопки — в ней, в ленте). */}
      {chat.cards.length > 0 && (
        <span className="sr-only" role="alert">
          {`Ассистент ждёт подтверждения: ${chat.cards[chat.cards.length - 1]!.title ?? ""}`}
        </span>
      )}
      {pinned && (
        <Pinned m={pinned} chat={chat} onShow={showPinned} onHide={() => hidePin(pinned.id)}
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
