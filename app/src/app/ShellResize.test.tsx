import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test } from "vitest";
import { CARD_MIN, LIST, NAV } from "../lib/panes";
import { ShellResize } from "./ShellResize";

function Shell({ list = true }: { list?: boolean }) {
  return <div className="app" data-testid="app"><ShellResize list={list} /></div>;
}

const app = () => screen.getByTestId("app");
const navW = () => parseInt(app().style.getPropertyValue("--nav-w"), 10);
const listW = () => parseInt(app().style.getPropertyValue("--list-w"), 10);
const navSplit = () => screen.getByRole("separator", { name: "Ширина навигации" });
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

test("ширины ставятся до отрисовки; у списка разделитель только в «Записях»", () => {
  const { rerender } = render(<Shell />);
  expect(navW()).toBe(NAV.def);
  expect(listW()).toBe(LIST.def);
  expect(app()).not.toHaveAttribute("data-nav-rail");
  expect(listSplit()).toBeInTheDocument();
  rerender(<Shell list={false} />);
  expect(screen.queryByRole("separator", { name: "Ширина списка записей" })).toBeNull();
});

test("список тянут — ширина меняется в пределах и запоминается; после перезапуска — та же", () => {
  const { unmount } = render(<Shell />);
  drag(listSplit(), 500, 580);
  expect(listW()).toBe(400);
  expect(localStorage.getItem("meet.pane.list")).toBe("400");
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

test("навигацию сузили ниже порога — полоса значков; вытянули — снова с подписями", () => {
  render(<Shell />);
  drag(navSplit(), 200, 70);
  expect(app()).toHaveAttribute("data-nav-rail");
  expect(navW()).toBe(NAV.rail);
  expect(localStorage.getItem("meet.pane.nav-mode")).toBe("rail");
  drag(navSplit(), 56, 236);
  expect(app()).not.toHaveAttribute("data-nav-rail");
  expect(navW()).toBe(236);
  expect(localStorage.getItem("meet.pane.nav-mode")).toBeNull();
  expect(localStorage.getItem("meet.pane.nav")).toBe("236");
});

test("навигацию ← до минимума и дальше — полоса; → из полосы — минимум", () => {
  render(<Shell />);
  navSplit().focus();
  fireEvent.keyDown(navSplit(), { key: "Home" });
  expect(app()).toHaveAttribute("data-nav-rail");
  fireEvent.keyDown(navSplit(), { key: "ArrowRight" });
  expect(app()).not.toHaveAttribute("data-nav-rail");
  expect(navW()).toBe(NAV.min);
});

test("окно сузили — панели ужимаются, карточке остаётся минимум; расширили — запомненные ширины вернулись", async () => {
  localStorage.setItem("meet.pane.nav", "300");
  localStorage.setItem("meet.pane.list", "520");
  render(<Shell />);
  expect([navW(), listW()]).toEqual([300, 520]);
  await resizeWindow(1200);
  expect(1200 - navW() - listW()).toBeGreaterThanOrEqual(CARD_MIN);
  expect(app()).not.toHaveAttribute("data-nav-rail");
  await resizeWindow(1000);
  expect(app()).toHaveAttribute("data-nav-rail");
  expect(1000 - navW() - listW()).toBeGreaterThanOrEqual(CARD_MIN);
  await resizeWindow(900);
  expect(900 - navW() - listW()).toBeGreaterThanOrEqual(CARD_MIN);
  await resizeWindow(1600);
  expect([navW(), listW()]).toEqual([300, 520]);
});

test("разделитель не даёт сузить карточку меньше минимума", () => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1100 });
  render(<Shell />);
  expect(listSplit()).toHaveAttribute("aria-valuemax", String(1100 - CARD_MIN - NAV.def));
  drag(listSplit(), 500, 900);
  expect(1100 - navW() - listW()).toBe(CARD_MIN);
  drag(navSplit(), 200, 400);
  expect(1100 - navW() - listW()).toBeGreaterThanOrEqual(CARD_MIN);
});
