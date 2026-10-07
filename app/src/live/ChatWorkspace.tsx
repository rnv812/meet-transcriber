/**
 * Рабочая область агента-участника (`assist.participant`): чат — главное,
 * расшифровка — узкой полосой.
 *
 * - Широкая (от 720 px): расшифровка — колонкой слева, чат — справа; ширину
 *   колонки человек подбирает сам (разделитель, `meet.pane.<place>-chat-side`).
 * - Узкая: расшифровка — полосой над чатом, её высота — разделителем.
 * - Компактная (уже COMPACT_PX): над чатом одна строка — последняя реплика;
 *   щелчок по ней (или переход по таймкоду) разворачивает полосу.
 *
 * Сводка с экрана ушла — она в поповере «Что я знаю» шапки сессии. Вся
 * область — зона перетаскивания вложений (`data-chat-drop`).
 */

import { useEffect, useRef, useState } from "react";

import { PaneResizer } from "../ui/PaneResizer";
import { ChatComposer } from "./ChatComposer";
import { LiveChat } from "./LiveChat";
import { LiveFeed } from "./LiveFeed";
import { CatchupNote, type LivePlace, type LiveView } from "./LiveWorkspace";
import { SessionBar } from "./SessionBar";
import type { Chat } from "./useChat";
import type { Live } from "./useLive";
import { useWide } from "./useWide";
import "./chat.css";

/** Уже этого — компактная раскладка: чат и строка расшифровки. */
export const COMPACT_PX = 420;

/** Пределы полосы расшифровки: колонка в широкой, высота в узкой. */
export const CHAT_PANES = {
  side: { min: 160, max: 640, reserve: 320 },
  strip: { min: 40, max: 2000, reserve: 200 },
} as const;

export type ChatLayout = "side" | "top" | "compact";

/** Почему писать ассистенту сейчас нельзя; null — можно. */
export function composerBlock(live: Live, chat: Chat, disabled: boolean): string | null {
  if (disabled) return "Запись останавливается — писать ассистенту уже нельзя";
  if (live.error) return "Нет связи с ассистентом — переподключаюсь…";
  if (!(chat.agent ?? live.agent)) return "Ассистент выключен";
  return null;
}

export function ChatWorkspace({ live, chat, view, disabled = false, place = "panel", compact: forced }: {
  live: Live;
  chat: Chat;
  view: LiveView;
  disabled?: boolean;
  place?: LivePlace;
  /** Задать компактность снаружи (тесты, свёрнутый вид); иначе — по ширине. */
  compact?: boolean;
}) {
  const root = useRef<HTMLDivElement>(null);
  const roomy = useWide(root, COMPACT_PX);
  const compact = forced ?? (!view.wide && !roomy);
  const layout: ChatLayout = view.wide ? "side" : compact ? "compact" : "top";
  const [stripOpen, setStripOpen] = useState(false);
  const key = place === "card" ? "live-card" : "live";
  const agent = chat.agent ?? live.agent ?? null;

  // Переход по таймкоду в компактной — развернуть полосу, чтобы было видно момент.
  const seq = view.focus?.seq;
  useEffect(() => { if (seq !== undefined) setStripOpen(true); }, [seq]);

  const block = composerBlock(live, chat, disabled);
  const feed = <LiveFeed lines={live.lines} className="chat-ws__feed" focus={view.focus} />;
  const last = live.lines.at(-1);
  const main = (
    <div className="chat-ws__main">
      <LiveChat chat={chat} onTime={view.jump} quiet={view.quiet} compact={compact} disabled={!!block} />
      <ChatComposer chat={chat} disabledReason={block} vision={agent?.vision !== false} />
    </div>
  );

  return (
    <div ref={root} className={`chat-ws chat-ws--${layout}`} data-chat-drop="">
      {live.catchup?.active && <CatchupNote catchup={live.catchup} />}
      {agent && (
        <SessionBar agent={agent} summary={live.summary} writing={!!chat.writing} compact={compact} quiet={view.quiet}
          onFrequency={(f) => void chat.setFrequency(f)} disabled={!!block} />
      )}
      {/* Одно место в дереве при любой раскладке: чат и строка ввода не
          пересоздаются при смене ширины (черновик, прокрутка, «↓ N»). */}
      <div className={`chat-ws__body chat-ws__body--${layout}`}>
        {layout === "compact" && (
          <button type="button" className="chat-ticker" aria-expanded={stripOpen}
            aria-label={stripOpen ? "Свернуть расшифровку" : "Развернуть расшифровку"}
            onClick={() => setStripOpen(!stripOpen)}>
            <span className="chat-ticker__mark" aria-hidden="true">{stripOpen ? "▾" : "▸"}</span>
            {last ? (
              <span className="chat-ticker__text">
                {last.speaker && <span className="live-feed__who">{last.speaker}</span>}{last.text}
              </span>
            ) : <span className="chat-ticker__text muted">Реплики появятся, как только их расшифрует ассистент</span>}
          </button>
        )}
        {(layout !== "compact" || stripOpen) && (
          <section className={layout === "side" ? "chat-ws__transcript"
            : `chat-ws__strip${layout === "compact" ? " chat-ws__strip--compact" : ""}`} aria-label="Расшифровка">
            {feed}
          </section>
        )}
        {layout === "side" ? (
          <PaneResizer key="side" name={`${key}-chat-side`} cssVar="--chat-side" spec={CHAT_PANES.side} panel="before"
            label="Ширина расшифровки" className="chat-ws__split" />
        ) : layout === "top" ? (
          <PaneResizer key="strip" name={`${key}-chat-strip`} cssVar="--chat-strip" spec={CHAT_PANES.strip}
            panel="before" axis="y" label="Высота расшифровки" />
        ) : null}
        {main}
      </div>
    </div>
  );
}
