import type { Segment } from "./types";

export type Turn = {
  speaker: string; start: number; end: number; texts: string[]; uncertain: boolean;
  /** "break" — отметка перерыва объединённой встречи: разделитель, не реплика. */
  kind?: "break";
  /** Номера сегментов транскрипта, из которых склеена реплика (для правки спикера). */
  idx?: number[];
};

const GAP_S = 2;
/** Подпись реплик без спикера: в транскрипте у них speaker = null, переименовать нечего. */
export const NO_SPEAKER = "Неизвестный";

/** Склеивает подряд идущие реплики одного спикера, если пауза меньше 2 с. */
export function mergeTurns(segments: Segment[]): Turn[] {
  const out: Turn[] = [];
  let cur: Turn | null = null;
  segments.forEach((s, i) => {
    if (s.kind === "break") {
      // Сама по себе и соседей не склеивает (как meet/search.py).
      out.push({ speaker: "", start: s.start, end: s.end, texts: [s.text], uncertain: false, kind: "break", idx: [i] });
      cur = null;
      return;
    }
    // Пустой спикер — как null (так же склеивает и поиск резидента, meet/search.py).
    const speaker = s.speaker || NO_SPEAKER;
    if (cur && cur.speaker === speaker && s.start - cur.end < GAP_S) {
      cur.texts.push(s.text);
      cur.end = Math.max(cur.end, s.end);
      if (s.uncertain) cur.uncertain = true;
      cur.idx?.push(i);
    } else {
      cur = { speaker, start: s.start, end: s.end, texts: [s.text], uncertain: s.uncertain, idx: [i] };
      out.push(cur);
    }
  });
  return out;
}

/** Спикеры в порядке появления. */
export function speakersOf(segments: Segment[]): string[] {
  const seen = new Set<string>();
  for (const s of segments) if (s.speaker) seen.add(s.speaker);
  return [...seen];
}

export function isUnnamed(name: string): boolean {
  return /^Спикер \d+$/.test(name) || /^SPEAKER_\d+$/.test(name);
}

export function initials(name: string): string {
  const num = /^Спикер (\d+)$/.exec(name) ?? /^SPEAKER_0*(\d+)$/.exec(name);
  if (num?.[1]) return num[1];
  const words = name.trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 2).map((w) => (w[0] ?? "").toUpperCase()).join("");
}

/**
 * «Эта и следующие подряд»: реплика `at` и идущие за ней реплики того же
 * спикера, пока не заговорит другой (или не встретится перерыв).
 */
export function runFrom(turns: Turn[], at: number): number[] {
  const first = turns[at];
  if (!first || first.kind === "break") return [];
  const out = [at];
  for (let i = at + 1; i < turns.length; i++) {
    const t = turns[i];
    if (!t || t.kind === "break" || t.speaker !== first.speaker) break;
    out.push(i);
  }
  return out;
}
