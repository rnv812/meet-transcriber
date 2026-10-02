/**
 * Карточка записи, которую сейчас пишет ассистент: та же лента, сводка и
 * вопросы, что в плавающей панели, но в полный размер. Пока ассистент
 * дописывает дорожки (`stopping`), лента остаётся, а поток и вопросы — нет.
 */

import { useCallback, useState } from "react";

import type { Endpoint } from "../../lib/api";
import type { LiveStatus } from "../../lib/types";
import { LiveAsk } from "../../live/LiveAsk";
import { type FeedFocus, LiveDigest, LiveFeed } from "../../live/LiveFeed";
import { useLiveAsk } from "../../live/useLastLook";
import { useLive } from "../../live/useLive";

export function LiveCard({ endpoint, live }: { endpoint: Endpoint; live: LiveStatus }) {
  const stopping = live.stopping || !live.active;
  // При остановке резидент поток уже закрыл: без `!stopping` хук
  // переподключался бы и писал «Нет связи с ассистентом».
  const state = useLive(endpoint, !stopping);
  const ask = useLiveAsk(state, true);
  const [focus, setFocus] = useState<FeedFocus | null>(null);
  const jump = useCallback((t: number) => setFocus((f) => ({ t, seq: (f?.seq ?? 0) + 1 })), []);
  return (
    <div className="live-card">
      <div className="live-card__head">
        <span className="live-dot" aria-hidden="true" />
        <span className="live-card__title">{stopping ? "Останавливаю…" : "Идёт запись с ассистентом"}</span>
        {state.error && <span className="muted">{state.error}</span>}
      </div>
      <LiveDigest digest={state.digest} />
      <LiveFeed lines={state.lines} className="live-card__feed" focus={focus} />
      <div className="live-card__ask">
        <LiveAsk qa={state.qa} asking={state.asking} error={state.askError} onAsk={ask}
          disabled={stopping} onTime={jump} />
      </div>
    </div>
  );
}
