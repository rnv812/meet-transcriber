import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TIP_DELAY_MS, Tip } from "./Tip";

afterEach(() => { vi.useRealTimers(); });

const tipEl = () => document.body.querySelector<HTMLElement>(".tooltip.tip");

test("наведение показывает подсказку с задержкой, уход — прячет", () => {
  vi.useFakeTimers();
  render(<Tip content="Открыть папку"><button type="button">Папка</button></Tip>);
  const button = screen.getByRole("button", { name: "Папка" });
  fireEvent.mouseEnter(button);
  expect(tipEl()).toBeNull();
  act(() => { vi.advanceTimersByTime(TIP_DELAY_MS - 50); });
  expect(tipEl()).toBeNull();
  act(() => { vi.advanceTimersByTime(60); });
  expect(tipEl()).toHaveTextContent("Открыть папку");
  expect(tipEl()).toHaveClass("is-open");
  fireEvent.mouseLeave(button);
  expect(tipEl()).toBeNull();
});

test("подсказка — порталом в body, без системного title, и для диктора — описание", () => {
  vi.useFakeTimers();
  render(<div className="host"><Tip content="Открыть папку"><button type="button">Папка</button></Tip></div>);
  const button = screen.getByRole("button", { name: "Папка" });
  expect(button).not.toHaveAttribute("title");
  expect(button).toHaveAccessibleDescription("Открыть папку");
  fireEvent.mouseEnter(button);
  act(() => { vi.advanceTimersByTime(TIP_DELAY_MS); });
  const tip = tipEl()!;
  expect(tip.parentElement).toBe(document.body);
  expect(document.querySelector(".host")!.contains(tip)).toBe(false);
  // Облачко повторяет описание — диктору его не читать дважды.
  expect(tip).toHaveAttribute("aria-hidden", "true");
});

test("describe={false}: подсказка повторяет имя — описания нет", () => {
  render(<Tip content="Папка" describe={false}><button type="button">Папка</button></Tip>);
  expect(screen.getByRole("button", { name: "Папка" })).not.toHaveAccessibleDescription();
});

test("фокус с клавиатуры показывает сразу, уход фокуса прячет", async () => {
  render(<><Tip content="Открыть папку"><button type="button">Папка</button></Tip><button type="button">Дальше</button></>);
  await userEvent.tab();
  expect(tipEl()).toHaveTextContent("Открыть папку");
  await userEvent.tab();
  expect(tipEl()).toBeNull();
});

test("фокус от нажатия мышью подсказку не показывает", async () => {
  render(<Tip content="Открыть папку"><button type="button">Папка</button></Tip>);
  await userEvent.click(screen.getByRole("button", { name: "Папка" }));
  expect(screen.getByRole("button", { name: "Папка" })).toHaveFocus();
  expect(tipEl()).toBeNull();
});

test("Esc прячет подсказку и не перехватывает клавишу у окна вокруг", async () => {
  const outer = vi.fn();
  window.addEventListener("keydown", outer);
  try {
    render(<Tip content="Открыть папку"><button type="button">Папка</button></Tip>);
    await userEvent.tab();
    expect(tipEl()).not.toBeNull();
    await userEvent.keyboard("{Escape}");
    expect(tipEl()).toBeNull();
    expect(outer).toHaveBeenCalled();
  } finally {
    window.removeEventListener("keydown", outer);
  }
});

test("нажатие прячет подсказку и отменяет ожидание; снова — только после ухода мыши", () => {
  vi.useFakeTimers();
  render(<Tip content="Открыть папку"><button type="button">Папка</button></Tip>);
  const button = screen.getByRole("button", { name: "Папка" });
  fireEvent.mouseEnter(button);
  fireEvent.pointerDown(button);
  act(() => { vi.advanceTimersByTime(TIP_DELAY_MS * 2); });
  expect(tipEl()).toBeNull();
  fireEvent.mouseEnter(button);
  act(() => { vi.advanceTimersByTime(TIP_DELAY_MS * 2); });
  expect(tipEl()).toBeNull();
  fireEvent.mouseLeave(button);
  fireEvent.mouseEnter(button);
  act(() => { vi.advanceTimersByTime(TIP_DELAY_MS); });
  expect(tipEl()).not.toBeNull();
});

test("недоступная кнопка тоже показывает подсказку (причину) при наведении", () => {
  vi.useFakeTimers();
  render(<Tip content="Дождитесь окончания установки"><button type="button" disabled>Пропустить</button></Tip>);
  const button = screen.getByRole("button", { name: "Пропустить" });
  expect(button).toHaveAccessibleDescription("Дождитесь окончания установки");
  fireEvent.mouseEnter(button);
  act(() => { vi.advanceTimersByTime(TIP_DELAY_MS); });
  expect(tipEl()).toHaveTextContent("Дождитесь окончания установки");
});

test("сторона: сверху по умолчанию, справа — по запросу (класс для отступа облачка)", () => {
  vi.useFakeTimers();
  const { rerender } = render(<Tip content="Подсказка"><button type="button">К</button></Tip>);
  const button = screen.getByRole("button", { name: "К" });
  // jsdom не раскладывает: кнопка посреди окна, места хватает со всех сторон.
  vi.spyOn(button, "getBoundingClientRect").mockReturnValue(
    { left: 300, right: 340, top: 300, bottom: 332, width: 40, height: 32, x: 300, y: 300, toJSON: () => ({}) });
  fireEvent.mouseEnter(button);
  act(() => { vi.advanceTimersByTime(TIP_DELAY_MS); });
  expect(tipEl()).toHaveAttribute("data-side", "top");
  rerender(<Tip content="Подсказка" side="right"><button type="button">К</button></Tip>);
  expect(tipEl()).toHaveAttribute("data-side", "right");
});

test("пустая подсказка — ребёнок как есть, без обработчиков и описания", () => {
  render(<Tip content={undefined}><button type="button">К</button></Tip>);
  const button = screen.getByRole("button", { name: "К" });
  expect(button).not.toHaveAccessibleDescription();
  fireEvent.mouseEnter(button);
  expect(tipEl()).toBeNull();
});

test("свой ref ребёнка сохраняется", () => {
  const ref = { current: null as HTMLButtonElement | null };
  render(<Tip content="Подсказка"><button type="button" ref={ref}>К</button></Tip>);
  expect(ref.current).toBe(screen.getByRole("button", { name: "К" }));
});

test("размонтирование с открытой подсказкой убирает облачко", () => {
  vi.useFakeTimers();
  const { unmount } = render(<Tip content="Подсказка"><button type="button">К</button></Tip>);
  fireEvent.mouseEnter(screen.getByRole("button", { name: "К" }));
  act(() => { vi.advanceTimersByTime(TIP_DELAY_MS); });
  expect(tipEl()).not.toBeNull();
  unmount();
  expect(tipEl()).toBeNull();
});
