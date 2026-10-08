/**
 * Рабочая область агента-участника (`assist.participant`): чат — главное,
 * расшифровка — колонкой рядом.
 *
 * Раскладка (макет MeetLive, доводка 0.4) — полосы во всю ширину по линейке:
 *   строка сессии (40 px: кнопка расшифровки, `SessionBar`) — линия снизу;
 *   тело — колонка расшифровки слева и чат (`LiveChat`) справа;
 *   док строки ввода (`.chat-dock` > `ChatComposer`) — линия сверху, сплошной фон.
 * Строка ввода — вне колонок: под ней лента не просвечивает, а её устройство
 * (поле, вложения, быстрые вопросы) живёт целиком в `ChatComposer`.
 *
 * Колонка расшифровки по умолчанию видна, только если область не уже
 * TRANSCRIPT_PX (560): в узкой панели две колонки нечитаемо узкие — там только
 * чат во всю ширину. Показать или убрать колонку — кнопкой в строке сессии;
 * выбор человека запоминается (`meet.pane.<place>-chat-side-hidden`: «1» —
 * убрана, «0» — показана) и важнее ширины. Таймкод в сообщении при убранной
 * расшифровке возвращает её — это тоже действие человека.
 *
 * Между колонками — разделитель (`meet.pane.<place>-chat-side`) без верхнего
 * предела. Окно сузили — сначала колонка расшифровки ужимается до
 * TRANSCRIPT_FLOOR (чату остаётся CHAT_MIN), потом чат — до CHAT_FLOOR. Ещё уже
 * (самая узкая панель с открытой вручную колонкой) чат остаётся CHAT_FLOOR, а
 * колонка становится уже TRANSCRIPT_FLOOR: её содержимое не ужимается дальше и
 * прокручивается вбок. В узкой колонке время — над репликой.
 *
 * Плотность (на раскладку не влияет): строка сессии — по ширине всей области
 * (уже COMPACT_PX «Что я знаю» — значком), лента — по ширине колонки чата (уже
 * CHAT_COMPACT_PX): время у сообщений в подсказке, у реакций только значки.
 *
 * До первого состояния ассистента раскладку выбирает `LiveWorkspace` по
 * последнему известному флагу участника (`meet.live.participant`).
 *
 * Сводка с экрана ушла — она в поповере «Что я знаю» шапки сессии. Вся
 * область — зона перетаскивания вложений (`data-chat-drop`).
 */

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { TERMS_NEEDED } from "../lib/terms";
import { IconButton } from "../ui/IconButton";
import { PaneResizer } from "../ui/PaneResizer";
import { ChatComposer } from "./ChatComposer";
import { LiveChat } from "./LiveChat";
import { LiveFeed } from "./LiveFeed";
import { CONNECTING, CatchupNote, type LivePlace, type LiveView } from "./LiveWorkspace";
import { SessionBar } from "./SessionBar";
import { PERSONAL_LIVE_QUESTIONS, profileOf } from "./profiles";
import type { Chat } from "./useChat";
import type { Live } from "./useLive";
import { useWide } from "./useWide";
import "./chat.css";

/** Область уже этого — шапка сессии короче; раскладка та же. */
export const COMPACT_PX = 420;
/** Область не уже этого — колонка расшифровки видна по умолчанию; уже — только чат (выбор человека важнее). */
export const TRANSCRIPT_PX = 560;
/** Колонка чата уже этого — время в подсказке, реакции без подписей (подписи не вылезают вбок). */
export const CHAT_COMPACT_PX = 360;

/** Чат не уже этого, пока колонке расшифровки хватает места (в chat.css — то же). */
export const CHAT_MIN = 280;
/** Колонка расшифровки не уже этого, пока чату хватает места. */
export const CHAT_SIDE_MIN = 160;
/**
 * Жёсткие минимумы: колонка расшифровки (её содержимое; сама колонка уже —
 * только в самой узкой панели, тогда она прокручивается вбок) и чат (всегда).
 */
export const TRANSCRIPT_FLOOR = 120;
export const CHAT_FLOOR = 160;
/** Промежуток между колонкой и чатом (chat.css, `gap`). */
const CHAT_GAP = 12;

/**
 * Сколько оставить чату (и промежутку) в области шириной `room`: обычно
 * CHAT_MIN; в узкой — меньше (колонке остаётся TRANSCRIPT_FLOOR), но не
 * меньше CHAT_FLOOR: ещё уже уступает колонка. Тот же расчёт — в chat.css
 * для ширины по умолчанию.
 */
export function chatReserve(room: number): number {
  const want = CHAT_MIN + CHAT_GAP;
  if (room - want >= TRANSCRIPT_FLOOR) return want;
  return Math.max(CHAT_FLOOR + CHAT_GAP, room - TRANSCRIPT_FLOOR);
}

/** Пределы колонки расшифровки: верхнего нет — только место и минимумы. */
export const CHAT_PANES = {
  side: { min: CHAT_SIDE_MIN, reserve: chatReserve },
} as const;

const hiddenKey = (name: string) => `meet.pane.${name}-hidden`;

/** Выбор человека: true — убрал колонку, false — показал; null — не выбирал (решает ширина). */
function loadHidden(name: string): boolean | null {
  try {
    const v = window.localStorage?.getItem(hiddenKey(name));
    return v === "1" ? true : v === "0" ? false : null;
  } catch {
    return null;
  }
}

function saveHidden(name: string, hidden: boolean): void {
  try {
    window.localStorage?.setItem(hiddenKey(name), hidden ? "1" : "0");
  } catch { /* хранилище недоступно: выбор живёт до перезапуска */ }
}

/** Почему писать ассистенту сейчас нельзя; null — можно. */
export function composerBlock(live: Live, chat: Chat, disabled: boolean): string | null {
  if (disabled) return "Запись останавливается — писать ассистенту уже нельзя";
  if (live.error) return "Нет связи с ассистентом — переподключаюсь…";
  if (!(chat.agent ?? live.agent)) return live.loaded ? "Ассистент выключен" : CONNECTING;
  return null;
}

export function ChatWorkspace({ live, chat, view, disabled = false, place = "panel", compact: forced, notice }: {
  live: Live;
  chat: Chat;
  view: LiveView;
  disabled?: boolean;
  place?: LivePlace;
  /** Пометка на месте строки ввода (условия не приняты): писать, реагировать и менять сессию нельзя. */
  notice?: ReactNode;
  /** Задать плотность снаружи (тесты); иначе — по ширине. На раскладку не влияет. */
  compact?: boolean;
}) {
  const root = useRef<HTMLDivElement>(null);
  const column = useRef<HTMLDivElement>(null);
  const roomy = useWide(root, COMPACT_PX);
  const spacious = useWide(root, TRANSCRIPT_PX);
  const chatRoomy = useWide(column, CHAT_COMPACT_PX);
  const barCompact = forced ?? !roomy;
  const compact = forced ?? !chatRoomy;
  const key = place === "card" ? "live-card" : "live";
  const name = `${key}-chat-side`;
  const [choice, setChoice] = useState(() => loadHidden(name));
  const hidden = choice ?? !spacious;
  const setHidden = (next: boolean) => {
    setChoice(next);
    saveHidden(name, next);
  };
  const agent = chat.agent ?? live.agent ?? null;

  // Переход по таймкоду при убранной расшифровке — вернуть её: человек хочет увидеть момент.
  // Только новый переход: тот, что был до (пере)монтирования (панель свернули и
  // развернули), расшифровку не возвращает.
  const seq = view.focus?.seq;
  const seenSeq = useRef(seq);
  useEffect(() => {
    if (seq === seenSeq.current) return;
    seenSeq.current = seq;
    if (seq !== undefined && hidden) setHidden(false);
  }, [seq, hidden]); // eslint-disable-line react-hooks/exhaustive-deps

  const block = notice ? TERMS_NEEDED : composerBlock(live, chat, disabled);
  const paneId = `${name}-pane`;

  return (
    <div ref={root} className={`chat-ws chat-ws--${place}${barCompact ? " chat-ws--compact" : ""}`} data-chat-drop="">
      {live.catchup?.active && <CatchupNote catchup={live.catchup} />}
      {/* Строка сессии: кнопка колонки расшифровки (всегда) и шапка сессии (когда агент известен). */}
      <div className="chat-ws__bar">
        <IconButton icon={hidden ? PanelLeftOpen : PanelLeftClose} className="chat-ws__side-toggle"
          label={hidden ? "Показать расшифровку" : "Убрать расшифровку"}
          tooltip={hidden ? "Показать расшифровку колонкой рядом с чатом" : "Убрать расшифровку — останется только чат"}
          aria-expanded={!hidden} aria-controls={hidden ? undefined : paneId} onClick={() => setHidden(!hidden)} />
        {agent && (
          <SessionBar agent={agent} summary={live.summary} writing={!!chat.writing} compact={barCompact} quiet={view.quiet}
            onFrequency={(f) => void chat.setFrequency(f)} onProfile={(p) => void chat.setProfile(p)}
            disabled={!!block} onRevokeGrant={(id) => void chat.revokeGrant(id)} />
        )}
      </div>
      {/* Чат и строка ввода всегда на месте и не пересоздаются; меняется только колонка расшифровки. */}
      <div className={`chat-ws__body${hidden ? " is-hidden" : ""}`}>
        {!hidden && (
          <section id={paneId} className="chat-ws__transcript" aria-label="Расшифровка">
            <LiveFeed lines={live.lines} className="chat-ws__feed" focus={view.focus} />
          </section>
        )}
        {!hidden && (
          <PaneResizer name={name} cssVar="--chat-side" spec={CHAT_PANES.side} panel="before"
            label="Ширина расшифровки" className="chat-ws__split" />
        )}
        <div ref={column} className="chat-ws__main">
          <LiveChat chat={chat} onTime={view.jump} quiet={view.quiet} compact={compact} disabled={!!block} />
        </div>
      </div>
      {/* Док строки ввода: во всю ширину под колонками, сплошной, с линией сверху. */}
      <div className="chat-dock">
        {notice ?? (
          <ChatComposer chat={chat} disabledReason={block} vision={agent?.vision !== false}
            quick={profileOf(agent?.profile) === "personal" ? PERSONAL_LIVE_QUESTIONS : undefined} />
        )}
      </div>
    </div>
  );
}
