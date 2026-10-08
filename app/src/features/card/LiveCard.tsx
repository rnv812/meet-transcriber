/**
 * Карточка записи, которую сейчас пишет ассистент: та же рабочая область, что
 * в плавающей панели (вкладки «Лента · Сводка · Подсказки · Спросить», в
 * широкой карточке — две колонки), но в полный размер. Пока ассистент
 * дописывает дорожки (`stopping`), лента остаётся, а поток и вопросы — нет.
 *
 * «Спросить об этом» у подсказки здесь — к агенту: `onAskAgent` переводит
 * карточку на вкладку «Агент» со ссылкой на подсказку (в плавающей панели —
 * вопрос ассистенту, как раньше).
 *
 * В шапке — часы записи (постоянное состояние, как у кнопки в рейке):
 * обычная запись с подключённым ассистентом — `elapsed_s` снимка, живой режим —
 * от `started_at`; между снимками часы идут сами. Временная встреча — пометка
 * «не сохранится».
 */

import { useEffect, useRef, useState } from "react";

import type { Endpoint } from "../../lib/api";
import { clock } from "../../lib/format";
import { TEMP_BADGE } from "../../lib/recordingStop";
import type { LiveHint, LiveStatus, Snapshot } from "../../lib/types";
import { LiveWorkspace, useLiveView } from "../../live/LiveWorkspace";
import { Bell, BellOff } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { useQuiet } from "../../live/useAttention";
import { useLiveAsk } from "../../live/useLastLook";
import { useChat } from "../../live/useChat";
import { useLive } from "../../live/useLive";
import { useWide } from "../../live/useWide";
import { LIVE_NUDGE_LEAD, OwnerVoiceNudge, wantsOwnerSample } from "../settings/OwnerVoiceDialog";

const TICK_MS = 1000;

/** Сколько идёт запись, секунд; неизвестно — null. Часы тикают сами между снимками. */
function useRecordingClock(live: LiveStatus, snapshot: Snapshot | null | undefined, on: boolean): number | null {
  const recording = snapshot?.status === "recording";
  const elapsed = recording ? snapshot.elapsed_s : null;
  const [seenAt, setSeenAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = Date.now();
    setSeenAt(t);
    setNow(t);
  }, [elapsed]);
  useEffect(() => {
    if (!on) return;
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [on]);
  if (!on) return null;
  if (elapsed !== null) return elapsed + Math.max(0, now - seenAt) / 1000;
  return live.started_at == null ? null : Math.max(0, now / 1000 - live.started_at);
}

export function LiveCard({ endpoint, live, snapshot, onAskAgent }: {
  endpoint: Endpoint;
  live: LiveStatus;
  /** Снимок резидента: часы записи и пометка временной встречи. */
  snapshot?: Snapshot | null;
  /** «Спросить об этом» у подсказки — агенту во вкладке «Агент». */
  onAskAgent?: (hint: LiveHint) => void;
}) {
  const stopping = live.stopping || !live.active;
  // При остановке резидент поток уже закрыл: без `!stopping` хук
  // переподключался бы и писал «Нет связи с ассистентом».
  const chat = useChat(endpoint);
  const state = useLive(endpoint, !stopping, chat.sink);
  const ask = useLiveAsk(state, true);
  const [quiet, setQuiet] = useQuiet(state.quietDefault);
  const root = useRef<HTMLDivElement>(null);
  const wide = useWide(root);
  const view = useLiveView(state, { open: true, wide, quiet });
  const elapsed = useRecordingClock(live, snapshot, !stopping);
  return (
    <div ref={root} className="live-card">
      <div className="live-card__head">
        <span className="live-dot" aria-hidden="true" />
        <span className="live-card__title">{stopping ? "Останавливаю…" : "Идёт запись с ассистентом"}</span>
        {elapsed !== null && <span className="muted num" role="timer" aria-label="Время записи">{clock(elapsed)}</span>}
        {/* Временная встреча вне библиотеки и в карточку не попадает — пометка на всякий случай. */}
        {snapshot?.temporary && <span className="badge badge--stale">{TEMP_BADGE}</span>}
        {state.status && !stopping && <span className="muted" role="status">{state.status}</span>}
        {state.error && <span className="muted">{state.error}</span>}
        <IconButton icon={quiet ? BellOff : Bell} label="Не отвлекать" pressed={quiet} className="live-card__quiet"
          tooltip="Не отвлекать: без подсветки и счётчиков" onClick={() => setQuiet(!quiet)} />
      </div>
      <div className="live-card__body">
        {wantsOwnerSample(state.mic) && (
          <OwnerVoiceNudge endpoint={endpoint} lead={LIVE_NUDGE_LEAD} className="live-nudge" />
        )}
        <LiveWorkspace live={state} view={view} onAsk={ask} disabled={stopping} onAskHint={onAskAgent} place="card"
          chat={chat} />
      </div>
    </div>
  );
}
