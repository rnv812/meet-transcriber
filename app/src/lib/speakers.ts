import type { Segment } from "./types";

export type Turn = {
  /** `uncertain` — нахлёст спикеров (диаризация собеседников). */
  speaker: string; start: number; end: number; texts: string[]; uncertain: boolean;
  /** Человек рядом с владельцем в комнате (голос с микрофона, не владелец). */
  room?: boolean;
  /** Голос микрофона под вопросом: подписан владельцем, но мог быть кто-то рядом. */
  unsure?: boolean;
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
  let curMic = false;
  segments.forEach((s, i) => {
    if (s.kind === "break") {
      // Сама по себе и соседей не склеивает (как meet/search.py).
      out.push({ speaker: "", start: s.start, end: s.end, texts: [s.text], uncertain: false, kind: "break", idx: [i] });
      cur = null;
      return;
    }
    // Пустой спикер — как null (так же склеивает и поиск резидента, meet/search.py).
    const speaker = s.speaker || NO_SPEAKER;
    // `uncertain` у микрофона — голос под вопросом, у собеседников — нахлёст.
    const mic = s.track === "mic";
    const unsure = s.uncertain && mic;
    const overlap = s.uncertain && !unsure;
    // Микрофон и звонок, голос под вопросом — разные реплики (как library.turn_mark у резидента:
    // номера реплик окна и поиска совпадают).
    if (cur && cur.speaker === speaker && s.start - cur.end < GAP_S && curMic === mic && !!cur.unsure === unsure) {
      cur.texts.push(s.text);
      cur.end = Math.max(cur.end, s.end);
      if (overlap) cur.uncertain = true;
      if (unsure) cur.unsure = true;
      if (s.room) cur.room = true;
      cur.idx?.push(i);
    } else {
      cur = { speaker, start: s.start, end: s.end, texts: [s.text], uncertain: overlap, idx: [i] };
      curMic = mic;
      if (unsure) cur.unsure = true;
      if (s.room) cur.room = true;
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

/**
 * Кегль инициалов в круге диаметром `size` (0.5): одна буква — ≈ 42 % диаметра,
 * две — ≈ 34 %, чтобы буквы не подходили к кольцу; не меньше 9 px.
 */
export function initialsFontSize(size: number, text: string): number {
  return Math.max(9, Math.round(size * ([...text].length > 1 ? 0.34 : 0.42)));
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
