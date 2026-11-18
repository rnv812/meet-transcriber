import { Fragment, type ReactNode } from "react";
import { splitByRanges, type Range } from "../lib/search";
import type { WordSpan } from "../lib/words";

/**
 * Кусок текста [from, to) с элементами слов (`<span data-w="номер">`): слово,
 * которое задевает кусок, — свой элемент, пробелы между словами — текстом. Слова
 * режутся по краям куска: подсветка поиска и ссылки остаются снаружи, у слова,
 * разрезанного ими, — несколько элементов с одним номером. Без слов — текст как есть.
 */
export function wordPieces(text: string, from: number, to: number, words: readonly WordSpan[] | null | undefined,
  key: string): ReactNode[] {
  if (!words?.length) return from < to ? [text.slice(from, to)] : [];
  const out: ReactNode[] = [];
  let at = from;
  words.forEach((w, k) => {
    const a = Math.max(w.from, from);
    const b = Math.min(w.to, to);
    if (a >= b) return;
    if (a > at) out.push(text.slice(at, a));
    out.push(<span key={`${key}w${k}`} data-w={k}>{text.slice(a, b)}</span>);
    at = b;
  });
  if (at < to) out.push(text.slice(at, to));
  return out;
}

/**
 * Текст с подсвеченными кусками (`<mark class="hit">`). `firstHit` — номер
 * первого куска среди всех совпадений: по `data-hit` поиск в карточке находит
 * текущее совпадение, не перерисовывая весь список реплик. `words` — элементы
 * слов (звучащее слово, щелчок по слову) внутри подсветки и вне её.
 */
export function Highlight({ text, ranges, firstHit, words }: {
  text: string; ranges: Range[]; firstHit?: number; words?: readonly WordSpan[] | null;
}) {
  let n = firstHit ?? 0;
  let at = 0;
  return (
    <>
      {splitByRanges(text, ranges).map((part, i) => {
        const from = at;
        at += part.text.length;
        const body = words ? wordPieces(text, from, at, words, `h${i}`) : part.text;
        return part.mark ? (
          <mark key={i} className="hit" data-hit={firstHit === undefined ? undefined : n++}>{body}</mark>
        ) : words ? <Fragment key={i}>{body}</Fragment> : part.text;
      })}
    </>
  );
}
