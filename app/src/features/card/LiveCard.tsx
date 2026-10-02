/**
 * Карточка записи, которую сейчас пишет ассистент: та же рабочая область, что
 * в плавающей панели (вкладки «Лента · Сводка · Подсказки · Спросить», в
 * широкой карточке — две колонки), но в полный размер. Пока ассистент
 * дописывает дорожки (`stopping`), лента остаётся, а поток и вопросы — нет.
 */

import { useRef, useState } from "react";

import type { Endpoint } from "../../lib/api";
import type { LiveStatus } from "../../lib/types";
import { LiveWorkspace, useLiveView } from "../../live/LiveWorkspace";
import { QuietIcon } from "../../live/icons";
import { useLiveAsk } from "../../live/useLastLook";
import { useLive } from "../../live/useLive";
import { useWide } from "../../live/useWide";

export function LiveCard({ endpoint, live }: { endpoint: Endpoint; live: LiveStatus }) {
  const stopping = live.stopping || !live.active;
  // При остановке резидент поток уже закрыл: без `!stopping` хук
  // переподключался бы и писал «Нет связи с ассистентом».
  const state = useLive(endpoint, !stopping);
  const ask = useLiveAsk(state, true);
  const [quiet, setQuiet] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const wide = useWide(root);
  const view = useLiveView(state, { open: true, wide, quiet });
  return (
    <div ref={root} className="live-card">
      <div className="live-card__head">
        <span className="live-dot" aria-hidden="true" />
        <span className="live-card__title">{stopping ? "Останавливаю…" : "Идёт запись с ассистентом"}</span>
        {state.status && !stopping && <span className="muted" role="status">{state.status}</span>}
        {state.error && <span className="muted">{state.error}</span>}
        <button type="button" className="icon-btn live-card__quiet" aria-pressed={quiet} aria-label="Не отвлекать"
          title="Не отвлекать: без подсветки и счётчиков" onClick={() => setQuiet(!quiet)}>
          <QuietIcon on={quiet} />
        </button>
      </div>
      <div className="live-card__body">
        <LiveWorkspace live={state} view={view} onAsk={ask} disabled={stopping} />
      </div>
    </div>
  );
}
