/**
 * Рабочая область агента-участника (`assist.participant`): чат — главное,
 * расшифровка — колонкой рядом.
 *
 * Раскладка одна при любой ширине окна (0.3.7): ничего не перескакивает и
 * ничего не прячется само. Слева — колонка расшифровки, справа — чат; между
 * ними разделитель (`meet.pane.<place>-chat-side`) без верхнего предела.
 * Окно сузили — сначала ужимается колонка (чату остаётся CHAT_MIN), потом
 * обе — до своих жёстких минимумов (TRANSCRIPT_FLOOR, CHAT_FLOOR). Убрать
 * расшифровку совсем можно только самому: кнопка «‹» на разделителе (и «›»,
 * чтобы вернуть); выбор запоминается (`meet.pane.<place>-chat-side-hidden`).
 * Таймкод в сообщении при убранной расшифровке возвращает её — это тоже
 * действие человека.
 *
 * `compact` (узкая область, уже COMPACT_PX) — только плотность: время у
 * сообщений в подсказке, у реакций только эмодзи. На раскладку не влияет.
 *
 * Сводка с экрана ушла — она в поповере «Что я знаю» шапки сессии. Вся
 * область — зона перетаскивания вложений (`data-chat-drop`).
 */

import { useEffect, useRef, useState } from "react";

import { PaneResizer } from "../ui/PaneResizer";
import { ChatComposer } from "./ChatComposer";
import { LiveChat } from "./LiveChat";
import { LiveFeed } from "./LiveFeed";
import { CONNECTING, CatchupNote, type LivePlace, type LiveView } from "./LiveWorkspace";
import { SessionBar } from "./SessionBar";
import type { Chat } from "./useChat";
import type { Live } from "./useLive";
import { useWide } from "./useWide";
import "./chat.css";

/** Уже этого — плотнее (время в подсказке, реакции без подписей); раскладка та же. */
export const COMPACT_PX = 420;

/** Чат не уже этого, пока колонке расшифровки хватает места (в chat.css — то же). */
export const CHAT_MIN = 280;
/** Колонка расшифровки не уже этого, пока чату хватает места. */
export const CHAT_SIDE_MIN = 160;
/** Совсем узкое окно: ниже этого не ужимаются ни колонка, ни чат. */
export const TRANSCRIPT_FLOOR = 96;
export const CHAT_FLOOR = 160;
/** Промежуток между колонкой и чатом (chat.css, `gap`). */
const CHAT_GAP = 12;

/**
 * Сколько оставить чату (и промежутку) в области шириной `room`: обычно
 * CHAT_MIN; в узкой — меньше, но колонке остаётся TRANSCRIPT_FLOOR, а чат не
 * уже CHAT_FLOOR (ещё уже — колонку можно убрать только самому, «‹»).
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

function loadHidden(name: string): boolean {
  try {
    return window.localStorage?.getItem(hiddenKey(name)) === "1";
  } catch {
    return false;
  }
}

function saveHidden(name: string, hidden: boolean): void {
  try {
    if (hidden) window.localStorage?.setItem(hiddenKey(name), "1");
    else window.localStorage?.removeItem(hiddenKey(name));
  } catch { /* хранилище недоступно: выбор живёт до перезапуска */ }
}

/** Почему писать ассистенту сейчас нельзя; null — можно. */
export function composerBlock(live: Live, chat: Chat, disabled: boolean): string | null {
  if (disabled) return "Запись останавливается — писать ассистенту уже нельзя";
  if (live.error) return "Нет связи с ассистентом — переподключаюсь…";
  if (!(chat.agent ?? live.agent)) return live.loaded ? "Ассистент выключен" : CONNECTING;
  return null;
}

export function ChatWorkspace({ live, chat, view, disabled = false, place = "panel", compact: forced }: {
  live: Live;
  chat: Chat;
  view: LiveView;
  disabled?: boolean;
  place?: LivePlace;
  /** Задать плотность снаружи (тесты); иначе — по ширине. На раскладку не влияет. */
  compact?: boolean;
}) {
  const root = useRef<HTMLDivElement>(null);
  const roomy = useWide(root, COMPACT_PX);
  const compact = forced ?? !roomy;
  const key = place === "card" ? "live-card" : "live";
  const name = `${key}-chat-side`;
  const [hidden, setHiddenState] = useState(() => loadHidden(name));
  const setHidden = (next: boolean) => {
    setHiddenState(next);
    saveHidden(name, next);
  };
  const agent = chat.agent ?? live.agent ?? null;

  // Переход по таймкоду при убранной расшифровке — вернуть её: человек хочет увидеть момент.
  const seq = view.focus?.seq;
  useEffect(() => {
    if (seq !== undefined && loadHidden(name)) setHidden(false);
  }, [seq]); // eslint-disable-line react-hooks/exhaustive-deps

  const block = composerBlock(live, chat, disabled);
  const paneId = `${name}-pane`;

  return (
    <div ref={root} className={`chat-ws${compact ? " chat-ws--compact" : ""}`} data-chat-drop="">
      {live.catchup?.active && <CatchupNote catchup={live.catchup} />}
      {agent && (
        <SessionBar agent={agent} summary={live.summary} writing={!!chat.writing} compact={compact} quiet={view.quiet}
          onFrequency={(f) => void chat.setFrequency(f)} disabled={!!block} />
      )}
      {/* Одна раскладка при любой ширине: чат и строка ввода всегда на месте и не пересоздаются. */}
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
        <div className="chat-ws__edge">
          <button type="button" className="chat-ws__toggle" aria-expanded={!hidden}
            aria-controls={hidden ? undefined : paneId}
            aria-label={hidden ? "Показать расшифровку" : "Убрать расшифровку"}
            title={hidden ? "Показать расшифровку" : "Убрать расшифровку — останется только чат"}
            onClick={() => setHidden(!hidden)}>
            <span aria-hidden="true">{hidden ? "›" : "‹"}</span>
          </button>
        </div>
        <div className="chat-ws__main">
          <LiveChat chat={chat} onTime={view.jump} quiet={view.quiet} compact={compact} disabled={!!block} />
          <ChatComposer chat={chat} disabledReason={block} vision={agent?.vision !== false} />
        </div>
      </div>
    </div>
  );
}
