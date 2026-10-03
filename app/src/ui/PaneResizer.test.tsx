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
