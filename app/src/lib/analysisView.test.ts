import {
  barLayout, buildView, chapterAt, chapterJump, curveLine, curvePath, curveValues, fitLabel, importantSpans, importantTurns, layoutRows,
  LOW_IMPORTANCE, skipTarget, topShare, turnAt, turnImportance, turnJump, turnType, typeCounts, usableAnalysis,
  type ViewParts,
} from "./analysisView";
import { mergeTurns, type Turn } from "./speakers";
import type { Analysis, Segment } from "./types";

const ALL: ViewParts = { types: true, importance: true, chapters: true, insights: true };
const seg = (start: number, end: number, speaker: string, text: string, kind?: "break"): Segment =>
  ({ start, end, speaker, text, uncertain: false, ...(kind ? { kind } : {}) });

// Сегменты 0–1 — одна реплика Анны (пауза меньше 2 с), 2 — Борис, 3 — перерыв, 4–5 — Анна, Борис.
const SEGMENTS: Segment[] = [
  seg(0, 4, "Анна", "Начнём с бюджета."),
  seg(4.5, 8, "Анна", "Кто возьмёт отчёт?"),
  seg(10, 15, "Борис", "Я возьму отчёт к пятнице."),
  seg(15, 15, "", "— перерыв —", "break"),
  seg(300, 310, "Анна", "Есть риск сорвать срок."),
  seg(312, 320, "Борис", "Согласен."),
];
const TURNS = mergeTurns(SEGMENTS);

const analysis = (over: Partial<Analysis> = {}): Analysis => ({
  version: 1, model: "test", created_at: 1, fingerprint: "f", segments: SEGMENTS.length,
  features: ["types", "importance", "chapters", "insights"],
  phrase_types: { 1: "question", 2: "task", 4: "risk", 5: "agreement" },
  importance: { 0: 0.4, 1: 0.9, 2: 0.7, 4: 0.8 },
  chapters: [
    { start_i: 0, end_i: 2, title: "Бюджет и отчёт", short: "Бюджет" },
    { start_i: 3, end_i: 5, title: "Сроки и риски", short: "Сроки" },
  ],
  insights: [{ id: "i1", kind: "attention", text: "Срок отчёта под угрозой.", refs: [4, 2, 2, 99], why: "Риск назван." }],
  ...over,
});

test("реплики карточки склеены из сегментов: номера сегментов у реплик", () => {
  expect(TURNS.map((t) => t.idx)).toEqual([[0, 1], [2], [3], [4], [5]]);
});

test("тип реплики — сильнейший тип её сегментов; одни утверждения — без типа", () => {
  const t = TURNS[0]!;
  expect(turnType(t, { 0: "idea", 1: "question" })).toBe("question");
  expect(turnType(t, { 0: "decision", 1: "question" })).toBe("decision");
  expect(turnType(t, { 0: "statement" })).toBeNull();
  expect(turnType(t, {})).toBeNull(); // нет ключа — утверждение (типы разрежены)
  expect(turnType(t, { 0: "нечто" as never })).toBeNull();
  expect(turnType(TURNS[2]!, { 3: "task" })).toBeNull(); // перерыв
});

test("важность реплики — наибольшая у сегментов; без оценки — низкая", () => {
  expect(turnImportance(TURNS[0]!, { 0: 0.4, 1: 0.9 })).toBe(0.9);
  expect(turnImportance(TURNS[1]!, {})).toBe(LOW_IMPORTANCE);
  expect(turnImportance(TURNS[1]!, { 2: 7 })).toBe(1);
  expect(turnImportance(TURNS[2]!, { 3: 1 })).toBe(0);
});

test("свежий анализ показывается; устаревший и прежний (пока идёт новый) — только при том же числе сегментов", () => {
  const a = analysis();
  expect(usableAnalysis({ state: "ready", analysis: a }, 6)).toBe(a);
  expect(usableAnalysis({ state: "stale", analysis: a }, 6)).toBe(a);
  expect(usableAnalysis({ state: "stale", analysis: a }, 7)).toBeNull();
  expect(usableAnalysis({ state: "running", analysis: a }, 6)).toBe(a);
  expect(usableAnalysis({ state: "failed", analysis: a }, 5)).toBeNull();
  const old = analysis({ segments: undefined });
  expect(usableAnalysis({ state: "ready", analysis: old }, 6)).toBe(old);
  expect(usableAnalysis({ state: "stale", analysis: old }, 6)).toBeNull();
  expect(usableAnalysis({ state: "none" }, 6)).toBeNull();
  expect(usableAnalysis(null, 6)).toBeNull();
});

test("разметка по репликам: типы, важность, главы, наблюдения", () => {
  const v = buildView(TURNS, analysis(), SEGMENTS.length, ALL)!;
  expect(v.types).toEqual(["question", "task", null, "risk", "agreement"]);
  expect(v.importance).toEqual([0.9, 0.7, 0, 0.8, LOW_IMPORTANCE]);
  // Глава 2 начинается с перерыва — первая реплика главы — следующая за ним.
  expect(v.chapters.map((c) => [c.n, c.turn, c.lastTurn, c.start, c.end])).toEqual([
    [1, 0, 1, 0, 15], [2, 3, 4, 300, 320],
  ]);
  expect([...v.chapterStart]).toEqual([0, -1, -1, 1, -1]);
  expect(v.insights[0]!.refs).toEqual([1, 3]); // повторы и чужие номера отброшены
});

test("выключенные части не берутся; пустой анализ — нет разметки", () => {
  const v = buildView(TURNS, analysis(), SEGMENTS.length, { ...ALL, types: false, chapters: false })!;
  expect(v.types).toBeNull();
  expect(v.chapters).toEqual([]);
  expect(v.importance).not.toBeNull();
  expect(buildView(TURNS, analysis(), 6, { types: false, importance: false, chapters: false, insights: false })).toBeNull();
  expect(buildView(TURNS, null, 6, ALL)).toBeNull();
});

test("верхняя доля по важности: неоценённые и низкие не бывают важными", () => {
  const turns: Turn[] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((k) =>
    ({ speaker: "А", start: k * 10, end: k * 10 + 5, texts: ["x"], uncertain: false }));
  const imp = [0.9, 0.2, 0.8, LOW_IMPORTANCE, 0.5, 0.95, 0.31, 0.29, 0.6, 0.7];
  expect(topShare(imp, turns, 0.15).flatMap((x, k) => (x ? [k] : []))).toEqual([0, 5]);
  expect(topShare(imp, turns, 0.3).flatMap((x, k) => (x ? [k] : []))).toEqual([0, 2, 5]);
  expect(topShare(turns.map(() => LOW_IMPORTANCE), turns, 0.3).some(Boolean)).toBe(false);
});

test("строки ленты: заголовки глав, фильтр сворачивает остальное в «… N реплик»", () => {
  const v = buildView(TURNS, analysis(), SEGMENTS.length, ALL)!;
  expect(layoutRows(TURNS, { types: v.types, chapterStart: v.chapterStart })).toEqual([
    { kind: "chapter", c: 0 }, { kind: "turn", i: 0 }, { kind: "turn", i: 1 }, { kind: "turn", i: 2 },
    { kind: "chapter", c: 1 }, { kind: "turn", i: 3 }, { kind: "turn", i: 4 },
  ]);
  expect(layoutRows(TURNS, { types: v.types, chapterStart: v.chapterStart, filter: new Set(["risk"]) })).toEqual([
    { kind: "chapter", c: 1 }, { kind: "turn", i: 3 }, { kind: "more", from: 4, to: 4, count: 1 },
  ]);
  // Глава без подходящих реплик скрыта целиком (и заголовок, и «… N реплик»).
  expect(layoutRows(TURNS, { types: v.types, chapterStart: v.chapterStart, filter: new Set(["question"]) })).toEqual([
    { kind: "chapter", c: 0 }, { kind: "turn", i: 0 }, { kind: "more", from: 1, to: 2, count: 1 },
  ]);
  expect(layoutRows(TURNS, { types: v.types, chapterStart: v.chapterStart, filter: new Set(["idea"]) })).toEqual([]);
  // Найденное поиском или открытое по ссылке — видно несмотря на фильтр.
  // Свёрнутый один перерыв (ни одной реплики) строки «… 0 реплик» не даёт.
  expect(layoutRows(TURNS, { types: v.types, filter: new Set(["risk"]), shown: (i) => i === 1 })).toEqual([
    { kind: "more", from: 0, to: 0, count: 1 }, { kind: "turn", i: 1 }, { kind: "turn", i: 3 },
    { kind: "more", from: 4, to: 4, count: 1 },
  ]);
  expect(typeCounts(v.types)).toEqual(new Map([["question", 1], ["task", 1], ["risk", 1], ["agreement", 1]]));
});

test("время: реплика и глава в момент t, переходы по главам и репликам", () => {
  const v = buildView(TURNS, analysis(), SEGMENTS.length, ALL)!;
  expect(turnAt(TURNS, 0)).toBe(0);
  expect(turnAt(TURNS, 12)).toBe(1);
  expect(turnAt(TURNS, 200)).toBe(1); // перерыв пропускается
  expect(turnAt(TURNS, 305)).toBe(3);
  expect(chapterAt(v.chapters, 100)).toBe(0);
  expect(chapterAt(v.chapters, 300)).toBe(1);
  expect(chapterJump(v.chapters, 100, 1)).toBe(300);
  expect(chapterJump(v.chapters, 310, -1)).toBe(300); // от начала главы ушли дальше 2 с — к её началу
  expect(chapterJump(v.chapters, 301, -1)).toBe(0); // в самом начале — к предыдущей
  expect(chapterJump(v.chapters, 310, 1)).toBeNull();
  expect(turnJump(TURNS, 5, 1)).toBe(10);
  expect(turnJump(TURNS, 12, -1)).toBe(10);
  expect(turnJump(TURNS, 10.5, -1)).toBe(0);
});

test("«Только важное»: верхние 30 %, склейка при зазоре меньше 4 с, запас 1 с", () => {
  const turns: Turn[] = [
    [0, 10, 0.9], [11, 20, 0.85], [30, 40, LOW_IMPORTANCE], [50, 55, 0.95], [57, 60, 0.2], [62, 70, 0.9],
    [80, 90, LOW_IMPORTANCE], [100, 110, LOW_IMPORTANCE], [120, 130, LOW_IMPORTANCE], [140, 150, LOW_IMPORTANCE],
    [160, 170, LOW_IMPORTANCE], [180, 190, LOW_IMPORTANCE], [200, 210, LOW_IMPORTANCE], [220, 230, LOW_IMPORTANCE],
  ].map(([start, end]) => ({ speaker: "А", start: start!, end: end!, texts: ["x"], uncertain: false }));
  const imp = [0.9, 0.85, LOW_IMPORTANCE, 0.95, 0.2, 0.9, ...Array(8).fill(LOW_IMPORTANCE)];
  const spans = importantSpans(turns, imp, 240);
  // 0–20 (зазор 1 с) и 50–55 + 62–70 (зазор 7 с — не склеены).
  expect(spans).toEqual([{ start: 0, end: 21 }, { start: 49, end: 56 }, { start: 61, end: 71 }]);
  expect(skipTarget(spans, 5)).toBeNull();
  expect(skipTarget(spans, 21)).toBe(49);
  expect(skipTarget(spans, 58)).toBe(61);
  expect(skipTarget(spans, 71)).toBe(Infinity);
  expect(skipTarget([], 3)).toBe(Infinity);
});

test("кривая важности: точки 0..1, пик — у важного места; путь SVG — одна линия с заливкой", () => {
  const values = curveValues(TURNS, [0.9, 0.7, 0, 0.8, LOW_IMPORTANCE], 320, 64);
  expect(values).toHaveLength(64);
  expect(Math.max(...values)).toBeCloseTo(1);
  expect(values.every((v) => v >= 0 && v <= 1)).toBe(true);
  const peak = values.indexOf(Math.max(...values));
  expect(peak).toBeLessThan(8); // начало встречи важнее середины
  const d = curvePath(values);
  expect(d.startsWith("M0,100 L")).toBe(true);
  expect(d.endsWith("L1000,100 Z")).toBe(true);
  expect(d.match(/M/g)).toHaveLength(1);
  // Контур (0.5) — тот же верх без спуска к низу и замыкания.
  const line = curveLine(values);
  expect(line.startsWith("M0,")).toBe(true);
  expect(line).not.toMatch(/Z$|M0,100 /);
  expect(d).toContain(line.slice(1));
  expect(curveLine([])).toBe("");
});

test("важные реплики для рисок — верхние 30 % по важности, по порядку, без пауз", () => {
  expect(importantTurns(TURNS, [0.9, 0.7, 0, 0.8, LOW_IMPORTANCE])).toEqual([0]) // 4 реплики без перерыва → верхние 30 % = одна;
  expect(importantTurns(TURNS, [0.1, 0.1, 0.1, 0.1, 0.1])).toEqual([]);
});

test("подпись главы на полосе: целиком, с многоточием, номер или ничего", () => {
  expect(fitLabel(1, "Вступление", 200)).toBe("1. Вступление");
  expect(fitLabel(2, "Бюджет на квартал", 80)).toBe("2. Бюджет…");
  expect(fitLabel(3, "Бюджет", 30)).toBe("3");
  expect(fitLabel(12, "Бюджет", 12)).toBe("");
  expect(fitLabel(1, "Вступление", 200, true)).toBe("1"); // узкий плеер — только номера
});

test("полоса плеера делится на главы: доли от длительности, первая — с начала, последняя — до конца", () => {
  const v = buildView(TURNS, analysis(), SEGMENTS.length, ALL)!;
  const wide = barLayout(v.chapters, 400, 800);
  expect(wide.map((p) => [p.n, p.a, p.b])).toEqual([[1, 0, 0.75], [2, 0.75, 1]]);
  expect(wide.map((p) => p.label)).toEqual(["1. Бюджет", "2. Сроки"]);
  expect(wide[0]!.title).toBe("Бюджет и отчёт");
  // Узкий плеер — только номера.
  expect(barLayout(v.chapters, 400, 300).map((p) => p.label)).toEqual(["1", "2"]);
  // Без глав — одна сплошная полоса.
  expect(barLayout([], 400, 800)).toEqual([{ n: 0, a: 0, b: 1, label: "", title: "" }]);
});
