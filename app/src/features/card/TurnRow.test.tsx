/**
 * Строка реплики (Atlas Aurora) — сетка [время 64 px] [имя / текст]: время и имя —
 * соседние ячейки первой строки, имя у всех реплик начинается ровно, бейдж типа и
 * пометки — после имени в его строке, не перед ним.
 * Геометрию не проверяем (jsdom её не считает) — только устройство разметки и
 * правила CSS, на которых держится выравнивание. Данные выдуманные.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render } from "@testing-library/react";
import { buildView } from "../../lib/analysisView";
import { mergeTurns, NO_SPEAKER } from "../../lib/speakers";
import type { Analysis, Segment } from "../../lib/types";
import { TranscriptView } from "./TranscriptView";
import { Turns } from "./Turns";

const seg = (start: number, end: number, speaker: string | null, text: string, extra: Partial<Segment> = {}): Segment =>
  ({ start, end, speaker, text, uncertain: false, ...extra });
const SEGMENTS: Segment[] = [
  seg(35, 40, "Ольга", "Отвечала по тикетам сама."),
  seg(43, 50, "Ольга", "Одну задачу перевела в закрытую."),
  seg(51, 55, "Ольга", "И ещё мысль подряд."),
  seg(119, 124, "Демьян (Вы)", "Сперва то, что просила безопасность."),
  seg(125, 128, "Гость", "Я рядом, добавлю.", { track: "mic", room: true }),
  seg(129, 132, "Демьян (Вы)", "Кажется, это я.", { track: "mic", uncertain: true }),
  seg(133, 136, "Спикер 3", "Поставщик поднимет цены."),
];
const ANALYSIS: Analysis = {
  version: 1, model: "test", created_at: 1, fingerprint: "f", segments: SEGMENTS.length,
  features: ["types", "importance"],
  phrase_types: { 1: "task", 3: "decision", 6: "risk" },
  importance: { 1: 0.95 },
  chapters: [], insights: [],
};
const TURNS = mergeTurns(SEGMENTS);
const VIEW = buildView(TURNS, ANALYSIS, SEGMENTS.length, { types: true, importance: true, chapters: false, insights: false });

const rows = (c: HTMLElement) => [...c.querySelectorAll<HTMLElement>(".turn")];
/** Ячейки строки (без скрытых подписей для чтения с экрана). */
const cells = (row: HTMLElement) =>
  [...row.children].filter((e) => !e.classList.contains("sr-only")).map((e) => e.className.split(" ")[0]);

function checkRows(c: HTMLElement) {
  const all = rows(c);
  expect(all.length).toBeGreaterThan(0);
  for (const row of all) {
    // Время, строка имени, текст — прямые ячейки сетки строки, в этом порядке.
    expect(cells(row)).toEqual(["turn__time", "turn__head", "turn__text"]);
    const head = row.querySelector(":scope > .turn__head")!;
    // Имя — первое в своей строке: перед ним ни бейджа типа, ни пометок.
    expect(head.firstElementChild).toHaveClass("turn__speaker");
    const type = head.querySelector(".turn__type");
    if (type) expect(type.previousElementSibling).not.toBeNull();
  }
}

test("с разметкой: бейдж типа — в строке имени после имени, имя первое в строке", () => {
  const { container } = render(
    <TranscriptView turns={TURNS} colors={new Map()} playable onPlay={() => {}} view={VIEW}
      selected={new Set([1])} onSelect={() => {}} onSpeaker={() => {}} onAskAgent={() => {}} nowTurn={2}
      find={{ q: "задачу", t: null, n: 1 }} />);
  checkRows(container);
  expect(rows(container).map((r) => r.querySelector(".turn__head > .turn__type")?.getAttribute("aria-label") ?? null))
    .toEqual([null, "Задача", "Решение", null, null, "Риск"]);
  // «в комнате» и «(голос под вопросом)» — после имени, в той же строке.
  const room = container.querySelector(".turn__room")!;
  expect(room.parentElement).toHaveClass("turn__head");
  expect(room.previousElementSibling).toHaveClass("turn__speaker");
  const unsure = [...container.querySelectorAll(".turn__flag")].find((f) => f.textContent?.includes("под вопросом"))!;
  expect(unsure.parentElement).toHaveClass("turn__head");
  expect(unsure.previousElementSibling).toHaveClass("turn__speaker");
});

test("без разметки и без плеера — та же сетка, без бейджей типа", () => {
  const { container } = render(<Turns turns={TURNS} colors={new Map()} playable={false} onPlay={() => {}} />);
  checkRows(container);
  expect(container.querySelector(".turn__type")).toBeNull();
});

test("текст до спикеров без подписи: текст — на строке времени (строки имени нет)", () => {
  const turns = mergeTurns([seg(0, 4, null, "Первая фраза."), seg(9, 12, "Демьян (Вы)", "Своя.")]);
  expect(turns[0]!.speaker).toBe(NO_SPEAKER);
  const { container } = render(<Turns turns={turns} colors={new Map()} playable onPlay={() => {}} textPhase />);
  const [first, second] = rows(container);
  expect(cells(first!)).toEqual(["turn__time", "turn__text"]);
  expect(first!.querySelector(".sr-only")).toHaveTextContent("Спикер ещё не определён");
  expect(cells(second!)).toEqual(["turn__time", "turn__head", "turn__text"]);
});

test("CSS: сетка [время 64 px | имя и текст] с общей высотой первой строки у времени и имени", () => {
  const css = readFileSync(resolve(__dirname, "card.css"), "utf-8");
  expect(css).toMatch(/\.turn \{[^}]*display: grid; grid-template-columns: 64px minmax\(0, 1fr\)/);
  // Высота строки — после `font: inherit` (иначе шорткат её сбрасывает).
  expect(css).toMatch(/\.turn__time \{[^}]*height: var\(--turn-line\);[^}]*font: inherit;[^}]*line-height: var\(--turn-line\)/);
  expect(css).toMatch(/\.turn__head \{[^}]*min-height: var\(--turn-line\); line-height: var\(--turn-line\)/);
  expect(css).toMatch(/button\.turn__speaker \{[^}]*line-height: inherit/);
  // Текущее совпадение поиска не сдвигает текст.
  expect(css).toMatch(/\.turn__text\.hit--current \{[^}]*margin-left: -6px; padding-left: 6px;/);
});
