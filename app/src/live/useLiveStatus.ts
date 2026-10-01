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

export function useLiveStatus(ep: Endpoint | null): LiveStatus | null {
  const [live, setLive] = useState<LiveStatus | null>(null);

  useEffect(() => {
    if (!ep) return;
    let closed = false;
    let close: (() => void) | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const apply = (s: Snapshot) => { if (!closed && s.live) setLive(s.live); };
    const connect = () => {
      close = openEvents(ep, {
        onSnapshot: apply,
        onEvent: (e) => {
          if (e.kind.startsWith("live.")) getState(ep).then(apply).catch(() => {});
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
