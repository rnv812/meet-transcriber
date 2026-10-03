import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CLOSE_DELAY_MS, HelpTip, TipLine, placeTip } from "./HelpTip";

const tip = () => (
  <div>
    <HelpTip label="Что такое база голосов" title="База голосов">
      <TipLine>Образцы голосов людей, которых вы назвали.</TipLine>
    </HelpTip>
    <button type="button">Снаружи</button>
  </div>
);

test("закрыта по умолчанию: только кнопка «?» с доступным именем", () => {
  render(tip());
  const button = screen.getByRole("button", { name: "Что такое база голосов" });
  expect(button).toHaveAttribute("aria-expanded", "false");
  expect(button).not.toHaveAttribute("aria-describedby");
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("фокус открывает подсказку и связывает её через aria-describedby; Esc закрывает", async () => {
  render(tip());
  await userEvent.tab();
  const button = screen.getByRole("button", { name: "Что такое база голосов" });
  expect(button).toHaveFocus();
  const tooltip = screen.getByRole("tooltip");
  expect(tooltip).toHaveTextContent("База голосов");
  expect(tooltip).toHaveTextContent("Образцы голосов");
  expect(button).toHaveAttribute("aria-describedby", tooltip.id);
  expect(button).toHaveAccessibleDescription(/Образцы голосов/);
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("нажатие закрепляет подсказку, клик снаружи закрывает", async () => {
  render(tip());
  await userEvent.click(screen.getByRole("button", { name: "Что такое база голосов" }));
  expect(screen.getByRole("tooltip")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Снаружи" }));
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("наведение открывает, уход мыши закрывает с паузой", () => {
  vi.useFakeTimers();
  try {
    render(tip());
    const root = screen.getByRole("button", { name: "Что такое база голосов" }).parentElement!;
    fireEvent.mouseEnter(root);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    fireEvent.mouseLeave(root);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(CLOSE_DELAY_MS + 10); });
    expect(screen.queryByRole("tooltip")).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("Esc закрывает и подсказку, открытую наведением", () => {
  render(tip());
  fireEvent.mouseEnter(screen.getByRole("button", { name: "Что такое база голосов" }).parentElement!);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("Esc закрывает подсказку и не доходит до окна вокруг неё", () => {
  const outer = vi.fn();
  document.addEventListener("keydown", outer);
  try {
    render(tip());
    fireEvent.click(screen.getByRole("button", { name: "Что такое база голосов" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(outer).not.toHaveBeenCalled();
    fireEvent.keyDown(document, { key: "Escape" }); // подсказки нет — Esc снова общий
    expect(outer).toHaveBeenCalledTimes(1);
  } finally {
    document.removeEventListener("keydown", outer);
  }
});

test("прокрутка закрывает подсказку, только если прокручен её контейнер", () => {
  render(
    <div>
      <div data-testid="scroller">{tip()}</div>
      <div data-testid="other" />
    </div>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Что такое база голосов" }));
  fireEvent.scroll(screen.getByTestId("other"));
  expect(screen.getByRole("tooltip")).toBeInTheDocument();
  fireEvent.scroll(screen.getByRole("tooltip"));
  expect(screen.getByRole("tooltip")).toBeInTheDocument();
  fireEvent.scroll(screen.getByTestId("scroller"));
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("изменение размера окна пересчитывает положение", () => {
  render(tip());
  const button = screen.getByRole("button", { name: "Что такое база голосов" });
  fireEvent.click(button);
  const tooltip = screen.getByRole("tooltip");
  button.getBoundingClientRect = () => ({ left: 500, right: 518, top: 40, bottom: 58 }) as DOMRect;
  act(() => { window.dispatchEvent(new Event("resize")); });
  expect(tooltip.style.left).toBe("500px");
  expect(tooltip.style.top).toBe("64px");
});

describe("placeTip", () => {
  const view = { width: 1000, height: 700 };
  const size = { width: 300, height: 120 };

  test("под кнопкой, левым краем к ней", () => {
    expect(placeTip({ left: 400, right: 420, top: 100, bottom: 120 }, size, view)).toEqual({ left: 400, top: 126 });
  });

  test("у правого края окна раскрывается влево — правым краем к кнопке", () => {
    expect(placeTip({ left: 900, right: 920, top: 100, bottom: 120 }, size, view).left).toBe(920 - 300);
  });

  test("не влезает ни снизу, ни сверху — прижата к краю окна, а не уходит за него", () => {
    const tall = { width: 300, height: 600 };
    const { top } = placeTip({ left: 400, right: 420, top: 300, bottom: 320 }, tall, view);
    expect(top).toBe(700 - 600 - 8);
  });

  test("у нижнего края — над кнопкой", () => {
    expect(placeTip({ left: 600, right: 620, top: 640, bottom: 660 }, size, view).top).toBe(640 - 6 - 120);
  });
});
