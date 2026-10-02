/**
 * Рабочая область ассистента — общая для плавающей панели и карточки записи.
 *
 * Узкая — вкладки «Лента · Сводка · Подсказки · Спросить» с маленькими
 * счётчиками нового. Широкая (от 720 px) — две колонки: слева лента, справа
 * сводка и подсказки, «Спросить» внизу правой колонки; счётчики не нужны —
 * видно всё.
 *
 * Состояние вида (`useLiveView`) живёт у владельца, а не здесь: свёрнутой
 * панели тоже нужно знать, сколько подсказок человек ещё не видел, и куда
 * развернуться по щелчку.
 */

import { type KeyboardEvent, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import type { LiveHint, LiveQuick } from "../lib/types";
import { Button } from "../ui/Button";
import { LiveAsk } from "./LiveAsk";
import { type FeedFocus, LiveFeed } from "./LiveFeed";
import { LiveHints } from "./LiveHints";
import { LiveSummary } from "./LiveSummary";
import { hintKey, summaryEntries } from "./liveModel";
import { useFresh, useUnseen } from "./useAttention";
import type { Live } from "./useLive";
import "./live.css";

export type LiveTab = "feed" | "summary" | "hints" | "ask";

const TABS: { id: LiveTab; label: string }[] = [
  { id: "feed", label: "Лента" },
  { id: "summary", label: "Сводка" },
  { id: "hints", label: "Подсказки" },
  { id: "ask", label: "Спросить" },
];

export type LiveView = ReturnType<typeof useLiveView>;

/**
 * Вид рабочей области: вкладка, переходы к моменту встречи, текст вопроса,
 * что подсвечено и сколько нового. `open` — область на экране (панель
 * развёрнута), `wide` — две колонки, `quiet` — «Не отвлекать».
 */
export function useLiveView(live: Live, { open, wide, quiet }: { open: boolean; wide: boolean; quiet: boolean }) {
  const [tab, setTab] = useState<LiveTab>("feed");
  const [focus, setFocus] = useState<FeedFocus | null>(null);
  const [draft, setDraft] = useState("");
  const shown = (t: LiveTab) => open && (wide || tab === t);

  const summaryList = useMemo(() => summaryEntries(live.summary), [live.summary]);
  const hintList = useMemo<[string, string][]>(() => live.hints.map((h) => [hintKey(h), h.kind]), [live.hints]);
  const answered = live.qa.filter((q) => !q.pending).map((q) => `${q.id}`);
  const unseen = {
    summary: useUnseen(summaryList.map(([k, v]) => `${k}:${v}`), shown("summary"), live.loaded),
    hints: useUnseen(hintList.map(([k]) => k), shown("hints"), live.loaded),
    ask: useUnseen(answered, shown("ask"), live.loaded),
  };
  const fresh = useFresh([...summaryList, ...hintList], { enabled: !quiet, ready: live.loaded });

  const jump = useCallback((t: number) => {
    if (!wide) setTab("feed");
    setFocus((f) => ({ t, seq: (f?.seq ?? 0) + 1 }));
  }, [wide]);
  const askAbout = useCallback((hint: LiveHint) => {
    setDraft(`Расскажите подробнее: «${hint.text}»`);
    if (!wide) setTab("ask");
  }, [wide]);
  const openAsk = useCallback(() => { if (!wide) setTab("ask"); }, [wide]);

  return { tab, setTab, focus, jump, draft, setDraft, askAbout, openAsk, unseen, fresh, quiet, wide };
}

function count(n: number, quiet: boolean) {
  if (quiet || n <= 0) return null;
  return <span className="live-tabs__count" aria-label={`новых: ${n}`}>{n}</span>;
}

/** Сколько «Скрыть» ещё можно отменить. */
export const UNDO_MS = 5000;

/**
 * «Скрыть» с возможностью передумать: подсказка сразу пропадает с экрана, а
 * ассистенту уходит через UNDO_MS — до этого «Вернуть» её возвращает.
 * Следующее «Скрыть» и уход со страницы отправляют отложенное сразу.
 */
function useUndoDismiss(send: (id: string) => void) {
  const [hidden, setHidden] = useState<{ id: string; text: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<string | null>(null);
  const sendRef = useRef(send);
  sendRef.current = send;
  const flush = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const id = pending.current;
    pending.current = null;
    if (id) sendRef.current(id);
  }, []);
  const hide = useCallback((hint: LiveHint) => {
    flush();
    pending.current = hint.id;
    setHidden({ id: hint.id, text: hint.text });
    timer.current = setTimeout(() => { flush(); setHidden(null); }, UNDO_MS);
  }, [flush]);
  const undo = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    pending.current = null;
    setHidden(null);
  }, []);
  useEffect(() => () => flush(), [flush]);
  return { hidden, hide, undo };
}

/** Вопрос агенту по «Вам вопрос»: что ответить. */
export function urgentQuestion(hint: LiveHint): string {
  return `Что мне ответить: «${hint.text}»? Предложите 1–2 коротких варианта от первого лица.`;
}

export function LiveWorkspace({ live, view, onAsk, disabled = false, onAskHint }: {
  live: Live;
  view: LiveView;
  onAsk: (question: string, quick?: LiveQuick) => void | Promise<void>;
  /** «Спросить об этом» у подсказки — не в «Спросить», а сюда (карточка: агенту). */
  onAskHint?: (hint: LiveHint) => void;
  /** Режим кончается — спрашивать уже некого. */
  disabled?: boolean;
}) {
  const { tab, setTab, focus, jump, draft, setDraft, askAbout, openAsk, unseen, fresh, quiet, wide } = view;
  const uid = useId();
  const dismissal = useUndoDismiss((id) => { void live.hint(id, "dismiss"); });
  const hintAction = (id: string, action: "pin" | "unpin" | "dismiss") => {
    const target = live.hints.find((h) => h.id === id);
    if (action === "dismiss" && target) dismissal.hide(target);
    else void live.hint(id, action);
  };
  const visibleHints = dismissal.hidden ? live.hints.filter((h) => h.id !== dismissal.hidden!.id) : live.hints;
  // «Подсказка скрыта · Вернуть» — плашкой поверх низа области: ничего не сдвигает.
  const undoBar = dismissal.hidden && (
    <div className="live-undo" role="status">
      <span className="live-undo__text">Подсказка скрыта</span>
      <Button size="sm" variant="link" onClick={dismissal.undo}>Вернуть</Button>
    </div>
  );

  const feed = <LiveFeed lines={live.lines} className="live-ws__feed" focus={focus} />;
  const summary = <LiveSummary summary={live.summary} fresh={fresh} />;
  const hints = (
    <LiveHints hints={visibleHints} fresh={fresh} enabled={live.hintsEnabled} error={live.hintError}
      onAction={hintAction} onAsk={onAskHint ?? askAbout} onTime={jump}
      onAskUrgent={onAskHint ?? ((h) => { void onAsk(urgentQuestion(h)); openAsk(); })}
      askTitle={onAskHint ? "Спросить агента об этой подсказке: откроется вкладка «Агент»" : undefined} />
  );
  const ask = (
    <LiveAsk qa={live.qa} asking={live.asking} error={live.askError} onAsk={onAsk} disabled={disabled}
      draft={draft} onDraft={setDraft} onTime={jump} />
  );

  if (wide) {
    return (
      <div className="live-ws live-ws--wide">
        {feed}
        {/* Сводка и подсказки — одна прокрутка (раньше у каждой своя, и карточки
            резались посередине), «Спросить» — внизу колонки. */}
        <div className="live-ws__side">
          <section className="live-ws__pane" aria-label="Сводка">
            <h3 className="live-ws__title">Сводка</h3>
            {summary}
          </section>
          <section className="live-ws__pane" aria-label="Подсказки">
            <h3 className="live-ws__title">Подсказки</h3>
            {hints}
          </section>
          <section className="live-ws__ask" aria-label="Спросить">{ask}</section>
        </div>
        {undoBar}
      </div>
    );
  }

  const counts: Record<LiveTab, number> = { feed: 0, summary: unseen.summary, hints: unseen.hints, ask: unseen.ask };
  // Клавиатура как у вкладок: стрелки — соседняя (по кругу), Home/End — края;
  // в порядке Tab — только выбранная вкладка (роуминг tabIndex).
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const at = TABS.findIndex((t) => t.id === tab);
    const next = e.key === "ArrowRight" ? (at + 1) % TABS.length
      : e.key === "ArrowLeft" ? (at - 1 + TABS.length) % TABS.length
        : e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : -1;
    if (next < 0) return;
    e.preventDefault();
    setTab(TABS[next]!.id);
    document.getElementById(`${uid}-tab-${TABS[next]!.id}`)?.focus();
  };
  return (
    <div className="live-ws">
      <div className="live-tabs" role="tablist" aria-label="Ассистент" onKeyDown={onKey}>
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" id={`${uid}-tab-${t.id}`} aria-selected={tab === t.id}
            tabIndex={tab === t.id ? 0 : -1}
            aria-controls={`${uid}-panel`} className="live-tabs__tab" onClick={() => setTab(t.id)}>
            {t.label}{count(counts[t.id], quiet)}
          </button>
        ))}
      </div>
      <div className={`live-ws__panel live-ws__panel--${tab}`} role="tabpanel" id={`${uid}-panel`}
        aria-labelledby={`${uid}-tab-${tab}`}>
        {tab === "feed" ? feed : tab === "summary" ? summary : tab === "hints" ? hints : ask}
      </div>
      {undoBar}
    </div>
  );
}
