/**
 * Грубая страховка производительности (M3): 3000 реплик с разметкой
 * отрисовываются за разумное время, а разметка по репликам считается быстро.
 * Бюджеты щедрые (jsdom медленнее браузера в разы): тест ловит лишь порядок —
 * например, квадратичный проход по репликам на каждую реплику.
 */

import { render } from "@testing-library/react";
import { buildView, curveValues, importantSpans, layoutRows } from "../../lib/analysisView";
import { mergeTurns } from "../../lib/speakers";
import type { Analysis, PhraseType, Segment } from "../../lib/types";
import { TranscriptView } from "./TranscriptView";

const N = 3000;
const TYPES: PhraseType[] = ["question", "decision", "task", "risk", "idea", "agreement", "objection"];
const SPEAKERS = ["Анна", "Борис", "Виктор", "Галина"];
const segments: Segment[] = Array.from({ length: N }, (_, i) => ({
  start: i * 6, end: i * 6 + 4, speaker: SPEAKERS[i % SPEAKERS.length]!, uncertain: false,
  text: `Реплика номер ${i}: обсуждаем бюджет, сроки и задачи команды на следующий квартал.`,
}));
const analysis: Analysis = {
  version: 1, model: "test", created_at: 1, fingerprint: "f", segments: N,
  features: ["types", "importance", "chapters", "insights"],
  phrase_types: Object.fromEntries(Array.from({ length: N / 3 }, (_, k) => [String(k * 3), TYPES[k % TYPES.length]!])),
  importance: Object.fromEntries(Array.from({ length: N / 2 }, (_, k) => [String(k * 2), (k % 10) / 10])),
  chapters: Array.from({ length: 12 }, (_, k) => ({ start_i: k * 250, end_i: k * 250 + 249, title: `Глава ${k + 1}`, short: `Г${k + 1}` })),
  insights: Array.from({ length: 12 }, (_, k) => ({ id: `i${k}`, kind: "insight", text: `Наблюдение ${k}`, refs: [k * 200], why: "" })),
};

test("разметка 3000 реплик, строки ленты, кривая и «Только важное» — быстро", () => {
  const turns = mergeTurns(segments);
  const t0 = performance.now();
  const view = buildView(turns, analysis, N, { types: true, importance: true, chapters: true, insights: true })!;
  layoutRows(turns, { types: view.types, chapterStart: view.chapterStart, filter: new Set(["risk"]) });
  curveValues(turns, view.importance!, N * 6);
  importantSpans(turns, view.importance!, N * 6);
  expect(performance.now() - t0).toBeLessThan(500);
});

test("3000 реплик с разметкой отрисовываются в пределах щедрого бюджета", () => {
  const turns = mergeTurns(segments);
  const view = buildView(turns, analysis, N, { types: true, importance: true, chapters: true, insights: true })!;
  const t0 = performance.now();
  const { container } = render(
    <TranscriptView turns={turns} colors={new Map()} playable onPlay={() => {}} view={view} onAskChapter={() => {}} />);
  const took = performance.now() - t0;
  expect(container.querySelectorAll(".turn")).toHaveLength(N);
  expect(container.querySelectorAll(".chapter-head")).toHaveLength(12);
  expect(took).toBeLessThan(8000);
});
