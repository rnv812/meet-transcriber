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
