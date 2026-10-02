/**
 * Карточка записи, которую сейчас пишет ассистент: та же рабочая область, что
 * в плавающей панели (вкладки «Лента · Сводка · Подсказки · Спросить», в
 * широкой карточке — две колонки), но в полный размер. Пока ассистент
 * дописывает дорожки (`stopping`), лента остаётся, а поток и вопросы — нет.
 *
 * «Спросить об этом» у подсказки здесь — к агенту: `onAskAgent` переводит
 * карточку на вкладку «Агент» со ссылкой на подсказку (в плавающей панели —
 * вопрос ассистенту, как раньше).
 */

import { useRef } from "react";

import type { Endpoint } from "../../lib/api";
import type { LiveHint, LiveStatus } from "../../lib/types";
import { LiveWorkspace, useLiveView } from "../../live/LiveWorkspace";
import { Bell, BellOff } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { useQuiet } from "../../live/useAttention";
import { useLiveAsk } from "../../live/useLastLook";
import { useLive } from "../../live/useLive";
import { useWide } from "../../live/useWide";

export function LiveCard({ endpoint, live, onAskAgent }: {
  endpoint: Endpoint;
  live: LiveStatus;
  /** «Спросить об этом» у подсказки — агенту во вкладке «Агент». */
  onAskAgent?: (hint: LiveHint) => void;
}) {
  const stopping = live.stopping || !live.active;
  // При остановке резидент поток уже закрыл: без `!stopping` хук
  // переподключался бы и писал «Нет связи с ассистентом».
  const state = useLive(endpoint, !stopping);
  const ask = useLiveAsk(state, true);
  const [quiet, setQuiet] = useQuiet(state.quietDefault);
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
        <IconButton icon={quiet ? BellOff : Bell} label="Не отвлекать" pressed={quiet} className="live-card__quiet"
          tooltip="Не отвлекать: без подсветки и счётчиков" onClick={() => setQuiet(!quiet)} />
      </div>
      <div className="live-card__body">
        <LiveWorkspace live={state} view={view} onAsk={ask} disabled={stopping} onAskHint={onAskAgent} />
      </div>
    </div>
  );
}
