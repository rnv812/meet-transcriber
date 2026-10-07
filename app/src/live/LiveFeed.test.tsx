import { act, fireEvent, render, screen } from "@testing-library/react";
import type { FeedLine } from "./useLive";
import { LABELS_HINT, LiveFeed, TARGET_MS, lineAt } from "./LiveFeed";

const line = (i: number, speaker: string | null = "Демьян"): FeedLine => ({ t: 60 + i, speaker, text: `реплика ${i}`, id: null });

/** jsdom не раскладывает: высоты ленты задаём сами, scrollTop — обычное поле. */
function fakeScroll(el: HTMLElement, { scrollHeight, clientHeight }: { scrollHeight: number; clientHeight: number }) {
  let top = 0;
  Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => scrollHeight });
  Object.defineProperty(el, "clientHeight", { configurable: true, get: () => clientHeight });
  Object.defineProperty(el, "scrollTop", { configurable: true, get: () => top, set: (v: number) => { top = v; } });
  return {
    grow(by: number) { scrollHeight += by; },
  };
}

test("строки: время от начала, спикер и текст; без спикера — только текст", () => {
  render(<LiveFeed lines={[line(0), line(1, null)]} />);
  const items = screen.getAllByRole("listitem");
  expect(items[0]).toHaveTextContent("01:00");
  expect(items[0]).toHaveTextContent("Демьян");
  expect(items[0]).toHaveTextContent("реплика 0");
  expect(items[1]).toHaveTextContent("реплика 1");
  expect(items[1]).not.toHaveTextContent("Демьян");
});

test("пустая лента — подсказка", () => {
  render(<LiveFeed lines={[]} />);
  expect(screen.getByText(/Реплики появятся/)).toBeInTheDocument();
});

test("автопрокрутка к новой строке, пока пользователь не прокрутил вверх", () => {
  const { rerender } = render(<LiveFeed lines={[line(0)]} />);
  const feed = screen.getByRole("log");
  const box = fakeScroll(feed, { scrollHeight: 500, clientHeight: 200 });
  box.grow(20);
  rerender(<LiveFeed lines={[line(0), line(1)]} />);
  expect(feed.scrollTop).toBe(520);

  // Прокрутил вверх — лента стоит на месте.
  feed.scrollTop = 100;
  fireEvent.scroll(feed);
  box.grow(20);
  rerender(<LiveFeed lines={[line(0), line(1), line(2)]} />);
  expect(feed.scrollTop).toBe(100);

  // Вернулся вниз — снова следим.
  feed.scrollTop = 540 - 200;
  fireEvent.scroll(feed);
  box.grow(20);
  rerender(<LiveFeed lines={[line(0), line(1), line(2), line(3)]} />);
  expect(feed.scrollTop).toBe(560);
});

test("строки ключуются по номеру: обрезка начала ленты не пересоздаёт остальные", () => {
  const withId = (i: number) => ({ ...line(i), id: i });
  const { rerender } = render(<LiveFeed lines={[withId(0), withId(1), withId(2)]} />);
  const kept = screen.getByText("реплика 2").closest("li");
  rerender(<LiveFeed lines={[withId(1), withId(2), withId(3)]} />);
  expect(screen.getByText("реплика 2").closest("li")).toBe(kept);
});

test("lineAt: последняя реплика, начавшаяся не позже момента", () => {
  const lines = [line(0), line(10), line(20)];
  expect(lineAt(lines, 75)).toBe(lines[1]);
  expect(lineAt(lines, 80)).toBe(lines[2]);
  expect(lineAt(lines, 5)).toBe(lines[0]);
  expect(lineAt([], 5)).toBeUndefined();
});

test("переход к моменту: реплика подсвечена, слежение за низом выключено, подсветка гаснет", () => {
  vi.useFakeTimers();
  const lines = [line(0), line(10), line(20)];
  const { rerender } = render(<LiveFeed lines={lines} />);
  const feed = screen.getByRole("log");
  const box = fakeScroll(feed, { scrollHeight: 500, clientHeight: 200 });
  rerender(<LiveFeed lines={lines} focus={{ t: 71, seq: 1 }} />);
  expect(screen.getByText("реплика 10").closest("li")).toHaveClass("is-target");
  const top = feed.scrollTop;
  box.grow(20);
  rerender(<LiveFeed lines={[...lines, line(30)]} focus={{ t: 71, seq: 1 }} />);
  expect(feed.scrollTop).toBe(top); // человек читает старое — не утаскиваем вниз
  act(() => { vi.advanceTimersByTime(TARGET_MS); });
  expect(screen.getByText("реплика 10").closest("li")).not.toHaveClass("is-target");
  vi.useRealTimers();
});

test("ленту сузили или сделали ниже (разделитель, окно) — она остаётся внизу; прокрученная вверх — стоит", () => {
  const observers: (() => void)[] = [];
  vi.stubGlobal("ResizeObserver", class {
    constructor(cb: () => void) { observers.push(cb); }
    observe() {}
    disconnect() {}
  });
  try {
    render(<LiveFeed lines={[line(0), line(1)]} />);
    const feed = screen.getByRole("log");
    const box = fakeScroll(feed, { scrollHeight: 500, clientHeight: 200 });
    feed.scrollTop = 300;
    fireEvent.scroll(feed);
    // Уже — строки переносятся, лента выросла, а новых строк нет.
    box.grow(180);
    act(() => observers.forEach((cb) => cb()));
    expect(feed.scrollTop).toBe(680);

    feed.scrollTop = 100;
    fireEvent.scroll(feed);
    box.grow(40);
    act(() => observers.forEach((cb) => cb()));
    expect(feed.scrollTop).toBe(100);
  } finally {
    vi.unstubAllGlobals();
  }
});

test("нумерованные подписи собеседников — с подсказкой, что они предварительные", () => {
  const { rerender } = render(<LiveFeed lines={[line(0, "Собеседник"), line(1)]} />);
  expect(screen.queryByText(LABELS_HINT)).toBeNull();
  rerender(<LiveFeed lines={[line(0, "Собеседник 1"), line(1, "Собеседник 2"), line(2)]} />);
  expect(screen.getByText(LABELS_HINT)).toBeInTheDocument();
  expect(screen.getByText("Собеседник 2")).toHaveAttribute("title", LABELS_HINT);
  expect(screen.getByText("Демьян")).not.toHaveAttribute("title");
  expect(screen.getAllByRole("listitem")).toHaveLength(3); // подсказка — не строка ленты
});
