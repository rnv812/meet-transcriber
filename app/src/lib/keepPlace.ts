/**
 * Место чтения в «Расшифровке», когда реплики пересобраны (Р4): текст до
 * спикеров сменился окончательной расшифровкой — те же слова, но реплики
 * разделены по спикерам, у них появились подписи, а строка хода ушла. Пиксели
 * прокрутки прежние, а текст под ними другой: человек потерял бы место.
 *
 * Место — момент записи на верхней кромке видимой области (с долей внутри
 * реплики) и где он стоял относительно кромки. После пересборки тот же
 * момент возвращается на то же место.
 */

import { turnAt } from "./analysisView";
import type { Turn } from "./speakers";

export type Place = { t: number; dy: number };

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

/** Ближайший прокручиваемый предок (карточка записи) или прокрутка страницы. */
export function scrollParent(el: Element | null): HTMLElement | null {
  for (let p = el?.parentElement ?? null; p; p = p.parentElement) {
    const overflow = getComputedStyle(p).overflowY;
    if (overflow === "auto" || overflow === "scroll") return p;
  }
  return (document.scrollingElement as HTMLElement | null) ?? null;
}

/** Где сейчас читают: момент на верхней кромке. В самом начале — null (там и останемся). */
export function placeOf(scroller: HTMLElement, box: HTMLElement, turns: Turn[]): Place | null {
  if (scroller.scrollTop <= 0) return null;
  const edge = scroller.getBoundingClientRect().top;
  for (const el of box.querySelectorAll<HTMLElement>("[data-turn]")) {
    const r = el.getBoundingClientRect();
    if (r.bottom <= edge) continue;
    const turn = turns[Number(el.dataset.turn)];
    if (!turn) return null;
    const frac = r.height > 0 ? clamp01((edge - r.top) / r.height) : 0;
    return { t: turn.start + frac * (turn.end - turn.start), dy: r.top + frac * r.height - edge };
  }
  return null;
}

/** Вернуть момент `place` на прежнее место после пересборки реплик. */
export function restorePlace(scroller: HTMLElement, box: HTMLElement, turns: Turn[], place: Place): void {
  const i = turnAt(turns, place.t);
  const turn = turns[i];
  const el = i < 0 ? null : box.querySelector<HTMLElement>(`[data-turn="${i}"]`);
  if (!turn || !el) return;
  const r = el.getBoundingClientRect();
  const span = turn.end - turn.start;
  const frac = span > 0 ? clamp01((place.t - turn.start) / span) : 0;
  const shift = r.top + frac * r.height - scroller.getBoundingClientRect().top - place.dy;
  if (Math.abs(shift) >= 1) scroller.scrollTop += shift;
}
