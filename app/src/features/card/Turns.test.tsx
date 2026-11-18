/**
 * Лента реплик: «(нахлёст)» — подсказка с клавиатуры и ▶ по самому нахлёсту;
 * точное время реплики — при наведении на её время.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { mergeTurns } from "../../lib/speakers";
import type { Segment } from "../../lib/types";
import { Turns } from "./Turns";

const seg = (start: number, end: number, text: string, extra: Partial<Segment> = {}): Segment => ({
  start, end, speaker: "Спикер 1", text, uncertain: false, ...extra,
});
const turns = mergeTurns([
  seg(1, 3, "Добрый день."),
  seg(3.24, 5.6, "Да-да, слышно.", { uncertain: true }),
]);

test("склейка помнит, где в реплике нахлёст", () => {
  expect(turns).toHaveLength(1);
  expect(turns[0]!.overlap).toEqual({ start: 3.24, end: 5.6 });
});

test("«(нахлёст)»: подсказка открывается с клавиатуры, ▶ играет нахлёст", async () => {
  const onPlayRange = vi.fn();
  render(<Turns turns={turns} colors={new Map()} playable onPlay={vi.fn()} onPlayRange={onPlayRange} />);
  const flag = screen.getByText("(нахлёст)");
  expect(flag).toHaveAccessibleDescription(/говорили одновременно/);
  while (document.activeElement !== flag) await userEvent.tab();
  expect(await screen.findByText(/говорили одновременно/, { selector: ".tooltip" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Прослушать нахлёст с 00:03" }));
  expect(onPlayRange).toHaveBeenCalledWith(3.24, 5.6);
});

test("без звука у нахлёста нет ▶, подсказка остаётся", () => {
  render(<Turns turns={turns} colors={new Map()} playable={false} onPlay={vi.fn()} onPlayRange={vi.fn()} />);
  expect(screen.getByText("(нахлёст)")).toHaveAccessibleDescription(/говорили одновременно/);
  expect(screen.queryByRole("button", { name: /нахлёст/ })).toBeNull();
});

test("наведение на время реплики — точное время начала и конца", async () => {
  render(<Turns turns={turns} colors={new Map()} playable onPlay={vi.fn()} />);
  const row = document.querySelector<HTMLElement>("[data-turn]")!;
  const time = within(row).getByRole("button", { name: "Слушать с 00:01" });
  expect(time).toHaveAccessibleDescription("00:01,0–00:05,6");
  await userEvent.hover(time);
  expect(await screen.findByText("00:01,0–00:05,6", { selector: ".tooltip" })).toBeInTheDocument();
});
