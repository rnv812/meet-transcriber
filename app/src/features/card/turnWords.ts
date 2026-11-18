/**
 * Элементы слов в ленте — только у играющей реплики и у той, по которой
 * щёлкнули: у двух часов встречи их были бы десятки тысяч. Какие реплики
 * сейчас «со словами», лента узнаёт из этого хранилища: текст реплики
 * подписан на свой номер и перерисовывается один, когда тот появился или ушёл.
 */

import { createContext } from "react";
import type { Turn } from "../../lib/speakers";
import { turnWordSpans, type SegmentWords, type WordSpan } from "../../lib/words";

export type TurnWords = {
  subscribe: (fn: () => void) => () => void;
  /** Слова реплики `i`, если её текст сейчас рисуется по словам; иначе null. */
  shown: (i: number) => WordSpan[] | null;
};

export type TurnWordsStore = TurnWords & {
  /** Слова реплики `i` (для щелчка — и у реплики без элементов слов); нет слов — null. */
  of: (i: number) => WordSpan[] | null;
  /** Реплики, слова сегментов и реплики «со словами» (играющая, щёлкнутая). */
  set: (turns: Turn[], words: SegmentWords | null, active: readonly (number | null)[]) => void;
};

export function createTurnWords(): TurnWordsStore {
  let turns: Turn[] = [];
  let words: SegmentWords | null = null;
  let active = new Set<number>();
  // Слова реплики считаются один раз на пару «реплики + слова»; один массив — одна перерисовка.
  let cache = new Map<number, WordSpan[] | null>();
  const fns = new Set<() => void>();
  const of = (i: number) => {
    if (!words) return null;
    if (!cache.has(i)) {
      const t = turns[i];
      cache.set(i, t ? turnWordSpans(t, words) : null);
    }
    return cache.get(i) ?? null;
  };
  return {
    of,
    shown: (i) => (active.has(i) ? of(i) : null),
    subscribe(fn) {
      fns.add(fn);
      return () => { fns.delete(fn); };
    },
    set(nextTurns, nextWords, nextActive) {
      const ids = new Set(nextActive.filter((i): i is number => i !== null));
      const same = nextTurns === turns && nextWords === words
        && ids.size === active.size && [...ids].every((i) => active.has(i));
      if (same) return;
      if (nextTurns !== turns || nextWords !== words) cache = new Map();
      turns = nextTurns;
      words = nextWords;
      active = ids;
      for (const fn of fns) fn();
    },
  };
}

/** Хранилище ленты; нет — текст реплик без элементов слов. */
export const TurnWordsContext = createContext<TurnWords | null>(null);
