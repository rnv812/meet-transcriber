import { act, fireEvent, render, screen } from "@testing-library/react";
import { Trash2, X } from "lucide-react";
import { Button } from "./Button";
import { IconButton } from "./IconButton";
import { Truncate } from "./Truncate";
import { Disclosure } from "./Disclosure";
import { Loading, StatusSlot } from "./Loading";
import { TIP_DELAY_MS } from "./Tip";

test("варианты Button — классы Atlas Aurora", () => {
  const { rerender } = render(<Button>Обычная</Button>);
  const b = () => screen.getByRole("button");
  expect(b()).toHaveClass("btn", "btn--outline", "btn--sm");
  rerender(<Button variant="primary">Главная</Button>);
  expect(b()).toHaveClass("btn--primary");
  rerender(<Button variant="primary" flat>Главная</Button>);
  expect(b()).toHaveClass("btn--flat");
  rerender(<Button variant="aurora">ИИ</Button>);
  expect(b()).toHaveClass("btn--aurora");
  rerender(<Button variant="ghost" size="md">Призрак</Button>);
  expect(b()).toHaveClass("btn--ghost");
  expect(b()).not.toHaveClass("btn--sm");
  rerender(<Button size="lg">Крупная</Button>);
  expect(b()).toHaveClass("btn--lg");
  rerender(<Button variant="link">Ссылка</Button>);
  expect(b()).toHaveClass("btn--link");
});

test("размер xs — 28 px через плотный режим Aurora", () => {
  render(<Button size="xs">Мелкая</Button>);
  const b = screen.getByRole("button");
  expect(b).toHaveClass("btn--sm");
  expect(b).toHaveAttribute("data-density", "compact");
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

test("кнопка-значок: имя, подсказка, нажатая, плотная", () => {
  const { rerender } = render(<IconButton icon={X} label="Закрыть" />);
  const b = () => screen.getByRole("button", { name: "Закрыть" });
  expect(b()).toHaveClass("btn", "btn--icon", "btn--sm", "btn--ghost");
  // Подсказка — облачко Aurora (ui/Tip), не системный title; имя не повторяется описанием.
  expect(b()).not.toHaveAttribute("title");
  expect(b()).not.toHaveAccessibleDescription();
  rerender(<IconButton icon={X} label="Закрыть" pressed />);
  expect(b()).toHaveAttribute("aria-pressed", "true");
  rerender(<IconButton icon={X} label="Закрыть" size="xs" variant="danger" />);
  expect(b()).toHaveAttribute("data-density", "compact");
  expect(b()).toHaveClass("btn--ghost-danger");
});

test("кнопка-значок md — 40 px рядом с полем md: без класса малой кнопки, значок 16", () => {
  render(<IconButton icon={X} label="Фильтры" size="md" variant="secondary" />);
  const b = screen.getByRole("button", { name: "Фильтры" });
  expect(b).toHaveClass("btn", "btn--icon", "btn--outline");
  expect(b).not.toHaveClass("btn--sm");
  expect(b).not.toHaveAttribute("data-density");
  expect(b.querySelector("svg")).toHaveAttribute("width", "16");
});

test("недоступная кнопка-значок сохраняет подсказку с причиной", () => {
  render(<IconButton icon={X} label="Закрыть" tooltip="Причина" disabled />);
  const b = screen.getByRole("button", { name: "Закрыть" });
  expect(b).toBeDisabled();
  expect(b).not.toHaveAttribute("title");
  expect(b).toHaveAccessibleDescription("Причина");
  vi.useFakeTimers();
  try {
    fireEvent.mouseEnter(b);
    act(() => { vi.advanceTimersByTime(TIP_DELAY_MS); });
    expect(document.body.querySelector(".tooltip.tip")).toHaveTextContent("Причина");
  } finally {
    vi.useRealTimers();
  }
});

test("icon button: label is the accessible name and the tooltip", () => {
  render(<IconButton icon={Trash2} label="Удалить категорию" variant="danger" size="xs" />);
  const button = screen.getByRole("button", { name: "Удалить категорию" });
  expect(button).not.toHaveAttribute("title");
  vi.useFakeTimers();
  try {
    fireEvent.mouseEnter(button);
    act(() => { vi.advanceTimersByTime(TIP_DELAY_MS); });
    expect(document.body.querySelector(".tooltip.tip")).toHaveTextContent("Удалить категорию");
  } finally {
    vi.useRealTimers();
  }
  expect(button).toHaveClass("btn", "btn--icon", "btn--ghost-danger");
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
