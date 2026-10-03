import { act, render, renderHook, screen } from "@testing-library/react";
import { ProgressBar, useSmoothProgress } from "./ProgressBar";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame", "setTimeout", "performance"] });
});
afterEach(() => {
  vi.useRealTimers();
});

const advance = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });

test("shows the first value at once, then eases toward a new one", () => {
  const { result, rerender } = renderHook(({ v }) => useSmoothProgress(v, "asr"), { initialProps: { v: 0.2 as number | null } });
  expect(result.current).toBe(0.2);
  rerender({ v: 0.6 });
  advance(50);
  const mid = result.current!;
  expect(mid).toBeGreaterThan(0.2);
  expect(mid).toBeLessThan(0.6);
  advance(5000);
  expect(result.current).toBe(0.6);
});

test("is monotonic within a stage: a lower value does not move it back", () => {
  const { result, rerender } = renderHook(({ v }) => useSmoothProgress(v, "asr"), { initialProps: { v: 0.5 as number | null } });
  rerender({ v: 0.3 });
  advance(2000);
  expect(result.current).toBe(0.5);
  rerender({ v: 0.7 });
  advance(5000);
  expect(result.current).toBe(0.7);
});

test("a new stage restarts from zero", () => {
  const { result, rerender } = renderHook(({ v, k }) => useSmoothProgress(v, k),
    { initialProps: { v: 0.9 as number | null, k: "asr" } });
  rerender({ v: 0, k: "align" });
  expect(result.current).toBe(0);
  rerender({ v: 0.4, k: "align" });
  advance(5000);
  expect(result.current).toBe(0.4);
});

test("unknown value is indeterminate, never a full bar", () => {
  render(<ProgressBar value={null} label="Этап 3 из 5 · Спикеры" />);
  const bar = screen.getByRole("progressbar", { name: "Этап 3 из 5 · Спикеры" });
  expect(bar).toHaveClass("progressbar__track--indeterminate");
  expect(bar).not.toHaveAttribute("aria-valuenow");
  expect(document.querySelector(".progressbar__fill")).toBeNull();
});

test("unknown after known within the same stage keeps the reached value", () => {
  const { result, rerender } = renderHook(({ v }) => useSmoothProgress(v, "model"), { initialProps: { v: 0.4 as number | null } });
  rerender({ v: null });
  expect(result.current).toBe(0.4);
});

test("going from unknown to known eases up from zero", () => {
  const { result, rerender } = renderHook(({ v }) => useSmoothProgress(v, "x"), { initialProps: { v: null as number | null } });
  expect(result.current).toBeNull();
  rerender({ v: 0.5 });
  advance(16);
  expect(result.current).toBeGreaterThan(0);
  expect(result.current).toBeLessThan(0.5);
  advance(5000);
  expect(result.current).toBe(0.5);
});

test("renders label, detail and the eased width", () => {
  render(<ProgressBar value={0.25} label="Этап 2 из 5 · Распознавание" detail="25 % · осталось ~4 мин" />);
  expect(screen.getByText("Этап 2 из 5 · Распознавание")).toBeInTheDocument();
  expect(screen.getByText("25 % · осталось ~4 мин")).toBeInTheDocument();
  const bar = screen.getByRole("progressbar");
  expect(bar).toHaveAttribute("aria-valuenow", "25");
  expect(document.querySelector<HTMLElement>(".progressbar__fill")!.style.width).toBe("25%");
});

// --- 0.3.1: продление между событиями ----------------------------------------

test("between updates the bar keeps moving at the recent pace, monotonic and capped", () => {
  const { result, rerender } = renderHook(({ v }) => useSmoothProgress(v, "job", { extrapolate: true, cap: 0.5 }),
    { initialProps: { v: 0.1 as number | null } });
  advance(10_000);
  rerender({ v: 0.2 }); // 1 % в секунду
  advance(3000);
  const a = result.current!;
  advance(3000);
  const b = result.current!;
  expect(a).toBeGreaterThan(0.2);
  expect(b).toBeGreaterThan(a); // события нет, а полоска идёт
  advance(60_000);
  const c = result.current!;
  expect(c).toBeLessThanOrEqual(0.5); // не дальше следующей отметки
  expect(c).toBeLessThan(0.2 + 0.01 * 16); // и не дольше пары промежутков без новостей
  rerender({ v: 0.25 }); // резидент сказал меньше, чем показано, — назад не идём
  advance(2000);
  expect(result.current!).toBeGreaterThanOrEqual(c);
});

test("extrapolation never reaches a full bar on its own", () => {
  const { result, rerender } = renderHook(({ v }) => useSmoothProgress(v, "job", { extrapolate: true, cap: 1 }),
    { initialProps: { v: 0.9 as number | null } });
  advance(1000);
  rerender({ v: 0.99 });
  advance(60_000);
  expect(result.current!).toBeLessThan(1);
  rerender({ v: 1 });
  advance(5000);
  expect(result.current).toBe(1);
});

test("a stage change restarts cleanly without carrying the old pace", () => {
  const { result, rerender } = renderHook(({ v, k }) => useSmoothProgress(v, k, { extrapolate: true, cap: 1 }),
    { initialProps: { v: 0.2 as number | null, k: "a" } });
  advance(1000);
  rerender({ v: 0.6, k: "a" });
  rerender({ v: 0.1, k: "b" });
  expect(result.current).toBe(0);
  advance(10_000);
  expect(result.current).toBe(0.1); // одно значение нового этапа — темпа ещё нет
});

test("without extrapolation the bar stops at the server value", () => {
  const { result, rerender } = renderHook(({ v }) => useSmoothProgress(v, "job"), { initialProps: { v: 0.1 as number | null } });
  advance(10_000);
  rerender({ v: 0.2 });
  advance(10_000);
  expect(result.current).toBe(0.2);
});

test("reduced motion: exact server values, no easing and no extrapolation", () => {
  const listeners: Array<() => void> = [];
  const original = window.matchMedia;
  window.matchMedia = ((query: string) => ({
    matches: query.includes("reduce"), media: query, onchange: null,
    addEventListener: (_: string, fn: () => void) => listeners.push(fn), removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  try {
    const { result, rerender } = renderHook(({ v }) => useSmoothProgress(v, "job", { extrapolate: true, cap: 1 }),
      { initialProps: { v: 0.1 as number | null } });
    advance(10_000);
    rerender({ v: 0.2 });
    expect(result.current).toBe(0.2); // сразу, без анимации
    advance(10_000);
    expect(result.current).toBe(0.2); // и без продления
  } finally {
    window.matchMedia = original;
  }
});

test("detail as a function shows the same percent as the bar", () => {
  render(<ProgressBar value={0.42} detail={(shown) => `${Math.floor((shown ?? 0) * 100)} %`} label="Ход" />);
  expect(screen.getByText("42 %")).toBeInTheDocument();
  expect(document.querySelector<HTMLElement>(".progressbar__fill")!.style.width).toBe("42%");
});
