// Последние системные `title` в ui/ (категория, бейдж «ИИ», ссылка Jira, разделитель) —
// облачка Aurora (ui/Tip). У ui/Truncate `title` остаётся намеренно: это полный текст обрезанной строки.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { AiBadge } from "./AiBadge";
import { CategoryMark, CategoryMenu } from "./Category";
import { JiraLink } from "./LinkedText";
import { Splitter } from "./Splitter";
import { TIP_DELAY_MS } from "./Tip";

afterEach(() => { vi.useRealTimers(); });

const tipEl = () => document.body.querySelector<HTMLElement>(".tooltip.tip");
const hover = (el: Element) => {
  fireEvent.mouseEnter(el);
  act(() => { vi.advanceTimersByTime(TIP_DELAY_MS); });
};

const daily = { id: "daily", name: "Дейлик", color: "#7c5cff", description: "Короткая встреча команды" };
const linker = { re: /\b[A-Z][A-Z0-9]+-\d+\b/g, base: "https://jira.example.com" };

test("метка категории: описание и облачко вместо title", () => {
  vi.useFakeTimers();
  const { container } = render(<CategoryMark category={daily} />);
  const mark = container.querySelector<HTMLElement>(".cat-mark")!;
  expect(mark).not.toHaveAttribute("title");
  expect(mark).toHaveAccessibleDescription("Дейлик — Короткая встреча команды");
  hover(mark);
  expect(tipEl()).toHaveTextContent("Дейлик — Короткая встреча команды");
});

test("меню категорий: описание пункта — облачком сбоку; без описания — подсказки нет", () => {
  vi.useFakeTimers();
  render(<CategoryMenu list={[daily, { ...daily, id: "x", name: "Прочее", description: "" }]} current={null}
    onPick={() => {}} />);
  const item = screen.getByRole("menuitemradio", { name: "Дейлик" });
  expect(item).not.toHaveAttribute("title");
  expect(item).toHaveAccessibleDescription("Короткая встреча команды");
  hover(item);
  expect(tipEl()).toHaveAttribute("data-side", "right");
  const plain = screen.getByRole("menuitemradio", { name: "Прочее" });
  expect(plain).not.toHaveAccessibleDescription();
});

test("бейдж «ИИ»: облачко с моделью; текст для диктора — внутри, описанием не дублируется", () => {
  vi.useFakeTimers();
  const { container } = render(<AiBadge by="Claude Code (sonnet)" />);
  const badge = container.querySelector<HTMLElement>(".ai-badge")!;
  expect(badge).not.toHaveAttribute("title");
  expect(badge).not.toHaveAttribute("aria-description");
  hover(badge);
  expect(tipEl()).toHaveTextContent("Название предложено ИИ (Claude Code (sonnet)) — нажмите, чтобы изменить");
});

test("ссылка Jira: «открыть в Jira» — описанием и облачком, без title", () => {
  vi.useFakeTimers();
  render(<JiraLink linker={linker} keyText="SPR-131">SPR-131</JiraLink>);
  const link = screen.getByRole("link", { name: "SPR-131" });
  expect(link).not.toHaveAttribute("title");
  expect(link).toHaveAccessibleDescription("Открыть SPR-131 в Jira");
  hover(link);
  expect(tipEl()).toHaveTextContent("Открыть SPR-131 в Jira");
});

test("разделитель: подсказка облачком (у вертикального — сбоку), нажатие её прячет", () => {
  vi.useFakeTimers();
  render(<Splitter label="Ширина списка" value={300} min={260} max={560} panel="before"
    onPreview={() => {}} onCommit={() => {}} onReset={() => {}} />);
  const bar = screen.getByRole("separator", { name: "Ширина списка" });
  expect(bar).not.toHaveAttribute("title");
  expect(bar).toHaveAccessibleDescription("Потяните, чтобы изменить ширину. Двойной щелчок или Enter — как было");
  hover(bar);
  expect(tipEl()).toHaveAttribute("data-side", "right");
  fireEvent.pointerDown(bar, { button: 0, clientX: 300, pointerId: 1 });
  expect(tipEl()).toBeNull();
});

test("в этих примитивах системного title больше нет", () => {
  for (const file of ["Category.tsx", "AiBadge.tsx", "LinkedText.tsx", "Splitter.tsx"]) {
    const src = readFileSync(join(process.cwd(), "src", "ui", file), "utf8");
    expect(src, file).not.toMatch(/\stitle=\{/);
  }
});
