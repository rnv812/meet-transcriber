import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { CARD_MIN, LIST, RAIL } from "../lib/panes";
import { ShellResize } from "./ShellResize";

function Shell({ list = true }: { list?: boolean }) {
  return <div className="app" data-testid="app"><ShellResize list={list} /></div>;
}

const app = () => screen.getByTestId("app");
const listW = () => parseInt(app().style.getPropertyValue("--list-w"), 10);
const listSplit = () => screen.getByRole("separator", { name: "Ширина списка записей" });

function drag(el: HTMLElement, from: number, to: number) {
  fireEvent.pointerDown(el, { button: 0, clientX: from, pointerId: 1 });
  fireEvent.pointerMove(el, { clientX: to, pointerId: 1 });
  fireEvent.pointerUp(el, { clientX: to, pointerId: 1 });
}

async function resizeWindow(w: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
  await act(async () => {
    window.dispatchEvent(new Event("resize"));
    await new Promise((r) => requestAnimationFrame(() => r(null)));
  });
}

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1400 });
});
afterEach(() => localStorage.clear());

test("рейка постоянной ширины — разделителя у неё нет; у списка разделитель только в «Записях»", () => {
  const { rerender } = render(<Shell />);
  expect(RAIL).toBe(60);
  expect(LIST.def).toBe(300);
  expect(listW()).toBe(LIST.def);
  expect(screen.queryByRole("separator", { name: "Ширина навигации" })).toBeNull();
  expect(app()).not.toHaveAttribute("data-nav-rail");
  expect(listSplit()).toBeInTheDocument();
  rerender(<Shell list={false} />);
  expect(screen.queryByRole("separator", { name: "Ширина списка записей" })).toBeNull();
});

test("прежние ширина и режим навигации (до 0.4) забываются", () => {
  localStorage.setItem("meet.pane.nav", "240");
  localStorage.setItem("meet.pane.nav-mode", "rail");
  render(<Shell />);
  expect(localStorage.getItem("meet.pane.nav")).toBeNull();
  expect(localStorage.getItem("meet.pane.nav-mode")).toBeNull();
});

test("список тянут — ширина меняется в пределах и запоминается; после перезапуска — та же", () => {
  const { unmount } = render(<Shell />);
  drag(listSplit(), 500, 580);
  expect(listW()).toBe(380);
  expect(localStorage.getItem("meet.pane.list")).toBe("380");
  drag(listSplit(), 500, 2000);
  expect(listW()).toBe(LIST.max);
  unmount();
  render(<Shell />);
  expect(listW()).toBe(LIST.max);
  // Двойной щелчок — по умолчанию, в хранилище ничего не остаётся.
  fireEvent.doubleClick(listSplit());
  expect(listW()).toBe(LIST.def);
  expect(localStorage.getItem("meet.pane.list")).toBeNull();
});

test("окно сузили — список ужимается, карточке остаётся минимум; расширили — запомненная ширина вернулась", async () => {
  localStorage.setItem("meet.pane.list", "520");
  render(<Shell />);
  expect(listW()).toBe(520);
  await resizeWindow(1000);
  expect(1000 - RAIL - listW()).toBeGreaterThanOrEqual(CARD_MIN);
  await resizeWindow(900);
  expect(900 - RAIL - listW()).toBe(CARD_MIN);
  // Самое узкое окно: список не уже своего минимума, карточке — что осталось.
  await resizeWindow(820);
  expect(listW()).toBe(LIST.min);
  await resizeWindow(1600);
  expect(listW()).toBe(520);
});

test("разделитель не даёт сузить карточку меньше минимума", () => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1100 });
  render(<Shell />);
  expect(listSplit()).toHaveAttribute("aria-valuemax", String(1100 - CARD_MIN - RAIL));
  drag(listSplit(), 500, 900);
  expect(1100 - RAIL - listW()).toBe(CARD_MIN);
});
