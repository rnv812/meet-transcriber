/** Лента живой записи: общая для плавающей панели и карточки записи. */

import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { clock } from "../lib/format";
import "./live.css";
import type { FeedLine } from "./useLive";

/** Насколько от низа ещё считается «внизу»: доли пикселей и последняя строка. */
const BOTTOM_SLACK_PX = 24;
/** Сколько подсвечена реплика, к которой перешли по таймкоду. */
export const TARGET_MS = 2500;

/** Переход к моменту встречи: секунды записи; `seq` различает повторные щелчки. */
export type FeedFocus = { t: number; seq: number };

/** Реплика для момента `t`: последняя, начавшаяся не позже него (иначе первая). */
export function lineAt(lines: FeedLine[], t: number): FeedLine | undefined {
  let found: FeedLine | undefined;
  for (const l of lines) {
    if (l.t <= t + 0.5) found = l;
    else break;
  }
  return found ?? lines[0];
}

/**
 * Лента строк (`id` — номер в потоке, по нему строка ключуется). Следит за
 * низом, пока человек сам не прокрутил вверх (перечитывает сказанное);
 * вернулся вниз — следит снова. `focus` — перейти к реплике момента и
 * ненадолго её подсветить (слежение за низом при этом выключается).
 */
export function LiveFeed({ lines, className = "", focus = null }: {
  lines: FeedLine[];
  className?: string;
  focus?: FeedFocus | null;
}) {
  const box = useRef<HTMLOListElement>(null);
  const follow = useRef(true);
  const [target, setTarget] = useState<FeedLine | null>(null);

  const onScroll = () => {
    const el = box.current;
    if (el) follow.current = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK_PX;
  };

  useLayoutEffect(() => {
    const el = box.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  // Ленту сузили или сделали ниже (разделитель областей, окно): строки
  // переносятся, высота растёт без новых строк — следящая лента остаётся внизу.
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(() => {
      if (follow.current) el.scrollTop = el.scrollHeight;
    });
    watch.observe(el);
    return () => watch.disconnect();
  }, []);

  useLayoutEffect(() => {
    if (!focus) return;
    const line = lineAt(lines, focus.t);
    if (!line) return;
    setTarget(line);
    const el = box.current;
    const index = lines.indexOf(line);
    const row = el?.children[index] as HTMLElement | undefined;
    if (el && row) {
      follow.current = false;
      el.scrollTop = Math.max(0, row.offsetTop - el.offsetTop - el.clientHeight / 3);
    }
    // Только на новый переход (focus): подросшая лента цель не меняет.
  }, [focus]);

  useEffect(() => {
    if (!target) return;
    const timer = setTimeout(() => setTarget(null), TARGET_MS);
    return () => clearTimeout(timer);
  }, [target]);

  return (
    <ol ref={box} className={`live-feed ${className}`.trim()} role="log" aria-label="Лента встречи"
      onScroll={onScroll}>
      {lines.length === 0 && <li className="live-feed__empty muted">Реплики появятся, как только их расшифрует ассистент</li>}
      {lines.map((l, k) => (
        // Ключ — номер строки: при обрезке начала ленты остальные строки не пересоздаются.
        <li key={l.id ?? `i${k}`} className={`live-feed__line${l === target ? " is-target" : ""}`}>
          <span className="live-feed__t num">{clock(l.t)}</span>
          <span className="live-feed__text">
            {l.speaker && <span className="live-feed__who">{l.speaker}</span>}
            {l.text}
          </span>
        </li>
      ))}
    </ol>
  );
}
