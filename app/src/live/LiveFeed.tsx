/** Лента живой записи и дайджест: общие для плавающей панели и карточки записи. */

import { useLayoutEffect, useRef, useState } from "react";

import { clock } from "../lib/format";
import { Markdown } from "../lib/markdown";
import type { LiveLine } from "../lib/types";
import "./live.css";

/** Насколько от низа ещё считается «внизу»: доли пикселей и последняя строка. */
const BOTTOM_SLACK_PX = 24;

/**
 * Лента строк. Следит за низом, пока человек сам не прокрутил вверх
 * (перечитывает сказанное); вернулся вниз — следит снова.
 */
export function LiveFeed({ lines, className = "" }: { lines: LiveLine[]; className?: string }) {
  const box = useRef<HTMLOListElement>(null);
  const follow = useRef(true);

  const onScroll = () => {
    const el = box.current;
    if (el) follow.current = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK_PX;
  };

  useLayoutEffect(() => {
    const el = box.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <ol ref={box} className={`live-feed ${className}`.trim()} role="log" aria-label="Лента встречи"
      onScroll={onScroll}>
      {lines.length === 0 && <li className="live-feed__empty muted">Реплики появятся, как только их расшифрует ассистент</li>}
      {lines.map((l, k) => (
        <li key={k} className="live-feed__line">
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

/** Дайджест встречи (Markdown модели); сворачивается по заголовку. */
export function LiveDigest({ digest, defaultOpen = true }: { digest: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="live-digest">
      <button type="button" className="live-digest__toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span aria-hidden="true">{open ? "▾" : "▸"}</span> Дайджест
      </button>
      {open && (digest.trim()
        ? <Markdown source={digest} className="live-digest__body" />
        : <div className="live-digest__body muted">Дайджест появится через пару минут разговора</div>)}
    </section>
  );
}
