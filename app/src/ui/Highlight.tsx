import { splitByRanges, type Range } from "../lib/search";

/**
 * Текст с подсвеченными кусками (`<mark class="hit">`). `firstHit` — номер
 * первого куска среди всех совпадений: по `data-hit` поиск в карточке находит
 * текущее совпадение, не перерисовывая весь список реплик.
 */
export function Highlight({ text, ranges, firstHit }: { text: string; ranges: Range[]; firstHit?: number }) {
  let n = firstHit ?? 0;
  return (
    <>
      {splitByRanges(text, ranges).map((part, i) =>
        part.mark ? (
          <mark key={i} className="hit" data-hit={firstHit === undefined ? undefined : n++}>{part.text}</mark>
        ) : part.text)}
    </>
  );
}
