/** Лента живой записи: общая для плавающей панели и карточки записи. */

import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { clock, plural } from "../lib/format";
import { Button } from "../ui/Button";
import { Tip } from "../ui/Tip";
import "./live.css";
import type { FeedLine } from "./useLive";

/** Насколько от низа ещё считается «внизу»: доли пикселей и последняя строка. */
const BOTTOM_SLACK_PX = 24;
/** Сколько подсвечена реплика, к которой перешли по таймкоду. */
export const TARGET_MS = 2500;

/** Подпись голоса собеседников, которую живой режим уточняет по ходу встречи. */
const PROVISIONAL = /^Собеседник \d+$/;
/** Подсказка к нумерованным подписям живой ленты. */
export const LABELS_HINT = "Подписи «Собеседник N» предварительные — уточнятся в расшифровке после встречи";

/** Та же строка ленты: по номеру в потоке, без него — по моменту и тексту. */
function sameLine(a: FeedLine, b: FeedLine): boolean {
  if (a === b) return true;
  if (a.id != null || b.id != null) return a.id === b.id;
  return a.t === b.t && a.text === b.text;
}

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
 * вернулся вниз — следит снова. Пока не следит, новые реплики считаются:
 * «↓ N новых» внизу ленты (0.5) возвращает вниз. `focus` — перейти к реплике
 * момента и ненадолго её подсветить (слежение за низом при этом выключается).
 */
export function LiveFeed({ lines, className = "", focus = null }: {
  lines: FeedLine[];
  className?: string;
  focus?: FeedFocus | null;
}) {
  const box = useRef<HTMLOListElement>(null);
  const follow = useRef(true);
  const [target, setTarget] = useState<FeedLine | null>(null);
  /** Новые реплики, пока лента не следит за низом. */
  const [unseen, setUnseen] = useState(0);
  const last = useRef<FeedLine | undefined>(lines.at(-1));

  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    follow.current = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK_PX;
    if (follow.current) setUnseen(0);
  };
  const toBottom = () => {
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
    follow.current = true;
    setUnseen(0);
  };

  useLayoutEffect(() => {
    const el = box.current;
    // Сколько пришло: строки после прежней последней (начало ленты могло обрезаться).
    const prev = last.current;
    let at = -1;
    if (prev) for (let k = lines.length - 1; k >= 0 && at < 0; k--) if (sameLine(lines[k]!, prev)) at = k;
    const added = at >= 0 ? lines.length - 1 - at : lines.length;
    last.current = lines.at(-1);
    if (el && follow.current) el.scrollTop = el.scrollHeight;
    else if (added > 0) setUnseen((n) => n + added);
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

  const numbered = lines.some((l) => PROVISIONAL.test(l.speaker ?? ""));
  const feed = (
    <ol ref={box} className={`live-feed ${className}`.trim()} role="log" aria-label="Лента встречи"
      onScroll={onScroll}>
      {lines.length === 0 && <li className="live-feed__empty muted">Реплики появятся, как только их расшифрует ассистент</li>}
      {lines.map((l, k) => (
        // Ключ — номер строки: при обрезке начала ленты остальные строки не пересоздаются.
        <li key={l.id ?? `i${k}`} className={`live-feed__line${l === target ? " is-target" : ""}`}>
          <span className="live-feed__t num">{clock(l.t)}</span>
          <span className="live-feed__text">
            {l.speaker && (
              <Tip content={PROVISIONAL.test(l.speaker) ? LABELS_HINT : ""}>
                <span className="live-feed__who">{l.speaker}</span>
              </Tip>
            )}
            {l.text}
          </span>
        </li>
      ))}
      {/* «↓ Новые» (0.5): прилипает к низу видимой части ленты (sticky), щелчок — вниз и снова следить. */}
      {unseen > 0 && (
        <li className="live-feed__more">
          <Button variant="deep" size="xs" className="live-feed__new" onClick={toBottom}>
            ↓ {unseen} {plural(unseen, "новая", "новых", "новых")}
          </Button>
        </li>
      )}
    </ol>
  );
  if (!numbered) return feed;
  return (
    <>
      <p className="live-feed__hint muted">{LABELS_HINT}</p>
      {feed}
    </>
  );
}
