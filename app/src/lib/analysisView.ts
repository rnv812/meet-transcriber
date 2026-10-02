/**
 * Разметка встречи (analysis.json, M2) в терминах карточки: реплики карточки
 * склеены из сегментов транскрипта (lib/speakers.ts, `Turn.idx`), а анализ
 * размечает сегменты. Здесь всё переводится на реплики один раз на анализ:
 * тип реплики, важность, главы, наблюдения, «Только важное», кривая важности.
 *
 * Ключи `phrase_types` и `importance` в анализе разрежены: нет ключа — это
 * «утверждение» и низкая важность (модель их не перечисляет, M2).
 */

import type { Turn } from "./speakers";
import type { Analysis, AnalysisState, InsightKind, PhraseType } from "./types";

/** Порядок «силы» типов: тип реплики — сильнейший тип её сегментов. */
export const TYPE_PRIORITY: PhraseType[] = [
  "decision", "task", "risk", "question", "objection", "agreement", "idea", "statement",
];

export const TYPE_LABEL: Record<PhraseType, string> = {
  statement: "Утверждение",
  question: "Вопрос",
  idea: "Идея",
  decision: "Решение",
  task: "Задача",
  risk: "Риск",
  agreement: "Согласие",
  objection: "Возражение",
};

export const INSIGHT_LABEL: Record<InsightKind, string> = {
  insight: "Наблюдение",
  contradiction: "Противоречие",
  attention: "Обратить внимание",
  followup: "Сделать после встречи",
};

/** Фильтры над репликами: подпись чипа и тип. */
export const TYPE_FILTERS: { type: PhraseType; label: string }[] = [
  { type: "question", label: "Вопросы" },
  { type: "decision", label: "Решения" },
  { type: "task", label: "Задачи" },
  { type: "risk", label: "Риски" },
  { type: "idea", label: "Идеи" },
];

/** Важность реплики без оценки: модель не перечисляет реплики ниже 0,3 (M2). */
export const LOW_IMPORTANCE = 0.15;
/** Ниже этого реплика не бывает «важной», даже если попала в верхние проценты. */
const IMPORTANT_MIN = 0.3;
/** Полоса слева у реплики — верхние 15 % по важности. */
export const KEY_SHARE = 0.15;
/** «Только важное» — верхние 30 %. */
export const ONLY_IMPORTANT_SHARE = 0.3;
/** «Только важное»: соседние фрагменты ближе этого склеиваются, каждый — с запасом по краям. */
export const MERGE_GAP_S = 4;
export const PAD_S = 1;

/** Что показывать (настройки «Расшифровка: подсветка и разметка» и «Анализ встречи»). */
export type ViewParts = { types: boolean; importance: boolean; chapters: boolean; insights: boolean };

export type ChapterView = {
  /** Номер главы с 1. */
  n: number;
  title: string;
  short: string;
  /** Первая и последняя реплика главы (номера реплик карточки). */
  turn: number;
  lastTurn: number;
  /** Начало первой реплики и конец последней, секунды. */
  start: number;
  end: number;
};

export type InsightView = {
  id: string;
  kind: InsightKind;
  text: string;
  why: string;
  /** Номера реплик карточки, по возрастанию, без повторов. */
  refs: number[];
};

export type AnalysisView = {
  /** Тип каждой реплики; null — утверждение (или тип не размечали). */
  types: (PhraseType | null)[] | null;
  /** Важность каждой реплики (0..1); null — важность не размечали. */
  importance: number[] | null;
  /** Реплики верхних 15 % по важности — полоса слева. */
  key: boolean[] | null;
  chapters: ChapterView[];
  /** Номер главы, которая начинается с этой реплики, иначе -1. */
  chapterStart: Int32Array;
  insights: InsightView[];
};

/**
 * Анализ, который можно показывать по нынешней расшифровке. Свежий — да.
 * Устаревший (правили текст), а также прежний, пока идёт новый или после сбоя,
 * — только если число сегментов не изменилось: номера реплик те же.
 */
export function usableAnalysis(state: AnalysisState | null, segmentCount: number): Analysis | null {
  const a = state?.analysis;
  if (!state || !a) return null;
  const counted = typeof a.segments === "number";
  if (counted && a.segments !== segmentCount) return null;
  if (state.state === "ready") return a;
  return counted ? a : null;
}

/** Номер реплики карточки для каждого сегмента транскрипта (-1 — пустой или перерыв). */
export function segmentTurns(turns: Turn[], segmentCount: number): Int32Array {
  const map = new Int32Array(Math.max(0, segmentCount)).fill(-1);
  turns.forEach((t, i) => {
    if (t.kind === "break") return;
    for (const s of t.idx ?? []) if (s >= 0 && s < map.length) map[s] = i;
  });
  return map;
}

/** Реплика сегмента; сегмент без реплики — ближайшая следующая, иначе предыдущая. */
function turnOfSegment(map: Int32Array, seg: number): number {
  if (!Number.isInteger(seg) || map.length === 0) return -1;
  const at = Math.max(0, Math.min(map.length - 1, seg));
  for (let s = at; s < map.length; s++) if (map[s]! >= 0) return map[s]!;
  for (let s = at - 1; s >= 0; s--) if (map[s]! >= 0) return map[s]!;
  return -1;
}

const rank = (t: PhraseType) => TYPE_PRIORITY.indexOf(t);

/** Тип реплики: сильнейший тип её сегментов; одни утверждения — null. */
export function turnType(turn: Turn, types: Record<string, PhraseType> | undefined): PhraseType | null {
  if (!types || turn.kind === "break") return null;
  let best: PhraseType | null = null;
  for (const s of turn.idx ?? []) {
    const t = types[String(s)];
    if (!t || rank(t) < 0 || t === "statement") continue;
    if (best === null || rank(t) < rank(best)) best = t;
  }
  return best;
}

/** Важность реплики: наибольшая у её сегментов; без оценки — LOW_IMPORTANCE. */
export function turnImportance(turn: Turn, importance: Record<string, number> | undefined): number {
  if (!importance || turn.kind === "break") return turn.kind === "break" ? 0 : LOW_IMPORTANCE;
  let best = LOW_IMPORTANCE;
  for (const s of turn.idx ?? []) {
    const v = importance[String(s)];
    if (typeof v === "number" && Number.isFinite(v)) best = Math.max(best, Math.min(1, Math.max(0, v)));
  }
  return best;
}

/**
 * Какие реплики в верхней доле `share` по важности. Реплики без оценки и с
 * оценкой ниже 0,3 важными не бывают — сколько бы их ни было.
 */
export function topShare(importance: number[], turns: Turn[], share: number): boolean[] {
  const values = importance.filter((_, i) => turns[i]?.kind !== "break");
  const out = importance.map(() => false);
  if (!values.length) return out;
  const sorted = [...values].sort((a, b) => b - a);
  const k = Math.max(1, Math.round(values.length * share));
  const threshold = Math.max(sorted[k - 1]!, IMPORTANT_MIN + 1e-9);
  importance.forEach((v, i) => { out[i] = turns[i]?.kind !== "break" && v >= threshold; });
  return out;
}

/** Разметка по репликам: один раз на анализ (и на смену набора показываемого). */
export function buildView(turns: Turn[], analysis: Analysis | null, segmentCount: number, parts: ViewParts): AnalysisView | null {
  if (!analysis) return null;
  const map = segmentTurns(turns, segmentCount);
  const types = parts.types && analysis.phrase_types ? turns.map((t) => turnType(t, analysis.phrase_types)) : null;
  const importance = parts.importance && analysis.importance
    ? turns.map((t) => turnImportance(t, analysis.importance)) : null;
  const key = importance ? topShare(importance, turns, KEY_SHARE) : null;

  const chapters: ChapterView[] = [];
  const chapterStart = new Int32Array(turns.length).fill(-1);
  if (parts.chapters && analysis.chapters?.length) {
    const starts = analysis.chapters
      .map((c) => ({ c, turn: turnOfSegment(map, c.start_i) }))
      .filter((x) => x.turn >= 0)
      .sort((a, b) => a.turn - b.turn);
    // Две главы с одной первой репликой — остаётся первая.
    const unique = starts.filter((x, k) => k === 0 || x.turn !== starts[k - 1]!.turn);
    unique.forEach(({ c, turn }, k) => {
      const next = unique[k + 1];
      let lastTurn = next ? next.turn - 1 : turns.length - 1;
      while (lastTurn > turn && turns[lastTurn]?.kind === "break") lastTurn--;
      const first = k === 0 ? firstSpoken(turns) : turn;
      chapters.push({
        n: k + 1,
        title: c.title,
        short: c.short || c.title,
        turn: first,
        lastTurn,
        start: turns[first]?.start ?? 0,
        end: turns[lastTurn]?.end ?? turns[first]?.end ?? 0,
      });
      chapterStart[first] = k;
    });
  }

  const insights: InsightView[] = parts.insights && analysis.insights
    ? analysis.insights.map((x) => ({
      id: x.id,
      kind: x.kind,
      text: x.text,
      why: x.why ?? "",
      refs: [...new Set((x.refs ?? []).map((s) => (s >= 0 && s < map.length ? map[s]! : -1)).filter((t) => t >= 0))]
        .sort((a, b) => a - b),
    }))
    : [];

  if (!types && !importance && !chapters.length && !insights.length) return null;
  return { types, importance, key, chapters, chapterStart, insights };
}

function firstSpoken(turns: Turn[]): number {
  const i = turns.findIndex((t) => t.kind !== "break");
  return i < 0 ? 0 : i;
}

// --- время ---------------------------------------------------------------------

/** Последняя реплика (не перерыв), начавшаяся не позже `t`; до первой — первая; нет — -1. */
export function turnAt(turns: Turn[], t: number): number {
  let lo = 0;
  let hi = turns.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (turns[mid]!.start <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  for (let i = found; i >= 0; i--) if (turns[i]!.kind !== "break") return i;
  return turns.findIndex((x) => x.kind !== "break");
}

/** Глава, идущая в момент `t` (по началу её первой реплики); до первой — первая; нет глав — -1. */
export function chapterAt(chapters: ChapterView[], t: number): number {
  if (!chapters.length) return -1;
  let at = 0;
  for (let k = 0; k < chapters.length; k++) if (chapters[k]!.start <= t) at = k;
  return at;
}

/** Куда перейти к соседней главе (Shift+←/→): назад — к началу текущей, если от него ушли дальше 2 с. */
export function chapterJump(chapters: ChapterView[], t: number, dir: 1 | -1): number | null {
  if (!chapters.length) return null;
  const k = chapterAt(chapters, t);
  if (dir > 0) return k + 1 < chapters.length ? chapters[k + 1]!.start : null;
  const here = k === 0 ? 0 : chapters[k]!.start;
  if (t - here > 2) return here;
  return k > 0 ? (k - 1 === 0 ? 0 : chapters[k - 1]!.start) : 0;
}

/** Соседняя реплика (Ctrl+←/→): назад — к началу текущей, если от него ушли дальше 1 с. */
export function turnJump(turns: Turn[], t: number, dir: 1 | -1): number | null {
  const i = turnAt(turns, t);
  if (i < 0) return null;
  if (dir > 0) {
    for (let k = i + 1; k < turns.length; k++) if (turns[k]!.kind !== "break" && turns[k]!.start > t) return turns[k]!.start;
    if (turns[i]!.start > t) return turns[i]!.start;
    return null;
  }
  if (t - turns[i]!.start > 1) return turns[i]!.start;
  for (let k = i - 1; k >= 0; k--) if (turns[k]!.kind !== "break") return turns[k]!.start;
  return turns[i]!.start;
}

// --- «Только важное» ------------------------------------------------------------

export type Span = { start: number; end: number };

/**
 * Фрагменты «Только важного»: реплики верхних 30 % по важности; соседние
 * ближе MERGE_GAP_S склеиваются, у каждого — PAD_S запаса с обеих сторон.
 */
export function importantSpans(turns: Turn[], importance: number[], duration: number): Span[] {
  const top = topShare(importance, turns, ONLY_IMPORTANT_SHARE);
  const raw: Span[] = [];
  turns.forEach((t, i) => {
    if (!top[i]) return;
    const last = raw.at(-1);
    if (last && t.start - last.end < MERGE_GAP_S) last.end = Math.max(last.end, t.end);
    else raw.push({ start: t.start, end: t.end });
  });
  const end = duration > 0 ? duration : Infinity;
  const out: Span[] = [];
  for (const s of raw) {
    const padded = { start: Math.max(0, s.start - PAD_S), end: Math.min(end, s.end + PAD_S) };
    const last = out.at(-1);
    if (last && padded.start <= last.end) last.end = Math.max(last.end, padded.end);
    else out.push(padded);
  }
  return out;
}

/**
 * Куда перескочить в «Только важном» из момента `t`: внутри фрагмента — null
 * (играть дальше), между фрагментами — к началу следующего, после последнего —
 * Infinity (конец: остановиться).
 */
export function skipTarget(spans: Span[], t: number): number | null {
  let lo = 0;
  let hi = spans.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const s = spans[mid]!;
    if (t < s.start) hi = mid - 1;
    else if (t >= s.end) lo = mid + 1;
    else return null;
  }
  return lo < spans.length ? spans[lo]!.start : Infinity;
}

// --- кривая важности -------------------------------------------------------------

export const CURVE_POINTS = 200;
/** Фон кривой там, где никто не говорит. */
const SILENCE = 0.04;

/**
 * Кривая важности по времени, как «самые пересматриваемые» на YouTube: `n`
 * точек, у каждой — наибольшая важность реплик в её отрезке, затем
 * сглаживание и нормировка к самому высокому месту.
 */
export function curveValues(turns: Turn[], importance: number[], duration: number, n = CURVE_POINTS): number[] {
  const out = new Array<number>(n).fill(SILENCE);
  if (!(duration > 0) || !turns.length) return out;
  const step = duration / n;
  turns.forEach((t, i) => {
    if (t.kind === "break") return;
    const v = importance[i] ?? LOW_IMPORTANCE;
    const a = Math.max(0, Math.floor(t.start / step));
    const b = Math.min(n - 1, Math.floor(Math.max(t.start, t.end - 1e-6) / step));
    for (let k = a; k <= b; k++) out[k] = Math.max(out[k]!, v);
  });
  const kernel = [1, 3, 6, 9, 10, 9, 6, 3, 1];
  const half = (kernel.length - 1) / 2;
  let smooth = out;
  for (let pass = 0; pass < 2; pass++) {
    const src = smooth;
    smooth = src.map((_, k) => {
      let sum = 0;
      let w = 0;
      kernel.forEach((kw, j) => {
        const x = src[k + j - half];
        if (x !== undefined) { sum += x * kw; w += kw; }
      });
      return sum / w;
    });
  }
  const peak = Math.max(0.5, ...smooth);
  return smooth.map((v) => v / peak);
}

/**
 * Контур кривой одной линией SVG (с заливкой до низа) в координатах
 * `width`×`height`; сглаженная: квадратичные кривые через середины точек.
 */
export function curvePath(values: number[], width = 1000, height = 100): string {
  if (!values.length) return "";
  const pts = values.map((v, k) => [
    values.length === 1 ? 0 : (k / (values.length - 1)) * width,
    height - Math.max(0, Math.min(1, v)) * height * 0.92,
  ] as const);
  const f = (x: number) => (Math.round(x * 10) / 10).toString();
  let d = `M0,${height} L${f(pts[0]![0])},${f(pts[0]![1])}`;
  for (let k = 1; k < pts.length; k++) {
    const [x0, y0] = pts[k - 1]!;
    const [x1, y1] = pts[k]!;
    d += ` Q${f(x0)},${f(y0)} ${f((x0 + x1) / 2)},${f((y0 + y1) / 2)}`;
  }
  const last = pts.at(-1)!;
  d += ` L${f(last[0])},${f(last[1])} L${width},${height} Z`;
  return d;
}

// --- полоса плеера ----------------------------------------------------------------

/** Ширина символа подписи на полосе (11 px Onest) — для «влезает ли». */
export const LABEL_CHAR_PX = 6.4;
/** Зазор между главами на полосе, px. */
export const BAR_GAP_PX = 3;
/** Уже этого плеер «узкий»: подписи — только номера. */
export const NARROW_BAR_PX = 420;

export type BarPiece = {
  /** Номер главы с 1; без глав — 0 (одна сплошная полоса). */
  n: number;
  /** Доли полосы: начало и конец (0..1). */
  a: number;
  b: number;
  /** Подпись под полосой: «1. Вступление», «1. Бюдж…», «1» или пусто. */
  label: string;
  /** Полное название (подсказка). */
  title: string;
};

/** Подпись главы для ширины `px`: целиком, с многоточием, номер или ничего. */
export function fitLabel(n: number, short: string, px: number, numbersOnly = false, charPx = LABEL_CHAR_PX): string {
  const room = Math.floor((px - 8) / charPx);
  const num = String(n);
  if (!numbersOnly) {
    const full = `${n}. ${short}`;
    if (Array.from(full).length <= room) return full;
    const head = `${n}. `;
    const fit = room - head.length - 1;
    if (fit >= 3) return `${head}${Array.from(short).slice(0, fit).join("").trimEnd()}…`;
  }
  return num.length <= room ? num : "";
}

/** Разбиение полосы на главы: доли, подписи по ширине полосы `px`. */
export function barLayout(chapters: ChapterView[], duration: number, px: number): BarPiece[] {
  if (!chapters.length || !(duration > 0)) return [{ n: 0, a: 0, b: 1, label: "", title: "" }];
  const narrow = px < NARROW_BAR_PX;
  return chapters.map((c, k) => {
    const a = k === 0 ? 0 : Math.min(1, Math.max(0, c.start / duration));
    const next = chapters[k + 1];
    const b = next ? Math.min(1, Math.max(a, next.start / duration)) : 1;
    const width = (b - a) * px - BAR_GAP_PX;
    return { n: c.n, a, b, label: fitLabel(c.n, c.short, width, narrow), title: c.title };
  });
}

// --- лента с фильтрами и главами ---------------------------------------------------

/**
 * Строка ленты: реплика (номер в `turns`), заголовок главы (номер главы) или
 * свёрнутые фильтром реплики «… N реплик» (с `from` по `to` включительно).
 */
export type TurnRow =
  | { kind: "turn"; i: number }
  | { kind: "chapter"; c: number }
  | { kind: "more"; from: number; to: number; count: number };

/**
 * Строки ленты. Без фильтра — все реплики (и заголовки глав перед первой
 * репликой главы). С фильтром по типам — подходящие реплики, а также `shown`
 * (найденные поиском, открытые по ссылке, развёрнутые); подряд идущие
 * остальные — одной строкой «… N реплик». Свёрнутое не переходит через начало
 * главы: заголовки глав видны всегда.
 */
export function layoutRows(
  turns: Turn[],
  opts: {
    types?: (PhraseType | null)[] | null;
    filter?: ReadonlySet<PhraseType>;
    shown?: (i: number) => boolean;
    chapterStart?: Int32Array | null;
  },
): TurnRow[] {
  const rows: TurnRow[] = [];
  const filtering = !!opts.filter?.size && !!opts.types;
  let run: { from: number; to: number; count: number } | null = null;
  const flush = () => {
    if (run && run.count > 0) rows.push({ kind: "more", ...run });
    run = null;
  };
  for (let i = 0; i < turns.length; i++) {
    const c = opts.chapterStart?.[i] ?? -1;
    if (c >= 0) {
      flush();
      rows.push({ kind: "chapter", c });
    }
    const t = turns[i]!;
    const type = opts.types?.[i] ?? null;
    const visible = !filtering || (type !== null && opts.filter!.has(type)) || (opts.shown?.(i) ?? false);
    if (visible) {
      flush();
      rows.push({ kind: "turn", i });
    } else {
      run ??= { from: i, to: i, count: 0 };
      run.to = i;
      if (t.kind !== "break") run.count += 1;
    }
  }
  flush();
  return rows;
}

/** Сколько реплик каждого типа (для чипов фильтра). */
export function typeCounts(types: (PhraseType | null)[] | null): Map<PhraseType, number> {
  const out = new Map<PhraseType, number>();
  for (const t of types ?? []) if (t) out.set(t, (out.get(t) ?? 0) + 1);
  return out;
}
