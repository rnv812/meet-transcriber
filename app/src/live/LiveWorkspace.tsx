/**
 * Рабочая область ассистента — общая для плавающей панели и карточки записи.
 *
 * Узкая — вкладки «Лента · Сводка · Подсказки · Спросить» с маленькими
 * счётчиками нового. Широкая (от 720 px) — две колонки: слева лента, справа
 * сводка и подсказки, «Спросить» внизу правой колонки; счётчики не нужны —
 * видно всё. В широкой размеры областей человек подбирает сам: ширину правой
 * колонки, высоту сводки и развёрнутого «Спросить» (разделители, `LIVE_PANES`).
 * В узкой, если по высоте хватает места, над вкладкой сводки, подсказок или
 * «Спросить» остаётся полоса ленты — её высоту тоже можно подобрать.
 *
 * Состояние вида (`useLiveView`) живёт у владельца, а не здесь: свёрнутой
 * панели тоже нужно знать, сколько подсказок человек ещё не видел, и куда
 * развернуться по щелчку.
 */

import {
  type KeyboardEvent, type ReactNode, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
} from "react";

import { clock } from "../lib/format";
import type { LiveCatchup, LiveHint, LiveQuick } from "../lib/types";
import { Button } from "../ui/Button";
import { PaneResizer } from "../ui/PaneResizer";
import { ChatWorkspace } from "./ChatWorkspace";
import { LiveAsk } from "./LiveAsk";
import { type FeedFocus, LiveFeed } from "./LiveFeed";
import { LiveHints } from "./LiveHints";
import { LiveSummary } from "./LiveSummary";
import { hintKey, summaryEntries } from "./liveModel";
import { useFresh, useUnseen } from "./useAttention";
import type { Chat } from "./useChat";
import type { Live } from "./useLive";
import "./live.css";

export type LiveTab = "feed" | "summary" | "hints" | "ask";

/** Где рабочая область: плавающая панель или карточка записи — размеры областей у каждой свои. */
export type LivePlace = "panel" | "card";

/** Промежутки сетки и правой колонки (live.css). */
const GRID_GAP = 12;
const SIDE_GAP = 10;

/**
 * Пределы областей широкой раскладки. Пока их не тянули, размеры — по CSS
 * (колонка — 2/5 ширины, подсказки и сводка — 3 : 2, «Спросить» — по
 * содержимому, не выше 30 %); потянули — запоминаются (`meet.pane.live-*`,
 * у карточки — `meet.pane.live-card-*`). Верхних пределов нет: ленте и
 * подсказкам всегда остаётся их минимум, остальное — как потянули.
 */
export const LIVE_PANES = {
  feedMin: 240,
  hintsMin: 96,
  /** Минимум колонки — как у сетки по умолчанию (`minmax(280px, 2fr)` в live.css). */
  side: { min: 280, reserve: 240 + GRID_GAP },
  summary: { min: 64 },
  ask: { min: 88, reserve: 96 + 64 + 2 * SIDE_GAP },
  /** Узкая: полоса ленты над вкладкой (по умолчанию ~35 %), вкладке — не меньше 120 px. */
  narrowFeed: { min: 48, reserve: 120 + 8 },
  /**
   * Полоса ленты в узкой — только если рабочая область не ниже этого: у
   * плавающей панели это окно от ~360 px (шапка и поля — около 60 px).
   */
  stripFrom: 300,
} as const;

/**
 * Высота элемента (`find` — после отрисовки, не во время неё); следит
 * ResizeObserver — меряется, только когда элемент меняет размер, а не на
 * каждую новую строку. `on: false` — не меряется (0).
 */
function useHeight(find: () => HTMLElement | null | undefined, on: boolean): number {
  const [height, setHeight] = useState(0);
  const get = useRef(find);
  get.current = find;
  useLayoutEffect(() => {
    const el = on ? get.current() : null;
    if (!el) {
      setHeight(0);
      return;
    }
    const measure = () => setHeight(el.offsetHeight);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, [on]);
  return height;
}

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
 * «Скрыть» с возможностью передумать: скрытие уходит ассистенту сразу (он
 * запоминает текст — «не предлагать снова» — в тот же момент), а UNDO_MS
 * панель предлагает «Вернуть» — это отдельное действие `restore`. Подсказку
 * успели убрать на стороне ассистента (ответ «ничего не изменилось») —
 * «Вернуть» не предлагается: возвращать нечего.
 */
function useUndoDismiss(live: Live) {
  const [hidden, setHidden] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const liveRef = useRef(live);
  liveRef.current = live;
  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setHidden(null);
  }, []);
  const hide = useCallback((hint: LiveHint) => {
    if (timer.current) clearTimeout(timer.current);
    setHidden(hint.id);
    timer.current = setTimeout(clear, UNDO_MS);
    // «Вернуть» — только если скрытие дошло и что-то изменило: подсказку уже
    // убрал ассистент (false) или запрос не дошёл (без ответа) — возвращать нечего.
    void Promise.resolve(liveRef.current.hint(hint.id, "dismiss")).then((changed) => {
      if (changed !== true) setHidden((cur) => (cur === hint.id ? null : cur));
    });
  }, [clear]);
  const undo = useCallback(() => {
    if (hidden) void liveRef.current.hint(hidden, "restore");
    clear();
  }, [hidden, clear]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return { hidden, hide, undo };
}

/** Сколько «Спросить» остаётся развёрнутым после ответа, если им не пользуются. */
export const ASK_STAY_MS = 60_000;

/**
 * «Спросить» в широкой панели: пока им не пользуются — одна строка (поле),
 * развёрнут — история и быстрые вопросы (не выше ~30 % колонки). Развёрнут,
 * когда фокус внутри, пока ждём ответ и ASK_STAY_MS после него — чтобы ответ
 * не свернулся, едва дописавшись.
 */
function AskSection({ waiting, answers, failed, resizer, children }: {
  waiting: boolean; answers: number; failed: boolean;
  /** Разделитель высоты — только у развёрнутого: у одной строки высоты нет. */
  resizer: ReactNode;
  children: ReactNode;
}) {
  const [focused, setFocused] = useState(false);
  const [recent, setRecent] = useState(false);
  const was = useRef({ waiting, answers });
  useEffect(() => {
    const before = was.current;
    was.current = { waiting, answers };
    if ((before.waiting && !waiting) || answers > before.answers) {
      setRecent(true);
      const t = setTimeout(() => setRecent(false), ASK_STAY_MS);
      return () => clearTimeout(t);
    }
  }, [waiting, answers]);
  // Вопрос не дошёл — развернуть: ошибку видно, и вопрос можно повторить.
  const open = focused || waiting || recent || failed;
  return (
    <section className={`live-ws__ask${open ? "" : " live-ws__ask--compact"}`} aria-label="Спросить"
      onFocus={() => setFocused(true)}
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false); }}>
      {open && resizer}
      {children}
    </section>
  );
}

/** Вопрос агенту по «Вам вопрос»: что ответить. */
export function urgentQuestion(hint: LiveHint): string {
  return `Что мне ответить: «${hint.text}»? Предложите 1–2 коротких варианта от первого лица.`;
}

/**
 * Ассистент включён посреди записи и догоняет уже записанное: полоса хода над
 * рабочей областью. Строки начала встречи встают в ленту выше живых.
 */
export function CatchupNote({ catchup }: { catchup: LiveCatchup }) {
  const from = catchup.capped && catchup.from_t != null ? clock(catchup.from_t) : null;
  return (
    <div className="live-catchup" role="status"
      title="Ассистента включили посреди записи: он распознаёт уже записанное, чтобы сводка и подсказки знали начало встречи">
      <span className="live-catchup__text">Догоняю начало встречи… <span className="num">{catchup.percent} %</span></span>
      <span className="live-catchup__bar" aria-hidden="true">
        <span className="live-catchup__fill" style={{ width: `${Math.max(0, Math.min(100, catchup.percent))}%` }} />
      </span>
      {from && <span className="live-catchup__note muted">с {from}; раньше — в итогах по полной расшифровке</span>}
    </div>
  );
}

/** Агент-участник включён: окно показывает чат (`ChatWorkspace`), а не подсказки и «Спросить». */
export function participantOn(live: Live, chat?: Chat | null): boolean {
  return !!(chat && (chat.agent || live.agent));
}

/** До первого состояния ассистента: причина в строке ввода чата. */
export const CONNECTING = "Подключаюсь к ассистенту…";

/**
 * Рабочая область: с агентом-участником — чат (`ChatWorkspace`), без него
 * (`assist.participant` выключен, «Только сводка») — прежняя раскладка. Пока
 * первое состояние не пришло — уже чат (агент-участник по умолчанию включён)
 * с «Подключаюсь…» в строке ввода, а не пустой экран: чат не прячется ни при
 * подключении, ни при обрыве связи. Агента однажды видели — чат остаётся
 * (`chat.agent` не сбрасывается), прежняя раскладка — только по настройке.
 */
export function LiveWorkspace(props: {
  live: Live;
  view: LiveView;
  onAsk: (question: string, quick?: LiveQuick) => void | Promise<void>;
  onAskHint?: (hint: LiveHint) => void;
  disabled?: boolean;
  place?: LivePlace;
  /** Чат агента-участника (`useChat`): есть агент — показывается он. */
  chat?: Chat | null;
}) {
  const { chat, ...rest } = props;
  if (chat && (participantOn(props.live, chat) || !props.live.loaded)) {
    return <ChatWorkspace live={props.live} chat={chat} view={props.view} disabled={props.disabled} place={props.place} />;
  }
  return <ClassicWorkspace {...rest} />;
}

function ClassicWorkspace({ live, view, onAsk, disabled = false, onAskHint, place = "panel" }: {
  live: Live;
  view: LiveView;
  onAsk: (question: string, quick?: LiveQuick) => void | Promise<void>;
  /** «Спросить об этом» у подсказки — не в «Спросить», а сюда (карточка: агенту). */
  onAskHint?: (hint: LiveHint) => void;
  /** Режим кончается — спрашивать уже некого. */
  disabled?: boolean;
  /** Чьи размеры областей помнить: плавающей панели или карточки. */
  place?: LivePlace;
}) {
  const { tab, setTab, focus, jump, draft, setDraft, askAbout, openAsk, unseen, fresh, quiet, wide } = view;
  const uid = useId();
  const side = useRef<HTMLDivElement>(null);
  const narrow = useRef<HTMLDivElement>(null);
  const key = place === "card" ? "live-card" : "live";
  // Сколько сейчас занимает «Спросить» (одна строка, развёрнут, растёт ответ) — предел сводки.
  const askHeight = useHeight(() => side.current?.querySelector<HTMLElement>(".live-ws__ask"), wide);
  // Узкая: хватает ли высоты на полосу ленты над вкладкой.
  const tall = useHeight(() => narrow.current, !wide) >= LIVE_PANES.stripFrom;
  const dismissal = useUndoDismiss(live);
  const undoBtn = useRef<HTMLButtonElement>(null);
  const hintAction = (id: string, action: "pin" | "unpin" | "dismiss") => {
    const target = live.hints.find((h) => h.id === id);
    if (action === "dismiss" && target) dismissal.hide(target);
    else void live.hint(id, action);
  };
  // Кнопка «Скрыть» ушла вместе с карточкой — фокус на «Вернуть», а не на <body>.
  useEffect(() => { if (dismissal.hidden) undoBtn.current?.focus(); }, [dismissal.hidden]);
  // «Подсказка скрыта · Вернуть» — плашкой поверх низа области: ничего не сдвигает.
  const undoBar = dismissal.hidden && (
    <div className="live-undo" role="status">
      <span className="live-undo__text">Подсказка скрыта</span>
      <Button ref={undoBtn} size="sm" variant="link" onClick={dismissal.undo}>Вернуть</Button>
    </div>
  );

  const feed = <LiveFeed lines={live.lines} className="live-ws__feed" focus={focus} />;
  const catchup = live.catchup?.active ? <CatchupNote catchup={live.catchup} /> : null;
  const summary = <LiveSummary summary={live.summary} fresh={fresh} />;
  const hints = (
    <LiveHints hints={live.hints} fresh={fresh} enabled={live.hintsEnabled} error={live.hintError} quiet={quiet}
      onAction={hintAction} onAsk={onAskHint ?? askAbout} onTime={jump}
      onAskUrgent={onAskHint ?? ((h) => { void onAsk(urgentQuestion(h)); openAsk(); })}
      askTitle={onAskHint ? "Спросить агента об этой подсказке: откроется вкладка «Агент»" : undefined} />
  );
  const ask = (
    <LiveAsk qa={live.qa} asking={live.asking} error={live.askError} onAsk={onAsk} disabled={disabled}
      draft={draft} onDraft={setDraft} onTime={jump} />
  );

  if (wide) {
    // Сводке не больше, чем оставляют подсказкам их минимум и «Спросить» — сколько он сейчас занимает.
    const summarySpec = { ...LIVE_PANES.summary, reserve: LIVE_PANES.hintsMin + 2 * SIDE_GAP + askHeight };
    return (
      <div className={`live-ws live-ws--wide${catchup ? " live-ws--catchup" : ""}`}>
        {catchup}
        {feed}
        <PaneResizer name={`${key}-side`} cssVar="--live-side" spec={LIVE_PANES.side} panel="after"
          label="Ширина колонки подсказок" className="live-ws__split-side" />
        {/* Сначала подсказки («Вам вопрос» — первым) со своей прокруткой; ниже —
            сводка со своей; «Спросить» — внизу, не выше ~30 % и в одну строку,
            пока им не пользуются. Между ними — разделители высоты. */}
        <div ref={side} className="live-ws__side">
          <section className="live-ws__pane live-ws__pane--hints" aria-label="Подсказки">
            <h3 className="live-ws__title">
              Подсказки{live.hints.length > 0 && <span className="live-ws__n num"> · {live.hints.length}</span>}
            </h3>
            <div className="live-ws__scroll">{hints}</div>
          </section>
          <PaneResizer name={`${key}-summary`} cssVar="--live-summary" spec={summarySpec} panel="after" axis="y"
            label="Высота сводки" cssValue={(h) => `0 1 ${h}px`} />
          <section className="live-ws__pane live-ws__pane--summary" aria-label="Сводка">
            <h3 className="live-ws__title">Сводка</h3>
            <div className="live-ws__scroll">{summary}</div>
          </section>
          <AskSection waiting={live.asking || live.qa.some((q) => q.pending)} answers={live.qa.length}
            failed={!!live.askError || live.qa.some((q) => !!q.error && !q.pending)}
            resizer={(
              <PaneResizer name={`${key}-ask`} cssVar="--live-ask" spec={LIVE_PANES.ask} panel="after" axis="y"
                label="Высота «Спросить»" area={(h) => h.parentElement?.parentElement ?? null}
                pane={(h) => h.parentElement} />
            )}>{ask}</AskSection>
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
  const strip = tall && tab !== "feed";
  return (
    <div ref={narrow} className="live-ws">
      {catchup}
      <div className="live-tabs" role="tablist" aria-label="Ассистент" onKeyDown={onKey}>
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" id={`${uid}-tab-${t.id}`} aria-selected={tab === t.id}
            tabIndex={tab === t.id ? 0 : -1}
            aria-controls={`${uid}-panel`} className="live-tabs__tab" onClick={() => setTab(t.id)}>
            {t.label}{count(counts[t.id], quiet)}
          </button>
        ))}
      </div>
      {/* Над сводкой, подсказками и «Спросить» — полоса ленты (по умолчанию
          ~35 %), её высота — разделителем; на «Ленте» лента во всю высоту. */}
      <div className="live-ws__stack">
        {strip && (
          <>
            <div className="live-ws__strip">{feed}</div>
            <PaneResizer name={`${key}-narrow-feed`} cssVar="--live-feed-h" spec={LIVE_PANES.narrowFeed} panel="before"
              axis="y" label="Высота ленты" />
          </>
        )}
        <div className={`live-ws__panel live-ws__panel--${tab}`} role="tabpanel" id={`${uid}-panel`}
          aria-labelledby={`${uid}-tab-${tab}`}>
          {tab === "feed" ? feed : tab === "summary" ? summary : tab === "hints" ? hints : ask}
        </div>
      </div>
      {undoBar}
    </div>
  );
}
