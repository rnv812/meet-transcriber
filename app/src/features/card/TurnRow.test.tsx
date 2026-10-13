/**
 * Строка реплики (0.6.1, «сценарий»): сетка [левое поле: имя и время] [текст и пометки]. Имя —
 * у первой реплики серии (подряд один говорящий), у продолжения — тихое (видно при наведении);
 * время — у каждой. Бейдж типа и пометки — после текста.
 * Геометрию не проверяем (jsdom её не считает) — только устройство разметки и
 * правила CSS, на которых она держится. Данные выдуманные.
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
const cells = (el: Element) =>
  [...el.children].filter((e) => !e.classList.contains("sr-only")).map((e) => e.className.split(" ")[0]);

function checkRows(c: HTMLElement) {
  const all = rows(c);
  expect(all.length).toBeGreaterThan(0);
  for (const row of all) {
    // Левое поле и тело — прямые ячейки сетки строки, в этом порядке.
    expect(cells(row)).toEqual(["turn__gutter", "turn__body"]);
    // В поле — имя, под ним время; в теле — текст первым.
    expect(cells(row.querySelector(":scope > .turn__gutter")!)).toEqual(["turn__speaker", "turn__time"]);
    expect(row.querySelector(":scope > .turn__body")!.firstElementChild).toHaveClass("turn__text");
  }
}

test("с разметкой: имя один раз на серию, время у каждой, бейдж типа и пометки — после текста", () => {
  const { container } = render(
    <TranscriptView turns={TURNS} colors={new Map()} playable onPlay={() => {}} view={VIEW}
      selected={new Set([1])} onSelect={() => {}} onSpeaker={() => {}} onAskAgent={() => {}} nowTurn={2}
      find={{ q: "задачу", t: null, n: 1 }} />);
  checkRows(container);
  // Ольга дважды подряд: вторая реплика — продолжение серии, имя у неё тихое.
  expect(rows(container).map((r) => r.classList.contains("turn--cont"))).toEqual([false, true, false, false, false, false]);
  expect(rows(container)[1]!.querySelector(".turn__speaker")).toHaveClass("turn__speaker--cont");
  expect(rows(container).map((r) => r.querySelector(".turn__body > .turn__marks > .turn__type")?.getAttribute("aria-label") ?? null))
    .toEqual([null, "Задача", "Решение", null, null, "Риск"]);
  // «в комнате» и «(голос под вопросом)» — тоже пометки после текста.
  expect(container.querySelector(".turn__room")!.parentElement).toHaveClass("turn__marks");
  const unsure = [...container.querySelectorAll(".turn__flag")].find((f) => f.textContent?.includes("под вопросом"))!;
  expect(unsure.parentElement).toHaveClass("turn__marks");
});

test("без разметки и без плеера — та же сетка, без бейджей типа", () => {
  const { container } = render(<Turns turns={TURNS} colors={new Map()} playable={false} onPlay={() => {}} />);
  checkRows(container);
  expect(container.querySelector(".turn__type")).toBeNull();
});

test("текст до спикеров без подписи: в поле — только время", () => {
  const turns = mergeTurns([seg(0, 4, null, "Первая фраза."), seg(9, 12, "Демьян (Вы)", "Своя.")]);
  expect(turns[0]!.speaker).toBe(NO_SPEAKER);
  const { container } = render(<Turns turns={turns} colors={new Map()} playable onPlay={() => {}} textPhase />);
  const [first, second] = rows(container);
  expect(cells(first!.querySelector(".turn__gutter")!)).toEqual(["turn__time"]);
  expect(first!.querySelector(".sr-only")).toHaveTextContent("Спикер ещё не определён");
  expect(cells(second!.querySelector(".turn__gutter")!)).toEqual(["turn__speaker", "turn__time"]);
});

test("CSS: сетка [левое поле | текст], поле шириной --gutter; продолжение серии — имя тихое до наведения", () => {
  const css = readFileSync(resolve(__dirname, "card.css"), "utf-8");
  expect(css).toMatch(/\.turn \{[^}]*display: grid; grid-template-columns: var\(--gutter\) minmax\(0, 1fr\)/);
  expect(css).toMatch(/\.turn__speaker--cont \{[^}]*opacity: 0/);
  expect(css).toMatch(/\.turn:hover \.turn__speaker--cont/);
  // Текущее совпадение поиска не сдвигает текст.
  expect(css).toMatch(/\.turn__text\.hit--current \{[^}]*margin-left: -6px; padding-left: 6px;/);
});
