import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { PaneResizer } from "./PaneResizer";

const SPEC = { def: 200, min: 140, max: 360, reserve: 420 };

function Area({ open = true }: { open?: boolean }) {
  return (
    <div className="area" data-testid="area">
      <nav />
      {open && <PaneResizer name="test-menu" cssVar="--menu-w" spec={SPEC} panel="before" label="Ширина меню" />}
      <div />
    </div>
  );
}

const area = () => screen.getByTestId("area");
const roomOf = (w: number) => vi.spyOn(HTMLElement.prototype, "clientWidth", "get")
  .mockImplementation(function (this: HTMLElement) { return this.classList.contains("area") ? w : 0; });

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

test("ширина ставится на область при первой же отрисовке вместе с ней; панель закрыли — переменная снята", () => {
  roomOf(1000);
  const { rerender } = render(<Area />);
  expect(area().style.getPropertyValue("--menu-w")).toBe("200px");
  rerender(<Area open={false} />);
  expect(area().style.getPropertyValue("--menu-w")).toBe("");
});

test("запомненная ширина восстанавливается, но не шире, чем оставляет соседу область", () => {
  localStorage.setItem("meet.pane.test-menu", "340");
  roomOf(700);
  render(<Area />);
  // 700 − 420 на текст настроек = 280.
  expect(area().style.getPropertyValue("--menu-w")).toBe("280px");
  const split = screen.getByRole("separator", { name: "Ширина меню" });
  expect(split).toHaveAttribute("aria-valuemax", "280");
  fireEvent.keyDown(split, { key: "Home" });
  expect(area().style.getPropertyValue("--menu-w")).toBe("140px");
  expect(localStorage.getItem("meet.pane.test-menu")).toBe("140");
  fireEvent.doubleClick(split);
  expect(area().style.getPropertyValue("--menu-w")).toBe("200px");
  expect(localStorage.getItem("meet.pane.test-menu")).toBeNull();
});

test("без хранилища всё работает: ширина по умолчанию, перетаскивание меняет её до перезапуска", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
  roomOf(1000);
  render(<Area />);
  expect(area().style.getPropertyValue("--menu-w")).toBe("200px");
  const split = screen.getByRole("separator", { name: "Ширина меню" });
  fireEvent.keyDown(split, { key: "ArrowRight" });
  expect(area().style.getPropertyValue("--menu-w")).toBe("216px");
});

/** Панели одна над другой: нижняя — по CSS (доля области), пока её не потянули. */
const FLUID = { min: 64, max: 600, reserve: 100 };

function Stack({ show = true, inside = false }: { show?: boolean; inside?: boolean }) {
  const resizer = show && (
    <PaneResizer name="test-low" cssVar="--low" spec={FLUID} panel="after" axis="y" label="Высота сводки"
      cssValue={(h) => `0 1 ${h}px`}
      {...(inside ? { area: (h: HTMLElement) => h.parentElement?.parentElement ?? null, pane: (h: HTMLElement) => h.parentElement } : {})} />
  );
  return (
    <div className="stack" data-testid="stack">
      <section />
      {!inside && resizer}
      <section className="low">{inside && resizer}</section>
    </div>
  );
}

const stack = () => screen.getByTestId("stack");
/** Высота области `room`, нижней панели — `low` (по CSS, до перетаскивания). */
function heights(room: number, low: number) {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get")
    .mockImplementation(function (this: HTMLElement) { return this.classList.contains("stack") ? room : 0; });
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get")
    .mockImplementation(function (this: HTMLElement) { return this.classList.contains("low") ? low : 0; });
}

test("по высоте без размера по умолчанию: пока не тянули — CSS области, разделитель показывает высоту панели", () => {
  heights(500, 180);
  render(<Stack />);
  expect(stack().style.getPropertyValue("--low")).toBe("");
  const split = screen.getByRole("separator", { name: "Высота сводки" });
  expect(split).toHaveAttribute("aria-orientation", "horizontal");
  expect(split).toHaveAttribute("aria-valuenow", "180");
  expect(split).toHaveAttribute("aria-valuemin", "64");
  // 500 − 100 оставить соседу.
  expect(split).toHaveAttribute("aria-valuemax", "400");
});

test("по высоте: клавиша меняет и запоминает высоту в своём формате; двойной щелчок — снова по CSS", () => {
  heights(500, 180);
  render(<Stack />);
  const split = screen.getByRole("separator", { name: "Высота сводки" });
  fireEvent.keyDown(split, { key: "ArrowUp" });
  expect(stack().style.getPropertyValue("--low")).toBe("0 1 196px");
  expect(localStorage.getItem("meet.pane.test-low")).toBe("196");
  expect(split).toHaveAttribute("aria-valuenow", "196");
  fireEvent.doubleClick(split);
  expect(stack().style.getPropertyValue("--low")).toBe("");
  expect(localStorage.getItem("meet.pane.test-low")).toBeNull();
  expect(split).toHaveAttribute("aria-valuenow", "180");
});

test("по высоте: перетаскивание мышью — в пределах области; после перезапуска высота та же", async () => {
  heights(500, 180);
  const { unmount } = render(<Stack />);
  const split = screen.getByRole("separator", { name: "Высота сводки" });
  fireEvent.pointerDown(split, { button: 0, clientY: 300, pointerId: 1 });
  fireEvent.pointerMove(split, { clientY: 0, pointerId: 1 }); // выше предела
  await new Promise((r) => requestAnimationFrame(() => r(null)));
  expect(stack().style.getPropertyValue("--low")).toBe("0 1 400px");
  fireEvent.pointerUp(split, { clientY: 0, pointerId: 1 });
  expect(localStorage.getItem("meet.pane.test-low")).toBe("400");
  unmount();

  render(<Stack />);
  expect(stack().style.getPropertyValue("--low")).toBe("0 1 400px");
});

test("по высоте: запомненная высота в низком окне ужимается, но не ниже минимума и не выше области", () => {
  localStorage.setItem("meet.pane.test-low", "450");
  heights(300, 120);
  render(<Stack />);
  expect(stack().style.getPropertyValue("--low")).toBe("0 1 200px");
  const split = screen.getByRole("separator", { name: "Высота сводки" });
  // Запомненное — пожелание: окно снова выше — высота вернётся.
  expect(localStorage.getItem("meet.pane.test-low")).toBe("450");
  expect(split).toHaveAttribute("aria-valuemax", "200");
  fireEvent.keyDown(split, { key: "Home" });
  expect(stack().style.getPropertyValue("--low")).toBe("0 1 64px");
  expect(localStorage.getItem("meet.pane.test-low")).toBe("64");
});

test("разделитель внутри самой панели: область и панель задаются явно", () => {
  heights(500, 150);
  render(<Stack inside />);
  const split = screen.getByRole("separator", { name: "Высота сводки" });
  expect(split).toHaveAttribute("aria-valuenow", "150");
  expect(split).toHaveAttribute("aria-valuemax", "400");
  fireEvent.keyDown(split, { key: "ArrowDown" });
  expect(stack().style.getPropertyValue("--low")).toBe("0 1 134px");
});
