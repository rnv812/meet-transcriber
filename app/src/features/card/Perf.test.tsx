/**
 * Грубая страховка производительности (M3): разметка и отрисовка реплик растут
 * линейно с длиной встречи. Сравниваем 300 и 3000 реплик, а не секундомер:
 * jsdom под нагрузкой (параллельная сборка, медленный CI) медленнее в разы, а
 * отношение времён от нагрузки почти не зависит. Линейный рост — около ×10,
 * квадратичный проход по репликам на каждую реплику — около ×100.
 */

import { cleanup, render } from "@testing-library/react";
import { buildView, curveValues, importantSpans, layoutRows } from "../../lib/analysisView";
import { mergeTurns } from "../../lib/speakers";
import type { Analysis, PhraseType, Segment } from "../../lib/types";
import { TranscriptView } from "./TranscriptView";

const SMALL = 300;
const N = 3000;
/** Во сколько раз 3000 реплик могут быть дольше 300: линейно ×10, запас на шум. */
const MAX_RATIO = 30;
/**
 * Отрисовка в jsdom в десятки раз дороже разметки (3000 реплик — 1,5 с в тишине,
 * под нагрузкой >20 с и таймаут теста), поэтому сравниваем размеры поменьше:
 * 240 и 1200 реплик (кратно 12 и 15, как требует meeting()), линейно ×5, квадратично ×25.
 */
const DRAW_SMALL = 240;
const DRAW_N = 1200;
const DRAW_MAX_RATIO = 12;
/** Последний рубеж на случай совсем медленной машины — порядок, а не бюджет. */
const SANITY_MS = 30_000;
const TYPES: PhraseType[] = ["question", "decision", "task", "risk", "idea", "agreement", "objection"];
const SPEAKERS = ["Анна", "Борис", "Виктор", "Галина"];
const ALL = { types: true, importance: true, chapters: true, insights: true };

function meeting(n: number): { segments: Segment[]; analysis: Analysis } {
  const segments: Segment[] = Array.from({ length: n }, (_, i) => ({
    start: i * 6, end: i * 6 + 4, speaker: SPEAKERS[i % SPEAKERS.length]!, uncertain: false,
    text: `Реплика номер ${i}: обсуждаем бюджет, сроки и задачи команды на следующий квартал.`,
  }));
  const chapter = n / 12;
  const analysis: Analysis = {
    version: 1, model: "test", created_at: 1, fingerprint: "f", segments: n,
    features: ["types", "importance", "chapters", "insights"],
    phrase_types: Object.fromEntries(Array.from({ length: n / 3 }, (_, k) => [String(k * 3), TYPES[k % TYPES.length]!])),
    importance: Object.fromEntries(Array.from({ length: n / 2 }, (_, k) => [String(k * 2), (k % 10) / 10])),
    chapters: Array.from({ length: 12 }, (_, k) => ({
      start_i: k * chapter, end_i: k * chapter + chapter - 1, title: `Глава ${k + 1}`, short: `Г${k + 1}` })),
    insights: Array.from({ length: 12 }, (_, k) => ({ id: `i${k}`, kind: "insight", text: `Наблюдение ${k}`, refs: [k * (n / 15)], why: "" })),
  };
  return { segments, analysis };
}

/**
 * Лучшее из нескольких замеров (`reps` прогонов подряд в каждом): одиночный
 * выброс (сборка мусора) не решает, а быстрая работа меряется не в долях мс.
 */
function best(times: number, fn: () => void, reps = 1): number {
  let min = Infinity;
  for (let i = 0; i < times; i++) {
    const t0 = performance.now();
    for (let r = 0; r < reps; r++) fn();
    min = Math.min(min, performance.now() - t0);
  }
  return min;
}

function markup(n: number) {
  const { segments, analysis } = meeting(n);
  const turns = mergeTurns(segments);
  return () => {
    const view = buildView(turns, analysis, n, ALL)!;
    layoutRows(turns, { types: view.types, chapterStart: view.chapterStart, filter: new Set(["risk"]) });
    curveValues(turns, view.importance!, n * 6);
    importantSpans(turns, view.importance!, n * 6);
  };
}

function draw(n: number) {
  const { segments, analysis } = meeting(n);
  const turns = mergeTurns(segments);
  const view = buildView(turns, analysis, n, ALL)!;
  return () => {
    const { container } = render(
      <TranscriptView turns={turns} colors={new Map()} playable onPlay={() => {}} view={view} onAskChapter={() => {}} />);
    expect(container.querySelectorAll(".turn")).toHaveLength(n);
    expect(container.querySelectorAll(".chapter-head")).toHaveLength(12);
    cleanup();
  };
}

test("разметка, строки ленты, кривая и «Только важное» растут линейно", () => {
  markup(SMALL)(); // прогрев JIT
  const small = Math.max(best(5, markup(SMALL), 20), 1);
  const large = best(3, markup(N), 20);
  expect(large).toBeLessThan(SANITY_MS);
  expect(large / small).toBeLessThan(MAX_RATIO);
});

test("отрисовка реплик с разметкой растёт линейно", () => {
  draw(DRAW_SMALL)(); // прогрев
  const small = Math.max(best(3, draw(DRAW_SMALL)), 5);
  const large = best(2, draw(DRAW_N));
  expect(large).toBeLessThan(SANITY_MS);
  expect(large / small).toBeLessThan(DRAW_MAX_RATIO);
}, 60_000);
