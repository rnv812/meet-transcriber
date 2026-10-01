/**
 * Плавающая панель ассистента (окно `live`, страница live.html).
 *
 * Окно создаёт и закрывает оболочка по `snapshot.live.active`; панель только
 * показывает режим и меняет свою высоту командой `live_resize` (120 — свёрнута,
 * 520 — развёрнута; нижний край окна на месте). Фокус панель не берёт: окно
 * создаётся без фокуса, и ни один элемент не фокусируется сам — клавиатура
 * остаётся у звонка, пока человек не щёлкнет в поле вопроса.
 */

import { useEffect, useState } from "react";

import { type Endpoint, NoResidentError, liveStop, resolveEndpoint } from "../lib/api";
import { clock, errorText } from "../lib/format";
import { inTauri, invoke } from "../lib/shell";
import { Button } from "../ui/Button";
import { LiveAsk } from "./LiveAsk";
import { LiveDigest, LiveFeed } from "./LiveFeed";
import { useLive } from "./useLive";
import { useLiveStatus } from "./useLiveStatus";
import "./live.css";

export const COLLAPSED_PX = 120;
export const EXPANDED_PX = 520;
const TICK_MS = 1000;
const FIND_MS = 2000;

/** Секунды с `started_at` (стенное время резидента), тикает раз в секунду. */
function useElapsed(startedAt: number | null | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt == null) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [startedAt]);
  return startedAt == null ? 0 : Math.max(0, now / 1000 - startedAt);
}

export function LivePanel({ endpoint }: { endpoint: Endpoint }) {
  const status = useLiveStatus(endpoint);
  // До первого снимка считаем режим идущим: окно существует только при нём.
  const live = useLive(endpoint, status ? status.active : true);
  const elapsed = useElapsed(status?.started_at);
  const [expanded, setExpanded] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const stopping = stopRequested || !!status?.stopping;

  useEffect(() => {
    if (!inTauri()) return;
    invoke("live_resize", { height: expanded ? EXPANDED_PX : COLLAPSED_PX })
      .catch((cause) => console.warn("live_resize:", cause));
  }, [expanded]);

  const stop = () => {
    setStopRequested(true);
    setStopError(null);
    liveStop(endpoint).catch((e) => {
      setStopRequested(false);
      setStopError(errorText(e));
    });
  };

  const last = live.lines.at(-1);
  return (
    <div className={`live-panel${expanded ? " live-panel--expanded" : ""}`}>
      <header className="live-head">
        <span className="live-head__title">
          <span className="live-dot" aria-hidden="true" />
          <span className="num">{clock(elapsed)}</span> · {stopping ? "Останавливаю…" : "Ассистент слушает"}
        </span>
        <span className="live-head__actions">
          <Button onClick={() => setExpanded(!expanded)}>{expanded ? "Свернуть" : "Развернуть"}</Button>
          <Button variant="danger" onClick={stop} disabled={stopping}>Стоп</Button>
        </span>
      </header>
      {stopError && <div className="live-panel__error" role="alert">{stopError}</div>}
      {live.error && <div className="live-panel__note muted">{live.error}</div>}
      {expanded ? (
        <div className="live-panel__body">
          <LiveDigest digest={live.digest} defaultOpen={false} />
          <LiveFeed lines={live.lines} className="live-panel__feed" />
          <LiveAsk reply={live.reply} onAsk={live.ask} disabled={stopping} />
        </div>
      ) : (
        <div className="live-last" aria-live="polite">
          {last ? (
            <>
              {last.speaker && <span className="live-feed__who">{last.speaker}</span>}
              <span>{last.text}</span>
            </>
          ) : <span className="muted">Реплики появятся, как только их расшифрует ассистент</span>}
        </div>
      )}
    </div>
  );
}

/** Страница окна: находит резидента (адрес и токен от оболочки) и показывает панель. */
export function LiveWindow() {
  const [endpoint, setEndpoint] = useState<Endpoint | null>(null);
  useEffect(() => {
    let gone = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const find = () => {
      resolveEndpoint().then((ep) => { if (!gone) setEndpoint(ep); }).catch((cause) => {
        if (!(cause instanceof NoResidentError)) console.warn("resolveEndpoint:", cause);
        if (!gone) timer = setTimeout(find, FIND_MS);
      });
    };
    find();
    return () => { gone = true; clearTimeout(timer); };
  }, []);

  if (!endpoint) {
    return (
      <div className="live-panel">
        <header className="live-head">
          <span className="live-head__title"><span className="live-dot" aria-hidden="true" />Ассистент слушает…</span>
        </header>
      </div>
    );
  }
  return <LivePanel endpoint={endpoint} />;
}
