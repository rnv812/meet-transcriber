/**
 * Карточка записи, которую сейчас пишет ассистент: та же лента, дайджест и
 * вопросы, что в плавающей панели, но в полный размер. Пока ассистент
 * дописывает дорожки (`stopping`), лента остаётся, а поток и вопросы — нет.
 */

import type { Endpoint } from "../../lib/api";
import type { LiveStatus } from "../../lib/types";
import { LiveAsk } from "../../live/LiveAsk";
import { LiveDigest, LiveFeed } from "../../live/LiveFeed";
import { useLive } from "../../live/useLive";

export function LiveCard({ endpoint, live }: { endpoint: Endpoint; live: LiveStatus }) {
  const stopping = live.stopping || !live.active;
  // При остановке резидент поток уже закрыл: без `!stopping` хук
  // переподключался бы и писал «Нет связи с ассистентом».
  const state = useLive(endpoint, !stopping);
  return (
    <div className="live-card">
      <div className="live-card__head">
        <span className="live-dot" aria-hidden="true" />
        <span className="live-card__title">{stopping ? "Останавливаю…" : "Идёт запись с ассистентом"}</span>
        {state.error && <span className="muted">{state.error}</span>}
      </div>
      <LiveDigest digest={state.digest} />
      <LiveFeed lines={state.lines} className="live-card__feed" />
      <div className="live-card__ask">
        <LiveAsk reply={state.reply} onAsk={state.ask} disabled={stopping} />
      </div>
    </div>
  );
}
