/**
 * Время общего плеера карточки для подсветки звучащего слова. Плеер сообщает
 * время каждый кадр одному слушателю (`onPlayhead` у RecordingCard); карточка
 * кладёт его сюда, а текст реплики или образца с элементами слов подписывается
 * и красит своё слово атрибутом `data-word-now` прямо в DOM — без перерисовки.
 */

import { createContext, useContext, useLayoutEffect, type RefObject } from "react";
import { wordIndexAt, type WordSpan } from "../../lib/words";

export type PlayClock = {
  /** Последнее известное время плеера, с; null — плеер ещё не сообщал. */
  now: () => number | null;
  subscribe: (fn: (t: number) => void) => () => void;
};

export function createPlayClock(): PlayClock & { set: (t: number) => void } {
  let now: number | null = null;
  const fns = new Set<(t: number) => void>();
  return {
    now: () => now,
    set(t) {
      now = t;
      for (const fn of fns) fn(t);
    },
    subscribe(fn) {
      fns.add(fn);
      return () => { fns.delete(fn); };
    },
  };
}

/** Часы плеера карточки; нет — подсветки слов нет. */
export const PlayTime = createContext<PlayClock | null>(null);

const NOW = "data-word-now";

/**
 * Подсветка звучащего слова внутри `root`: элементы слова — `[data-w="номер"]`
 * (слово, разрезанное подсветкой поиска или ссылкой, — несколько элементов).
 * Без слов (`spans` null) — ничего. Подписка — после каждой отрисовки: React мог
 * заменить элементы слов, а атрибут ставится мимо него.
 */
export function useWordPaint(root: RefObject<HTMLElement | null>, spans: WordSpan[] | null): void {
  const clock = useContext(PlayTime);
  useLayoutEffect(() => {
    const el = root.current;
    if (!el || !spans || !clock) return;
    let lit = -1;
    const clear = () => { for (const x of el.querySelectorAll(`[${NOW}]`)) x.removeAttribute(NOW); };
    const paint = (t: number | null) => {
      const k = t === null ? -1 : wordIndexAt(spans, t);
      if (k === lit) return;
      lit = k;
      clear();
      if (k >= 0) for (const x of el.querySelectorAll(`[data-w="${k}"]`)) x.setAttribute(NOW, "");
    };
    paint(clock.now());
    const off = clock.subscribe(paint);
    return () => { off(); clear(); };
  });
}
