/**
 * Перечитанная карточка с той же расшифровкой — та же ссылка на неё.
 *
 * Карточку перечитывают при каждом шаге задач записи (анализ встал в очередь,
 * пошёл, закончился), при выгрузке в базу знаний и т. п. Ответ — новый объект,
 * даже если текст не менялся, а окна правки («Исправить…», выделение реплик,
 * меню спикера, «Разделить реплику») сбрасываются по смене `segments`: без
 * этого они закрывались бы посреди ввода. Сравнение — по содержимому (O(n)).
 */

import type { Segment, Transcript } from "./types";

const SEGMENT_KEYS = ["start", "end", "speaker", "text", "uncertain", "kind", "has_words"] as const;

export function sameSegments(a: Segment[], b: Segment[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i]!, y = b[i]!;
    for (const k of SEGMENT_KEYS) if (x[k] !== y[k]) return false;
  }
  return true;
}

function sameRest(a: Transcript, b: Transcript): boolean {
  const { segments: _a, ...ra } = a;
  const { segments: _b, ...rb } = b;
  return JSON.stringify(ra) === JSON.stringify(rb);
}

/** `next`, но с прежними объектами там, где содержимое не изменилось. */
export function keepTranscript(prev: Transcript | null | undefined, next: Transcript | null): Transcript | null {
  if (!prev || !next || !sameSegments(prev.segments, next.segments)) return next;
  return sameRest(prev, next) ? prev : { ...next, segments: prev.segments };
}
