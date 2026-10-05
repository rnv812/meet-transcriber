/**
 * Состояние живого режима для плавающей панели: когда начался (таймер) и не
 * дописывается ли уже (`stopping`).
 *
 * Не опрос, а поток событий резидента (`/events`): первым сообщением он
 * присылает снимок, а на `live.*` снимок перечитываем. Пока ничего не
 * меняется — ни одного запроса; остановку из трея или главного окна панель
 * видит сразу. Ни ответ `/live/events` (`state` без времени старта), ни
 * разовое чтение снимка этого не дают, а опрос раз в 2 с дороже и запаздывает.
 */

import { useEffect, useState } from "react";

import { type Endpoint, getState, openEvents } from "../lib/api";
import type { LiveStatus, Snapshot } from "../lib/types";

const RECONNECT_MS = 2000;

/** Состояние ассистента и откуда идущая запись (`source: "live"` — «Запись с ассистентом»). */
export type PanelStatus = LiveStatus & { source?: string | null };

export function useLiveStatus(ep: Endpoint | null): PanelStatus | null {
  const [live, setLive] = useState<PanelStatus | null>(null);

  useEffect(() => {
    if (!ep) return;
    let closed = false;
    let close: (() => void) | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // Ответы /state приходят в любом порядке: применяется только последний
    // запрошенный, а снимок из потока отменяет все ещё не пришедшие.
    let seq = 0;
    const apply = (s: Snapshot) => { if (!closed && s.live) setLive({ ...s.live, source: s.source ?? null }); };
    const connect = () => {
      close = openEvents(ep, {
        onSnapshot: (s) => { seq++; apply(s); },
        onEvent: (e) => {
          if (!e.kind.startsWith("live.")) return;
          const mine = ++seq;
          getState(ep).then((s) => { if (mine === seq) apply(s); }).catch(() => {});
        },
        onError: () => {
          close?.();
          if (!closed) timer = setTimeout(connect, RECONNECT_MS);
        },
      });
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      close?.();
    };
  }, [ep]);

  return live;
}
