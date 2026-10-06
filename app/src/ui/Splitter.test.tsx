import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { NAV } from "../lib/panes";
import { Splitter } from "./Splitter";

function setup(props: Partial<Parameters<typeof Splitter>[0]> = {}) {
  const onPreview = vi.fn();
  const onCommit = vi.fn();
  const onReset = vi.fn();
  let renders = 0;
  function Probe() {
    renders += 1;
    return (
      <Splitter label="Ширина списка" value={300} min={260} max={560} panel="before"
        onPreview={onPreview} onCommit={onCommit} onReset={onReset} {...props} />
    );
  }
  render(<Probe />);
  return { el: screen.getByRole("separator", { name: "Ширина списка" }), onPreview, onCommit, onReset, renders: () => renders };
}

const down = (el: HTMLElement, x: number) => fireEvent.pointerDown(el, { button: 0, clientX: x, pointerId: 1 });
const move = (el: HTMLElement, x: number) => fireEvent.pointerMove(el, { clientX: x, pointerId: 1 });
const up = (el: HTMLElement, x: number) => fireEvent.pointerUp(el, { clientX: x, pointerId: 1 });

test("разделитель доступен с клавиатуры: роль, ориентация, значение и пределы", () => {
  const { el } = setup();
  expect(el).toHaveAttribute("aria-orientation", "vertical");
  expect(el).toHaveAttribute("aria-valuenow", "300");
  expect(el).toHaveAttribute("aria-valuemin", "260");
  expect(el).toHaveAttribute("aria-valuemax", "560");
  expect(el).toHaveAttribute("tabindex", "0");
});

test("перетаскивание меняет ширину в пределах, без перерисовки; отпустили — одна фиксация", async () => {
  const { el, onPreview, onCommit, renders } = setup();
  const before = renders();
  down(el, 500);
  expect(document.documentElement).toHaveClass("is-resizing");
  move(el, 520);
  move(el, 540);
  // Ширина меняется не чаще кадра: два движения — один показ.
  await new Promise((r) => requestAnimationFrame(() => r(null)));
  expect(onPreview).toHaveBeenCalledTimes(1);
  expect(onPreview).toHaveBeenLastCalledWith(340);
  expect(el).toHaveAttribute("aria-valuenow", "340");
  expect(onCommit).not.toHaveBeenCalled();
  move(el, 2000); // дальше предела
  up(el, 2000);
  expect(onPreview).toHaveBeenLastCalledWith(560);
  expect(onCommit).toHaveBeenCalledTimes(1);
  expect(onCommit).toHaveBeenCalledWith(560);
  expect(el).toHaveAttribute("aria-valuenow", "560");
  expect(renders()).toBe(before);
  expect(document.documentElement).not.toHaveClass("is-resizing");

  onCommit.mockClear();
  down(el, 500);
  move(el, 0);
  up(el, 0);
  expect(onCommit).toHaveBeenCalledWith(260);
});

test("панель справа от разделителя: тянут влево — шире", () => {
  const { el, onCommit } = setup({ panel: "after" });
  down(el, 500);
  move(el, 440);
  up(el, 440);
  expect(onCommit).toHaveBeenCalledWith(360);
});

test("щелчок без движения ничего не меняет; двойной щелчок — ширина по умолчанию", () => {
  const { el, onCommit, onReset } = setup();
  down(el, 500);
  up(el, 500);
  expect(onCommit).not.toHaveBeenCalled();
  fireEvent.doubleClick(el);
  expect(onReset).toHaveBeenCalledTimes(1);
});

test("клавиши: ←/→ по 16 px, Home/End — к пределам; у панели справа стрелки наоборот", () => {
  const { el, onCommit } = setup();
  fireEvent.keyDown(el, { key: "ArrowRight" });
  expect(onCommit).toHaveBeenLastCalledWith(316);
  fireEvent.keyDown(el, { key: "ArrowLeft" });
  expect(onCommit).toHaveBeenLastCalledWith(284);
  fireEvent.keyDown(el, { key: "Home" });
  expect(onCommit).toHaveBeenLastCalledWith(260);
  fireEvent.keyDown(el, { key: "End" });
  expect(onCommit).toHaveBeenLastCalledWith(560);
});

test("клавиши у панели справа: ← — шире", () => {
  const { el, onCommit } = setup({ panel: "after" });
  fireEvent.keyDown(el, { key: "ArrowLeft" });
  expect(onCommit).toHaveBeenLastCalledWith(316);
});

test("навигация: уже порога — полоса значков; из полосы стрелкой — снова минимум", () => {
  const snap = { below: NAV.snap, to: NAV.rail };
  const { el, onCommit } = setup({ value: 200, min: NAV.min, max: NAV.max, snap });
  expect(el).toHaveAttribute("aria-valuemin", String(NAV.rail));
  down(el, 200);
  move(el, 60);
  up(el, 60);
  expect(onCommit).toHaveBeenLastCalledWith(NAV.rail);
  fireEvent.keyDown(el, { key: "Home" });
  expect(onCommit).toHaveBeenLastCalledWith(NAV.rail);
});

const downY = (el: HTMLElement, y: number) => fireEvent.pointerDown(el, { button: 0, clientY: y, pointerId: 1 });
const moveY = (el: HTMLElement, y: number) => fireEvent.pointerMove(el, { clientY: y, pointerId: 1 });
const upY = (el: HTMLElement, y: number) => fireEvent.pointerUp(el, { clientY: y, pointerId: 1 });

test("разделитель по высоте: ориентация горизонтальная, тянут по вертикали, курсор «↕» во всём окне", () => {
  const { el, onCommit } = setup({ axis: "y", panel: "after", value: 200, min: 64, max: 400 });
  expect(el).toHaveAttribute("aria-orientation", "horizontal");
  expect(el).toHaveClass("splitter--y");
  downY(el, 300);
  expect(document.documentElement).toHaveClass("is-resizing", "is-resizing--y");
  // Панель снизу: тянут вверх — выше.
  moveY(el, 250);
  upY(el, 250);
  expect(onCommit).toHaveBeenLastCalledWith(250);
  expect(document.documentElement).not.toHaveClass("is-resizing--y");
  // Горизонтальное движение высоту не меняет.
  onCommit.mockClear();
  fireEvent.pointerDown(el, { button: 0, clientX: 0, clientY: 300, pointerId: 1 });
  fireEvent.pointerMove(el, { clientX: 400, clientY: 300, pointerId: 1 });
  fireEvent.pointerUp(el, { clientX: 400, clientY: 300, pointerId: 1 });
  expect(onCommit).not.toHaveBeenCalled();
});

test("разделитель по высоте: ↑/↓ по 16 px, у панели сверху ↓ — выше; пределы держатся", () => {
  const below = setup({ axis: "y", panel: "after", value: 200, min: 64, max: 400 });
  fireEvent.keyDown(below.el, { key: "ArrowUp" });
  expect(below.onCommit).toHaveBeenLastCalledWith(216);
  fireEvent.keyDown(below.el, { key: "ArrowDown" });
  expect(below.onCommit).toHaveBeenLastCalledWith(184);
  // ←/→ у разделителя по высоте ничего не делают.
  below.onCommit.mockClear();
  fireEvent.keyDown(below.el, { key: "ArrowRight" });
  expect(below.onCommit).not.toHaveBeenCalled();
});

test("разделитель по высоте, панель сверху: тянут вниз — выше, не выше предела", () => {
  const { el, onCommit } = setup({ axis: "y", panel: "before", value: 200, min: 64, max: 400 });
  fireEvent.keyDown(el, { key: "ArrowDown" });
  expect(onCommit).toHaveBeenLastCalledWith(216);
  downY(el, 100);
  moveY(el, 900);
  upY(el, 900);
  expect(onCommit).toHaveBeenLastCalledWith(400);
});

test("нажатие на разделитель переводит на него фокус: клавиши работают сразу после перетаскивания", () => {
  const { el } = setup();
  down(el, 500);
  expect(el).toHaveFocus();
  up(el, 500);
});

test("Enter на разделителе — как было (то же, что двойной щелчок)", () => {
  const { el, onReset, onCommit } = setup();
  fireEvent.keyDown(el, { key: "Enter" });
  expect(onReset).toHaveBeenCalledTimes(1);
  expect(onCommit).not.toHaveBeenCalled();
});
