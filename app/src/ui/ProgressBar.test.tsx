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
