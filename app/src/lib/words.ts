/**
 * Слова с таймкодами в тексте: где слово стоит в тексте реплики или образца и
 * какое слово звучит в момент `t`. Слова приходят от резидента отдельно от
 * транскрипта (`GET /recordings/{id}/words`, по сегменту — список или null у
 * правленого) и ищутся в тексте по порядку: не сошлись с текстом — у этого
 * текста слов нет, он ведёт себя как раньше.
 */

import { nfc } from "./search";
import type { Turn } from "./speakers";
import type { SampleWord } from "./types";

/** Слова сегментов транскрипта, по номеру сегмента; null — у сегмента слов нет. */
export type SegmentWords = (SampleWord[] | null)[];

/** Слово: [from, to) — место в тексте (NFC), start–end — время в записи, с. */
export type WordSpan = { from: number; to: number; start: number; end: number };

/** Места слов в тексте `text` (уже в NFC), со сдвигом `base`; слова не сходятся с текстом — null. */
export function wordSpans(text: string, words: readonly SampleWord[], base = 0): WordSpan[] | null {
  const out: WordSpan[] = [];
  let at = 0;
  for (const [start, end, raw] of words) {
    const token = nfc(String(raw)).trim();
    if (!token) continue;
    const k = text.indexOf(token, at);
    if (k < 0 || !Number.isFinite(start) || !Number.isFinite(end)) return null;
    out.push({ from: base + k, to: base + k + token.length, start, end });
    at = k + token.length;
  }
  return out.length ? out : null;
}

/**
 * Слова реплики в её тексте (`nfc(turn.texts.join(" "))`, как его рисует лента):
 * по сегментам реплики; сегмент без слов (или с несошедшимися) — просто текст.
 */
export function turnWordSpans(turn: Turn, words: SegmentWords): WordSpan[] | null {
  if (turn.kind === "break" || !turn.idx) return null;
  const out: WordSpan[] = [];
  let base = 0;
  turn.texts.forEach((raw, k) => {
    const text = nfc(raw);
    const seg = words[turn.idx![k]!];
    const spans = seg ? wordSpans(text, seg, base) : null;
    if (spans) out.push(...spans);
    base += text.length + 1;
  });
  return out.length ? out : null;
}

/** Номер слова, которое звучит в момент `t` (start ≤ t < end); между словами — −1. */
export function wordIndexAt(spans: readonly WordSpan[], t: number): number {
  return spans.findIndex((w) => w.start <= t && t < w.end);
}

/** Номер слова, в котором стоит символ `at` текста; пробел между словами — −1. */
export function wordAtOffset(spans: readonly WordSpan[], at: number): number {
  return spans.findIndex((w) => w.from <= at && at < w.to);
}
