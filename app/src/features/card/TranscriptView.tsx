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
  useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent,
  type ReactNode,
} from "react";
import { findHits, parseQuery, prepare } from "../../lib/search";
import type { Turn } from "../../lib/speakers";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { TranscriptShown } from "./transcriptShown";
import { Turns, type PersonColor, type TurnMarks } from "./Turns";

export const FIND_DELAY_MS = 150;

/** Открыть карточку с запросом (из поиска по записям); `t` — начало нужной реплики, `n` — номер просьбы. */
export type FindRequest = { q: string; t: number | null; n: number };

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
const scrollTo = (el: Element) => el.scrollIntoView?.({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });

export function TranscriptView({
  turns, colors, playable, onPlay, onNameSpeaker, onSpeaker, selected, onSelect, onSplitAt, toolbar, find,
}: {
  turns: Turn[];
  colors: Map<string, string>;
  playable: boolean;
  onPlay: (turn: Turn) => void;
  onNameSpeaker?: (label: string) => void;
  /** Правка спикера у реплики (TurnEdit): меню, выбор нескольких. */
  onSpeaker?: (turn: number, anchor: HTMLElement) => void;
  selected?: ReadonlySet<number>;
  onSelect?: (turn: number, how: "toggle" | "range") => void;
  onSplitAt?: (turn: number, event: MouseEvent<HTMLElement>) => void;
  /** Полоса над репликами (выбранные, итог назначения). */
  toolbar?: ReactNode;
  find?: FindRequest | null;
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
    const el = root.querySelector(`[data-hit="${current}"]`);
    if (!el) return;
    el.classList.add("hit--current");
    // Панель скрыта (открыты «Итоги» или «Вопросы») — прокрутим, когда её покажут.
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

  const active = query.trim() !== "";
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
          disabled={hits.length < 2} onClick={() => step(-1)}>↑</button>
        <button type="button" className="find__nav" aria-label="Следующее совпадение" title="Следующее (Enter)"
          disabled={hits.length < 2} onClick={() => step(1)}>↓</button>
        <HelpTip label="Как искать в расшифровке" title="Поиск по расшифровке">
          <TipLine>Слова без кавычек находят реплики, где есть все эти слова в любой форме: «задача» найдёт и «задачи».</TipLine>
          <TipLine>Фраза в кавычках, например <code>"план работ"</code>, ищется точно, слово в слово.</TipLine>
          <TipLine><code>спикер:Анна</code> — только реплики этого спикера.</TipLine>
          <TipLine>Ctrl+F — к поиску, Enter и Shift+Enter — следующее и предыдущее совпадение, Esc — очистить.</TipLine>
        </HelpTip>
      </div>
      {toolbar}
      <Turns turns={turns} colors={colors} playable={playable} onPlay={onPlay} onNameSpeaker={onNameSpeaker}
        onSpeaker={onSpeaker} selected={selected} onSelect={onSelect} onSplitAt={onSplitAt}
        marks={active ? marks : undefined} />
    </div>
  );
}
