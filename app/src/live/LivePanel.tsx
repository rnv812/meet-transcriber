/**
 * Плавающая панель ассистента (окно `live`, страница live.html).
 *
 * Окно создаёт и закрывает оболочка по `snapshot.live.active`. Панель
 * двигают за шапку (двойной щелчок по ней — на весь экран и обратно), её
 * растягивают за края; свёрнутая показывает последнюю реплику, развёрнутая и
 * на весь экран — ленту, дайджест и вопросы. Размер и место помнит оболочка
 * (`useLiveWindow`). Фокус панель не берёт: окно создаётся без фокуса, и ни
 * один элемент не фокусируется сам — клавиатура остаётся у звонка, пока
 * человек не щёлкнет в панель.
 */

import { type MouseEvent, useEffect, useState } from "react";

import { type Endpoint, NoResidentError, liveStop, resolveEndpoint } from "../lib/api";
import { clock, errorText } from "../lib/format";
import { inTauri, invoke } from "../lib/shell";
import { Button } from "../ui/Button";
import { LiveAsk } from "./LiveAsk";
import { LiveDigest, LiveFeed } from "./LiveFeed";
import { useLive } from "./useLive";
import { useLiveStatus } from "./useLiveStatus";
import { useLiveWindow } from "./useLiveWindow";
import "./live.css";

const TICK_MS = 1000;
const FIND_MS = 2000;

/**
 * Секунды с `started_at` (стенное время резидента, когда ассистент начал
 * слушать), тикает раз в секунду; null — время начала неизвестно.
 */
function useElapsed(startedAt: number | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt == null) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [startedAt]);
  return startedAt == null ? null : Math.max(0, now / 1000 - startedAt);
}

/** Кнопки, поля и ссылки шапки окно не таскают: по ним щёлкают. */
const CONTROLS = "button, input, textarea, select, a, [role='button']";

/**
 * Нажатие левой кнопкой на свободное место шапки: одиночное — тащить окно
 * (системное перетаскивание, оболочка `live_start_drag`), двойное — на весь
 * экран и обратно. Как `data-tauri-drag-region`, но двойной щелчок знает
 * вид панели и проверяется тестами.
 */
export function headPress(e: MouseEvent, drag: () => void, toggle?: () => void) {
  if (e.button !== 0 || (e.target as Element).closest(CONTROLS)) return;
  e.preventDefault(); // без выделения текста шапки
  if (e.detail === 2) toggle?.();
  else if (e.detail === 1) drag();
}

function MaximizeIcon({ maximized }: { maximized: boolean }) {
  return maximized ? (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M2.5 9.5h4v4M13.5 6.5h-4v-4M6.5 9.5l-4.5 4.5M9.5 6.5L14 2" />
    </svg>
  ) : (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5L9 7M2.5 13.5L7 9" />
    </svg>
  );
}

function PinIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round">
      <path d="M6 2.5h4M7 2.5v4L4.5 9h7L9 6.5v-4M8 9v4.5" />
    </svg>
  );
}

export function LivePanel({ endpoint }: { endpoint: Endpoint }) {
  const status = useLiveStatus(endpoint);
  const [stopRequested, setStopRequested] = useState(false);
  const stopping = stopRequested || !!status?.stopping;
  // До первого снимка считаем режим идущим: окно существует только при нём.
  // Дописывает запись — поток резидент уже закрыл, переподключаться незачем
  // (иначе «Нет связи с ассистентом» на всё время остановки).
  const live = useLive(endpoint, (status ? status.active : true) && !stopping);
  const elapsed = useElapsed(status?.started_at);
  const { view, setExpanded, setMaximized, setPinned, startDrag } = useLiveWindow();
  const [stopError, setStopError] = useState<string | null>(null);
  // На весь экран — всё содержимое, как у развёрнутой.
  const open = view.expanded || view.maximized;

  // Esc возвращает обычный размер (клавиатура у панели, только если по ней щёлкнули).
  useEffect(() => {
    if (!view.maximized) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMaximized(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view.maximized, setMaximized]);

  const stop = () => {
    setStopRequested(true);
    setStopError(null);
    liveStop(endpoint).catch((e) => {
      setStopRequested(false);
      setStopError(errorText(e));
    });
  };

  const state = stopping ? "Останавливаю…" : "Ассистент слушает";
  const sizeLabel = view.maximized ? "Обычный размер" : "На весь экран";
  const last = live.lines.at(-1);
  const mods = `${open ? " live-panel--open" : ""}${view.maximized ? " live-panel--maximized" : ""}`;
  return (
    <div className={`live-panel${mods}`}>
      <header className="live-head" onMouseDown={(e) => headPress(e, startDrag, () => setMaximized(!view.maximized))}>
        <span className="live-head__title" title={state}>
          <span className="live-dot" aria-hidden="true" />
          <span className="num">{elapsed === null ? "—" : clock(elapsed)}</span> · {state}
        </span>
        <span className="live-head__actions">
          <button
            type="button" className="icon-btn live-head__pin" aria-pressed={view.pinned}
            aria-label="Поверх всех окон"
            title={view.pinned ? "Панель поверх всех окон — открепить" : "Закрепить поверх всех окон"}
            onClick={() => setPinned(!view.pinned)}
          >
            <PinIcon />
          </button>
          <button
            type="button" className="icon-btn" aria-label={sizeLabel} title={`${sizeLabel} (двойной щелчок по шапке)`}
            onClick={() => setMaximized(!view.maximized)}
          >
            <MaximizeIcon maximized={view.maximized} />
          </button>
          <Button aria-expanded={open} onClick={() => setExpanded(!open)}>
            {open ? "Свернуть" : "Развернуть"}
          </Button>
          <Button variant="danger" onClick={stop} disabled={stopping}>Стоп</Button>
        </span>
      </header>
      {stopError && <div className="live-panel__error" role="alert">{stopError}</div>}
      {live.error && <div className="live-panel__note muted">{live.error}</div>}
      {open ? (
        <div className="live-panel__body">
          <LiveFeed lines={live.lines} className="live-panel__feed" />
          <div className="live-panel__side">
            <LiveDigest digest={live.digest} defaultOpen={false} />
            <LiveAsk reply={live.reply} onAsk={live.ask} disabled={stopping} />
          </div>
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

/** Тащить окно, пока панели ещё нет (резидента ищем). */
function dragWindow() {
  if (inTauri()) invoke("live_start_drag").catch((cause) => console.warn("live_start_drag:", cause));
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
        <header className="live-head" onMouseDown={(e) => headPress(e, dragWindow)}>
          <span className="live-head__title"><span className="live-dot" aria-hidden="true" />Ассистент слушает…</span>
        </header>
      </div>
    );
  }
  return <LivePanel endpoint={endpoint} />;
}
