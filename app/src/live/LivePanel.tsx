/**
 * Плавающая панель ассистента (окно `live`, страница live.html).
 *
 * Окно создаёт и закрывает оболочка по `snapshot.live.active`. Панель
 * двигают за шапку (двойной щелчок по ней — на весь экран и обратно), её
 * растягивают за края. Свёрнутая — одна строка: самая важная подсказка (она
 * сменяется, только когда сменилась сама) и счётчик новых; щелчок
 * разворачивает панель на «Подсказках». Развёрнутая и на весь экран —
 * рабочая область (`LiveWorkspace`): вкладки, а в широком окне — две колонки.
 *
 * «Не отвлекать»: ни подсветки, ни счётчиков, строка свёрнутой панели не
 * меняется, пока её подсказка жива; содержимое при этом обновляется.
 * Исключение — «Вам вопрос»: он встаёт в строку свёрнутой панели и в
 * «Не отвлекать» (без анимации) — ответа ждут сейчас.
 *
 * Размер и место помнит оболочка (`useLiveWindow`). Фокус панель не берёт:
 * окно создаётся без фокуса, и ни один элемент не фокусируется сам —
 * клавиатура остаётся у звонка, пока человек не щёлкнет в панель.
 */

import { type MouseEvent, useEffect, useRef, useState } from "react";

import { type Endpoint, NoResidentError, liveStop, resolveEndpoint } from "../lib/api";
import { clock, errorText } from "../lib/format";
import { inTauri, invoke } from "../lib/shell";
import type { LiveHint } from "../lib/types";
import { Bell, BellOff, ChevronDown, ChevronUp, Maximize2, Minimize2, Pin, Square } from "lucide-react";
import { IconButton } from "../ui/IconButton";
import { Truncate } from "../ui/Truncate";
import { LiveWorkspace, useLiveView } from "./LiveWorkspace";
import { KIND_LABEL, isUrgent, topHint } from "./liveModel";
import { useQuiet } from "./useAttention";
import { useLiveAsk } from "./useLastLook";
import { useLive } from "./useLive";
import { useLiveStatus } from "./useLiveStatus";
import { useLiveWindow } from "./useLiveWindow";
import { useWide } from "./useWide";
import "./live.css";

const TICK_MS = 1000;
/** Сколько держать в шапке заметку о несработавшем действии с подсказкой. */
export const HINT_NOTE_MS = 8000;

const HINT_FAILED: Record<string, string> = {
  dismiss: "Подсказку не удалось скрыть",
  restore: "Подсказку не удалось вернуть",
  pin: "Подсказку не удалось закрепить",
  unpin: "Подсказку не удалось открепить",
};

/** Несработавшее действие с подсказкой — заметкой на HINT_NOTE_MS (карточки скрытой подсказки уже нет на экране). */
function useHintNote(error: { id: string; text: string; action?: string } | null): string | null {
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    if (!error) return;
    setNote(`${HINT_FAILED[error.action ?? ""] ?? "Действие с подсказкой не удалось"}: ${error.text}`);
    const t = setTimeout(() => setNote(null), HINT_NOTE_MS);
    return () => clearTimeout(t);
  }, [error]);
  return note;
}
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

/**
 * Подсказка строки свёрнутой панели: самая важная; сменяется, только когда
 * сменилась самая важная. «Не отвлекать» — держим показанную, пока она жива,
 * но «Вам вопрос» встаёт в строку и тогда.
 */
function useShownHint(hints: LiveHint[], quiet: boolean): LiveHint | null {
  const shownId = useRef<string | null>(null);
  const best = topHint(hints);
  const kept = quiet && shownId.current ? hints.find((h) => h.id === shownId.current) : undefined;
  const shown = best && isUrgent(best) && !(kept && isUrgent(kept)) ? best : kept ?? best;
  shownId.current = shown?.id ?? null;
  return shown;
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
  const [quiet, setQuiet] = useQuiet(live.quietDefault);
  const root = useRef<HTMLDivElement>(null);
  const wide = useWide(root);
  // На весь экран — всё содержимое, как у развёрнутой.
  const open = view.expanded || view.maximized;
  const ask = useLiveAsk(live, open);
  const ws = useLiveView(live, { open, wide, quiet });
  const shown = useShownHint(live.hints, quiet);
  const hintNote = useHintNote(live.hintError);

  // Esc возвращает обычный размер (клавиатура у панели, только если по ней
  // щёлкнули). В поле вопроса Esc — дело поля, окно не трогаем.
  useEffect(() => {
    if (!view.maximized) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || (e.target as Element | null)?.closest?.("input, textarea")) return;
      setMaximized(false);
    };
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

  const openHints = () => {
    ws.setTab(shown ? "hints" : "feed");
    setExpanded(true);
  };

  // Коротко: шапка узкой панели (от 300 px) вмещает таймер, состояние и пять кнопок.
  const state = stopping ? "Останавливаю…" : "Слушает";
  const sizeLabel = view.maximized ? "Обычный размер" : "На весь экран";
  const last = live.lines.at(-1);
  const newHints = quiet ? 0 : ws.unseen.hints;
  const mods = `${open ? " live-panel--open" : ""}${view.maximized ? " live-panel--maximized" : ""}`;
  // Свёрнутую панель можно тащить за любое свободное место, а не только за шапку.
  const dragAnywhere = (e: MouseEvent) => {
    if (!open && !(e.target as Element).closest(".live-head")) headPress(e, startDrag);
  };
  return (
    <div ref={root} className={`live-panel${mods}${quiet ? " live-panel--quiet" : ""}`} onMouseDown={dragAnywhere}>
      <header className="live-head" onMouseDown={(e) => headPress(e, startDrag, () => setMaximized(!view.maximized))}>
        <span className="live-head__title" title={stopping ? state : "Ассистент слушает встречу"}>
          <span className="live-dot" aria-hidden="true" />
          <span className="num">{elapsed === null ? "—" : clock(elapsed)}</span> · {state}
          {quiet && <span className="live-head__quiet-tag">тихо</span>}
          {/* Ошибки и связь — в той же строке состояния шапки (одна, по важности):
              не закрывают поле вопроса и действия и не сдвигают содержимое. */}
          {stopError ? (
            <span className="live-status live-status--error" role="alert" title={stopError}>
              <span className="live-status__dot" aria-hidden="true" />
              <span className="live-status__text">{stopError}</span>
            </span>
          ) : hintNote ? (
            <span className="live-status live-status--error" role="alert" title={hintNote}>
              <span className="live-status__dot" aria-hidden="true" />
              <span className="live-status__text">{hintNote}</span>
            </span>
          ) : live.error ? (
            <span className="live-status" role="status" title={live.error}>
              <span className="live-status__dot" aria-hidden="true" />
              <span className="live-status__text">{live.error}</span>
            </span>
          ) : live.status && !stopping && (
            <span className="live-status" role="status" title={live.status}>
              <span className="live-status__dot" aria-hidden="true" />
              <span className="live-status__text">{live.status}</span>
            </span>
          )}
        </span>
        {/* Все кнопки шапки — значки 28 px одного вида при любой ширине; подпись — в подсказке. */}
        <span className="live-head__actions">
          <IconButton icon={quiet ? BellOff : Bell} label="Не отвлекать" pressed={quiet} className="live-head__quiet"
            tooltip={quiet ? "«Не отвлекать» включено: без подсветки и счётчиков" : "Не отвлекать: без подсветки и счётчиков"}
            onClick={() => setQuiet(!quiet)} />
          <IconButton icon={Pin} label="Поверх всех окон" pressed={view.pinned} className="live-head__pin"
            tooltip={view.pinned ? "Панель поверх всех окон — открепить" : "Закрепить поверх всех окон"}
            onClick={() => setPinned(!view.pinned)} />
          <IconButton icon={view.maximized ? Minimize2 : Maximize2} label={sizeLabel}
            tooltip={`${sizeLabel} (двойной щелчок по шапке)`} onClick={() => setMaximized(!view.maximized)} />
          <IconButton icon={open ? ChevronUp : ChevronDown} label={open ? "Свернуть" : "Развернуть"}
            aria-expanded={open} onClick={() => setExpanded(!open)} />
          <IconButton icon={Square} label="Стоп" variant="danger" className="live-head__stop"
            tooltip="Остановить и сохранить запись" onClick={stop} disabled={stopping} />
        </span>
      </header>
      {open ? (
        <div className="live-panel__body">
          <LiveWorkspace live={live} view={ws} onAsk={ask} disabled={stopping} />
        </div>
      ) : (
        <button type="button" className={`live-last${shown && isUrgent(shown) ? " live-last--urgent" : ""}`} onClick={openHints}
          aria-label={shown ? `Подсказка: ${shown.text}. Открыть подсказки` : "Развернуть панель"}>
          {shown ? (
            <>
              <span className={`live-last__kind live-hint--${shown.kind}`}>{KIND_LABEL[shown.kind]}</span>
              <Truncate className="live-last__text">{shown.text}</Truncate>
            </>
          ) : last ? (
            <Truncate className="live-last__text muted" text={`${last.speaker ? `${last.speaker}: ` : ""}${last.text}`}>
              {last.speaker && <span className="live-feed__who">{last.speaker}</span>}
              <span>{last.text}</span>
            </Truncate>
          ) : <span className="live-last__text muted">Реплики появятся, как только их расшифрует ассистент</span>}
          {newHints > 0 && <span className="live-last__count" aria-label={`новых подсказок: ${newHints}`}>{newHints}</span>}
        </button>
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
