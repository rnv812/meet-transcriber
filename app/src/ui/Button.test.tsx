import { act, fireEvent, render, screen } from "@testing-library/react";
import { Trash2 } from "lucide-react";
import { Button } from "./Button";
import { IconButton } from "./IconButton";
import { Truncate } from "./Truncate";
import { Disclosure } from "./Disclosure";
import { Loading, StatusSlot } from "./Loading";

test("variants and sizes map to classes; default is secondary", () => {
  const { rerender } = render(<Button>Обычная</Button>);
  expect(screen.getByRole("button")).toHaveClass("btn");
  expect(screen.getByRole("button").className).not.toMatch(/btn--/);
  rerender(<Button variant="ghost" size="sm">Тихая</Button>);
  expect(screen.getByRole("button")).toHaveClass("btn", "btn--ghost", "btn--sm");
  rerender(<Button variant="link">Ссылка</Button>);
  expect(screen.getByRole("button")).toHaveClass("btn--link");
});

test("a promise from onClick makes the button busy at once, keeping its label for width", async () => {
  let finish!: () => void;
  const onClick = vi.fn(() => new Promise<void>((r) => { finish = r; }));
  render(<Button onClick={onClick}>Проверить</Button>);
  const button = screen.getByRole("button", { name: "Проверить" });
  fireEvent.click(button);
  expect(button).toHaveAttribute("aria-busy", "true");
  expect(button).toHaveClass("btn--busy");
  expect(button.querySelector(".btn__spinner")).not.toBeNull();
  expect(button).toHaveTextContent("Проверить");
  // Повторное нажатие, пока занято, не запускает действие снова.
  fireEvent.click(button);
  expect(onClick).toHaveBeenCalledTimes(1);
  await act(async () => { finish(); });
  expect(button).not.toHaveAttribute("aria-busy");
  fireEvent.click(button);
  expect(onClick).toHaveBeenCalledTimes(2);
});

test("busy keeps focus on the button (aria-disabled, not disabled)", () => {
  render(<Button busy>Сохранить</Button>);
  const button = screen.getByRole("button");
  expect(button).not.toBeDisabled();
  expect(button).toHaveAttribute("aria-disabled", "true");
});

test("icon button: label is the accessible name and the tooltip", () => {
  render(<IconButton icon={Trash2} label="Удалить категорию" variant="danger" size="sm" />);
  const button = screen.getByRole("button", { name: "Удалить категорию" });
  expect(button).toHaveAttribute("title", "Удалить категорию");
  expect(button).toHaveClass("icon-btn", "icon-btn--danger", "icon-btn--sm");
  expect(button.querySelector("svg")).toHaveAttribute("width", "14");
});

test("truncate sets a tooltip only when the text overflows", () => {
  render(<Truncate>Очень длинное название встречи</Truncate>);
  const el = screen.getByText("Очень длинное название встречи");
  Object.defineProperty(el, "clientWidth", { value: 100, configurable: true });
  Object.defineProperty(el, "scrollWidth", { value: 100, configurable: true });
  fireEvent.mouseEnter(el);
  expect(el).not.toHaveAttribute("title");
  Object.defineProperty(el, "scrollWidth", { value: 240, configurable: true });
  fireEvent.mouseEnter(el);
  expect(el).toHaveAttribute("title", "Очень длинное название встречи");
});

test("disclosure toggles with aria-expanded", () => {
  render(<Disclosure title="Интеграции"><p>внутри</p></Disclosure>);
  const head = screen.getByRole("button", { name: "Интеграции" });
  expect(head).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("внутри")).toBeNull();
  fireEvent.click(head);
  expect(head).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("внутри")).toBeInTheDocument();
});

test("loading shows its label only after the delay", () => {
  vi.useFakeTimers();
  try {
    render(<Loading label="Загружаю модели…" />);
    expect(screen.queryByText("Загружаю модели…")).toBeNull();
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    act(() => { vi.advanceTimersByTime(350); });
    expect(screen.getByText("Загружаю модели…")).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

test("status slot is present even when empty (reserved height)", () => {
  render(<StatusSlot />);
  const slot = screen.getByRole("status");
  expect(slot).toBeEmptyDOMElement();
  expect(slot).toHaveClass("status-slot", "status-slot--1");
});
