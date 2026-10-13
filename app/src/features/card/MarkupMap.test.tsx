/**
 * Карта разметки (0.6.1): узкая полоса справа от ленты — вся встреча по времени. Отметки
 * типов реплик и наблюдений на своих местах; цвет акцента — у наблюдений и выбранного
 * фильтра, остальные — нейтральные. Щелчок и стрелки — переход к реплике. Данные выдуманные.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { InsightView } from "../../lib/analysisView";
import type { Turn } from "../../lib/speakers";
import type { PhraseType } from "../../lib/types";
import { MarkupMap } from "./MarkupMap";

const turn = (start: number, speaker: string, text: string): Turn =>
  ({ start, end: start + 5, speaker, texts: [text], uncertain: false });
const TURNS = [
  turn(0, "Анна", "Начнём."),
  turn(25, "Борис", "Когда показ?"),
  turn(50, "Анна", "Решили: в пятницу."),
  turn(75, "Борис", "Без шлюзов не покажем."),
  turn(95, "Анна", "Итог."),
];
const TYPES: (PhraseType | null)[] = [null, "question", "decision", "risk", null];
const INSIGHTS: InsightView[] = [{ id: "i1", kind: "attention", text: "Срок не подтверждён.", why: "", refs: [3] }];

function map(props: Partial<Parameters<typeof MarkupMap>[0]> = {}) {
  const onJump = vi.fn();
  const view = render(
    <MarkupMap turns={TURNS} types={TYPES} insights={INSIGHTS} filter={new Set(["question"])} insightsOn={false}
      onJump={onJump} onScrollTo={() => {}} {...props} />);
  return { onJump, ...view };
}

test("отметки — на своих местах по времени встречи; наблюдение — широкое; цвет — у наблюдения и выбранного типа", () => {
  map();
  const group = screen.getByRole("group", { name: "Карта разметки" });
  const marks = within(group).getAllByRole("button");
  // По времени: вопрос 00:25, решение 00:50, наблюдение и риск 01:15.
  expect(marks.map((m) => m.getAttribute("aria-label"))).toEqual([
    "Вопрос, 00:25: Когда показ?",
    "Решение, 00:50: Решили: в пятницу.",
    "Наблюдение, 01:15: Срок не подтверждён.",
    "Риск, 01:15: Без шлюзов не покажем.",
  ]);
  expect(marks[0]!.style.top).toBe("25%");
  expect(marks[2]).toHaveClass("markup-map__mark--wide", "markup-map__mark--on");
  expect(marks[0]).toHaveClass("markup-map__mark--on"); // фильтр «Вопросы»
  expect(marks[1]).not.toHaveClass("markup-map__mark--on");
  expect(group.querySelector(".markup-map__frame")).not.toBeNull();
});

test("щелчок по отметке — переход к реплике; стрелки ходят по отметкам, Tab — одна точка", async () => {
  const { onJump } = map();
  const marks = within(screen.getByRole("group", { name: "Карта разметки" })).getAllByRole("button");
  await userEvent.click(marks[1]!);
  expect(onJump).toHaveBeenCalledWith(2);
  expect(marks.map((m) => m.tabIndex)).toEqual([-1, 0, -1, -1]);
  fireEvent.keyDown(marks[1]!, { key: "ArrowDown" });
  expect(marks[2]).toHaveFocus();
  fireEvent.keyDown(marks[2]!, { key: "Home" });
  expect(marks[0]).toHaveFocus();
  fireEvent.keyDown(marks[0]!, { key: "Enter" });
  expect(onJump).toHaveBeenLastCalledWith(1);
});

test("щелчок по полосе мимо отметок — прокрутка к реплике в этом месте встречи, без перехода", () => {
  const onScrollTo = vi.fn();
  const { onJump } = map({ onScrollTo });
  const strip = document.querySelector<HTMLElement>(".markup-map")!;
  strip.getBoundingClientRect = () => ({ top: 0, bottom: 200, height: 200, left: 0, right: 14, width: 14, x: 0, y: 0, toJSON: () => ({}) });
  fireEvent.pointerDown(strip, { clientY: 100, button: 0 }); // середина — 50 с
  expect(onScrollTo).toHaveBeenCalledWith(2);
  expect(onJump).not.toHaveBeenCalled();
});

test("нечего показать — карты нет", () => {
  const { container } = map({ types: [null, null, null, null, null], insights: [] });
  expect(container.firstChild).toBeNull();
});
