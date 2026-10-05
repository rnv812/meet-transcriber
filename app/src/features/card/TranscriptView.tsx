/**
 * Вкладка «Расшифровка»: поиск по тексту встречи над репликами.
 *
 * Правила поиска — lib/search.ts (те же у поиска по всем записям). Текст
 * разбирается один раз на транскрипт, запрос — с задержкой 150 мс. Текущее
 * совпадение выделяется прямо в DOM (`data-hit`): переход ↑/↓ по тысячам
 * реплик не перерисовывает список.
 *
 * Клавиши: Ctrl+F — к полю (его ловит CardTabs), Enter / Shift+Enter —
 * следующее / предыдущее, Esc — очистить и вернуть фокус, где он был.
 */

import {
  useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent,
  type ReactNode,
} from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { layoutRows, turnAt, typeCounts, type AnalysisView, type InsightView } from "../../lib/analysisView";
import { JiraLinks, jiraTasks } from "../../lib/jira";
import { placeOf, restorePlace, scrollParent, type Place } from "../../lib/keepPlace";
import { findHits, parseQuery, prepare } from "../../lib/search";
import type { Turn } from "../../lib/speakers";
import type { PhraseType } from "../../lib/types";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { InsightsBlock, TypeFilters } from "./markup";
import { TranscriptShown } from "./transcriptShown";
import { Turns, type PersonColor, type TurnMarks } from "./Turns";

export const FIND_DELAY_MS = 150;

/** Открыть карточку с запросом (из поиска по записям); `t` — начало нужной реплики, `n` — номер просьбы. */
export type FindRequest = { q: string; t: number | null; n: number };

/** Показать реплику (номер в `turns`): развернуть, если свёрнута фильтром, прокрутить и подсветить; `n` — номер просьбы. */
export type RevealRequest = { turn: number; n: number };

/** Плеер перемотали на секунду `t` (`n` — номер перемотки): прокрутить к реплике, которая там звучит. */
export type SeekRequest = { t: number; n: number };

const NO_FILTER: ReadonlySet<PhraseType> = new Set();
const NO_INSIGHTS: InsightView[] = [];
/** Сколько длится подсветка реплики, к которой перешли. */
const FLASH_MS = 1700;

export type { PersonColor };

/** Номер совпадения для перехода к реплике, начинающейся в `t` (или первой после). */
function hitAt(turns: Turn[], hits: { turn: number }[], t: number): number {
  const exact = turns.findIndex((x) => Math.abs(x.start - t) < 0.05);
  const turn = exact >= 0 ? exact : turns.findIndex((x) => x.start >= t);
  if (turn < 0) return 0;
  const i = hits.findIndex((h) => h.turn >= turn);
  return i < 0 ? 0 : i;
}

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
/** Куда ставить реплику при перемотке: примерно 30 % высоты от верха. */
const SEEK_TOP = "30vh";
const scrollTo = (el: Element) => el.scrollIntoView?.({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });

export function TranscriptView({
  turns, colors, playable, onPlay, onNameSpeaker, onSpeaker, selected, onSelect, onSplitAt, toolbar, find, onAskAgent,
  view = null, onAskChapter, onAskInsight, reveal = null, onRestrictSelection, tools, seekTo = null, nowTurn = null,
  textPhase = false,
}: {
  turns: Turn[];
  colors: Map<string, string>;
  playable: boolean;
  onPlay: (turn: Turn) => void;
  onNameSpeaker?: (label: string) => void;
  /** Правка спикера у реплики (TurnEdit): меню, выбор нескольких. */
  onSpeaker?: (turn: number, anchor: HTMLElement) => void;
  selected?: ReadonlySet<number>;
  /** Выбор реплик; `visible` — видимые при фильтре по типам (диапазон — только по ним). */
  onSelect?: (turn: number, how: "toggle" | "range", visible?: (turn: number) => boolean) => void;
  /** Фильтр спрятал выбранные реплики — оставить в выборе только видимые. */
  onRestrictSelection?: (visible: (turn: number) => boolean) => void;
  onSplitAt?: (turn: number, event: MouseEvent<HTMLElement>) => void;
  /** Полоса над репликами (выбранные, итог назначения). */
  toolbar?: ReactNode;
  /** Кнопки в строке поиска справа (✦ «Улучшить расшифровку»). */
  tools?: ReactNode;
  find?: FindRequest | null;
  /** ✦ «Спросить агента» у реплик (номера реплик). */
  onAskAgent?: (turns: number[]) => void;
  /** Разметка анализа встречи по репликам (что показывать — уже учтено). */
  view?: AnalysisView | null;
  /** ✦ «Обсудить главу с агентом» (номер главы в `view.chapters`). */
  onAskChapter?: (chapter: number) => void;
  /** ✦ «Обсудить с агентом» у наблюдения. */
  onAskInsight?: (insight: InsightView) => void;
  /** Показать реплику (глава из плеера). */
  reveal?: RevealRequest | null;
  /** Перемотка из плеера: прокрутить к реплике (свёрнутую фильтром не разворачивать). */
  seekTo?: SeekRequest | null;
  /** Реплика, которая звучит сейчас (номер в `turns`): отметка «сейчас играет». */
  nowTurn?: number | null;
  /** Текст до спикеров (Р4): подписи без действий; пришли спикеры — место чтения сохраняется. */
  textPhase?: boolean;
}) {
  const [text, setText] = useState(find?.q ?? "");
  const [query, setQuery] = useState(find?.q ?? "");
  const [current, setCurrent] = useState(0);
  /** Куда перейти по просьбе из списка; `n` — номер просьбы: повтор с тем же запросом — тоже переход. */
  const [jump, setJump] = useState<{ t: number | null; n: number } | null>(
    find ? { t: find.t, n: find.n } : null);
  const usedJump = useRef<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);

  // Новая просьба из списка (та же карточка, другой фрагмент) — запрос и переход сразу.
  const lastFind = useRef(find?.n);
  useEffect(() => {
    if (!find || find.n === lastFind.current) return;
    lastFind.current = find.n;
    setText(find.q);
    setQuery(find.q);
    setJump({ t: find.t, n: find.n });
  }, [find]);

  useEffect(() => {
    if (text === query) return;
    const timer = setTimeout(() => setQuery(text), FIND_DELAY_MS);
    return () => clearTimeout(timer);
  }, [text, query]);

  // Отметка перерыва — не реплика: искать в ней нечего (как в meet/search.py).
  const prepared = useMemo(
    () => turns.map((t) => (t.kind === "break" ? prepare("") : prepare(t.texts.join(" "), t.speaker))), [turns]);
  const { hits, marks } = useMemo(() => {
    const found = findHits(prepared, parseQuery(query));
    const marks: TurnMarks = new Map();
    found.hits.forEach((h, i) => {
      if (!marks.has(h.turn)) marks.set(h.turn, { ranges: found.ranges.get(h.turn) ?? [], first: i });
    });
    return { hits: found.hits, marks };
  }, [prepared, query]);

  // Фильтр по типам реплик: остальные сворачиваются в «… N реплик».
  const [filter, setFilter] = useState<ReadonlySet<PhraseType>>(NO_FILTER);
  /** Реплики, которые показаны несмотря на фильтр (развёрнуты, открыты по ссылке). */
  const [opened, setOpened] = useState<ReadonlySet<number>>(() => new Set());
  const types = view?.types ?? null;
  // Сбрасываются со сменой фильтра или числа реплик; правка слова или спикера
  // (перечитанная запись, новые `turns`) развёрнутое не сворачивает.
  const turnCount = turns.length;
  useEffect(() => { setOpened(new Set()); }, [filter, turnCount]);
  // Типов в анализе больше нет (выключили, анализ устарел) — фильтр снимается.
  useEffect(() => { if (!types) setFilter(NO_FILTER); }, [types]);
  const counts = useMemo(() => typeCounts(types), [types]);
  const active = query.trim() !== "";
  const rows = useMemo(() => {
    if (!view) return undefined;
    return layoutRows(turns, {
      types, filter, chapterStart: view.chapters.length ? view.chapterStart : null,
      shown: (i) => opened.has(i) || (active && marks.has(i)),
    });
  }, [view, turns, types, filter, opened, active, marks]);
  const expand = useCallback((from: number) => {
    const row = rows?.find((r) => r.kind === "more" && r.from === from);
    if (!row || row.kind !== "more") return;
    setOpened((cur) => {
      const next = new Set(cur);
      for (let i = row.from; i <= row.to; i++) next.add(i);
      return next;
    });
  }, [rows]);
  // С фильтром выбор (и действия над выбранным) — только среди видимых реплик.
  const visible = useMemo(
    () => (filter.size && rows ? new Set(rows.flatMap((r) => (r.kind === "turn" ? [r.i] : []))) : null),
    [filter, rows]);
  const select = useCallback((t: number, how: "toggle" | "range") => {
    if (visible) onSelect?.(t, how, (i) => visible.has(i));
    else onSelect?.(t, how);
  }, [onSelect, visible]);
  useEffect(() => {
    if (!visible || !selected || !onRestrictSelection) return;
    for (const i of selected) {
      if (!visible.has(i)) { onRestrictSelection((k) => visible.has(k)); return; }
    }
  }, [visible, selected, onRestrictSelection]);
  // Фильтр скрыл все главы целиком — вместо пустой ленты подсказка со сбросом.
  const noMatches = !!view?.chapters.length && filter.size > 0 && !!rows && !rows.some((r) => r.kind === "turn");
  const annotations = useMemo(
    () => (view ? { types: view.types, key: view.key, chapters: view.chapters } : null), [view]);

  // Переход к реплике (ссылка наблюдения, глава из плеера): показать, прокрутить, подсветить.
  const [goto, setGoto] = useState<RevealRequest | null>(null);
  const lastReveal = useRef(reveal?.n);
  useEffect(() => {
    if (!reveal || reveal.n === lastReveal.current) return;
    lastReveal.current = reveal.n;
    setGoto(reveal);
  }, [reveal]);
  const jumpTo = useCallback((turn: number) => setGoto((g) => ({ turn, n: (g?.n ?? 0) + 1 })), []);
  // «Задачи»: задачи Jira, названные во встрече (ссылки резидента по репликам).
  const jira = useContext(JiraLinks);
  const tasks = useMemo(() => jiraTasks(jira?.turns), [jira]);
  useEffect(() => {
    if (!goto) return;
    setOpened((cur) => (cur.has(goto.turn) ? cur : new Set(cur).add(goto.turn)));
  }, [goto]);
  const doneGoto = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (!goto || doneGoto.current === goto.n) return;
    const el = box.current?.querySelector<HTMLElement>(`[data-turn="${goto.turn}"]`);
    if (!el) return; // ещё свёрнута — после следующей отрисовки
    // «Расшифровка» скрыта (глава выбрана на «Итогах») — дойдём, когда её покажут.
    if (el.closest("[hidden]")) return;
    doneGoto.current = goto.n;
    // Начало главы — к её заголовку: он над первой репликой.
    const prev = el.previousElementSibling;
    scrollTo(prev?.matches(".chapter-head") ? prev : el);
    el.focus?.({ preventScroll: true });
    el.classList.remove("turn--flash");
    void el.offsetWidth; // перезапуск анимации
    el.classList.add("turn--flash");
    // Класс ставится мимо React: снять самим (иначе без анимации подсветка осталась бы навсегда).
    setTimeout(() => el.classList.remove("turn--flash"), FLASH_MS);
  });

  // Перемотка из плеера: прокрутить к реплике, звучащей в этот момент. Свёрнутую фильтром не
  // разворачиваем — к строке «… N реплик», где она спряталась (иначе к ближайшей видимой).
  const [pendingSeek, setPendingSeek] = useState<SeekRequest | null>(null);
  const lastSeek = useRef(seekTo?.n);
  useEffect(() => {
    if (!seekTo || seekTo.n === lastSeek.current) return;
    lastSeek.current = seekTo.n;
    setPendingSeek(seekTo);
  }, [seekTo]);
  const shownNow = useContext(TranscriptShown);
  useLayoutEffect(() => {
    if (!pendingSeek) return;
    const root = box.current;
    if (!root) return;
    const idx = turnAt(turns, pendingSeek.t);
    if (idx < 0) { setPendingSeek(null); return; }
    let el = root.querySelector<HTMLElement>(`[data-turn="${idx}"]`);
    if (!el) {
      const row = rows?.find((r) => r.kind === "more" && r.from <= idx && idx <= r.to);
      if (row && row.kind === "more") el = root.querySelector<HTMLElement>(`[data-more="${row.from}"]`);
    }
    if (!el) {
      let best = Infinity;
      for (const c of root.querySelectorAll<HTMLElement>("[data-turn]")) {
        const d = Math.abs(Number(c.dataset.turn) - idx);
        if (d < best) { best = d; el = c; }
      }
    }
    if (!el) { setPendingSeek(null); return; }
    // «Расшифровка» скрыта (открыты «Итоги» или «Агент») — прокрутим, когда её покажут.
    if (el.closest("[hidden]")) return;
    setPendingSeek(null);
    el.style.scrollMarginTop = SEEK_TOP;
    el.scrollIntoView?.({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
    el.classList.remove("turn--flash");
    void el.offsetWidth; // перезапуск анимации
    el.classList.add("turn--flash");
    const done = el;
    setTimeout(() => done.classList.remove("turn--flash"), FLASH_MS);
  }, [pendingSeek, shownNow, turns, rows]);

  // Текст до спикеров сменился окончательной расшифровкой: реплики пересобраны, а
  // читают то же место. Где оно было, смотрим по ещё не обновлённому DOM (во время
  // отрисовки), возвращаем — после неё, до показа кадра.
  const lastPhase = useRef(textPhase);
  const lastTurns = useRef(turns);
  const keep = useRef<{ place: Place; scroller: HTMLElement; turns: Turn[] } | null>(null);
  if (lastTurns.current !== turns) {
    // И текст до спикеров сменился новым (повтор прерванной расшифровки) — тоже пересборка.
    if ((lastPhase.current || textPhase) && box.current) {
      const scroller = scrollParent(box.current);
      const place = scroller ? placeOf(scroller, box.current, lastTurns.current) : null;
      keep.current = scroller && place ? { place, scroller, turns } : null;
    } else {
      keep.current = null;
    }
    lastTurns.current = turns;
    lastPhase.current = textPhase;
  }
  useLayoutEffect(() => {
    const held = keep.current;
    keep.current = null;
    // Место — для тех реплик, при отрисовке которых его сняли (прерванная отрисовка не в счёт).
    if (held && held.turns === turns && box.current) restorePlace(held.scroller, box.current, turns, held.place);
  }, [turns]);

  // «Сейчас играет»: атрибут мимо React — реплики не перерисовываются при смене.
  useLayoutEffect(() => {
    const root = box.current;
    if (!root) return;
    for (const old of root.querySelectorAll("[data-now]")) old.removeAttribute("data-now");
    if (nowTurn !== null) root.querySelector(`[data-turn="${nowTurn}"]`)?.setAttribute("data-now", "true");
  });

  // Новый результат: к нужной реплике (просьба из списка), новый запрос — к
  // первому совпадению; тот же запрос по перечитанной записи — остаёмся на месте.
  // Layout-эффекты: счётчик и выделение текущего меняются в одном кадре, без мигания.
  const lastQuery = useRef(query);
  useLayoutEffect(() => {
    const same = lastQuery.current === query;
    lastQuery.current = query;
    if (jump && usedJump.current !== jump.n) {
      usedJump.current = jump.n;
      setCurrent(jump.t !== null && hits.length ? hitAt(turns, hits, jump.t) : 0);
    } else {
      setCurrent((c) => (same && c < hits.length ? c : 0));
    }
  }, [hits, turns, query, jump]);

  // Текущее совпадение: выделить сильнее и прокрутить к нему (по центру).
  const unscrolled = useRef(false);
  useLayoutEffect(() => {
    const root = box.current;
    if (!root) return;
    for (const el of root.querySelectorAll(".hit--current")) el.classList.remove("hit--current");
    if (!hits.length) return;
    const all = root.querySelectorAll(`[data-hit="${current}"]`);
    const el = all[0];
    if (!el) return;
    // Совпадение, разрезанное ссылкой (Jira), — несколько кусков с одним номером.
    for (const piece of all) piece.classList.add("hit--current");
    // Панель скрыта (открыты «Итоги» или «Агент») — прокрутим, когда её покажут.
    unscrolled.current = el.closest("[hidden]") !== null;
    if (!unscrolled.current) scrollTo(el);
  }, [current, hits, jump]);

  // «Расшифровку» показали: досказать отложенную прокрутку (только её — ручное
  // переключение вкладок прокрутку не трогает).
  const shown = useContext(TranscriptShown);
  useLayoutEffect(() => {
    if (!unscrolled.current) return;
    const el = box.current?.querySelector(".hit--current");
    if (!el || el.closest("[hidden]")) return;
    unscrolled.current = false;
    scrollTo(el);
  }, [shown]);

  const step = (by: number) => {
    if (!hits.length) return;
    setCurrent((c) => (c + by + hits.length) % hits.length);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      // Запрос ещё не применился (задержка) — применить, а не листать старый.
      if (text !== query) setQuery(text);
      else step(e.shiftKey ? -1 : 1);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setText("");
      setQuery("");
      const back = returnTo.current;
      returnTo.current = null;
      if (back?.isConnected && back !== input.current) back.focus();
      else input.current?.blur();
    }
  };

  const counter = !active ? "" : hits.length ? `${current + 1} из ${hits.length}` : "Ничего не найдено";

  return (
    <div className="transcript" ref={box}>
      <div className="find" role="search" aria-label="Поиск по расшифровке">
        <input
          ref={input}
          type="search"
          className="find__input"
          data-transcript-search=""
          placeholder="Найти в расшифровке"
          aria-label="Найти в расшифровке"
          aria-keyshortcuts="Control+F"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onFocus={(e) => { returnTo.current = e.relatedTarget instanceof HTMLElement ? e.relatedTarget : null; }}
          onKeyDown={onKeyDown}
        />
        <span className={`find__count num${active && !hits.length ? " find__count--none" : ""}`}
          aria-live="polite" aria-atomic="true">{counter}</span>
        <button type="button" className="find__nav" aria-label="Предыдущее совпадение" title="Предыдущее (Shift+Enter)"
          disabled={hits.length < 2} onClick={() => step(-1)}><ChevronUp size={16} strokeWidth={1.75} aria-hidden="true" /></button>
        <button type="button" className="find__nav" aria-label="Следующее совпадение" title="Следующее (Enter)"
          disabled={hits.length < 2} onClick={() => step(1)}><ChevronDown size={16} strokeWidth={1.75} aria-hidden="true" /></button>
        <HelpTip label="Как искать в расшифровке" title="Поиск по расшифровке">
          <TipLine>Слова без кавычек находят реплики, где есть все эти слова в любой форме: «задача» найдёт и «задачи».</TipLine>
          <TipLine>Фраза в кавычках, например <code>"план работ"</code>, ищется точно, слово в слово.</TipLine>
          <TipLine><code>спикер:Анна</code> — только реплики этого спикера.</TipLine>
          <TipLine>Ctrl+F — к поиску, Enter и Shift+Enter — следующее и предыдущее совпадение, Esc — очистить.</TipLine>
        </HelpTip>
        {tools}
      </div>
      {((view && view.insights.length > 0) || tasks.length > 0) && (
        <InsightsBlock insights={view?.insights ?? NO_INSIGHTS} tasks={tasks} turns={turns} onJump={jumpTo}
          onAsk={onAskInsight} />
      )}
      {types && <TypeFilters counts={counts} value={filter} onChange={setFilter} />}
      {toolbar}
      {noMatches ? (
        <div className="turns-empty" role="status">
          <p className="turns-empty__text">Нет реплик по выбранным фильтрам</p>
          <button type="button" className="link-btn" onClick={() => setFilter(NO_FILTER)}>Сбросить фильтр</button>
        </div>
      ) : (
        <Turns turns={turns} colors={colors} playable={playable} onPlay={onPlay} onNameSpeaker={onNameSpeaker}
          onSpeaker={onSpeaker} selected={selected} onSelect={onSelect ? select : undefined} onSplitAt={onSplitAt}
          marks={active ? marks : undefined} onAskAgent={onAskAgent} rows={rows} annotations={annotations}
          onAskChapter={onAskChapter} onExpand={expand} textPhase={textPhase} />
      )}
    </div>
  );
}
