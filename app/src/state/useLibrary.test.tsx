import { act, renderHook } from "@testing-library/react";

const h = vi.hoisted(() => ({
  ApiError: class extends Error {
    constructor(public status: number, message: string) { super(message); }
  },
}));
vi.mock("../lib/api", () => ({
  ApiError: h.ApiError,
  getRecordings: vi.fn().mockResolvedValue({ root: "r", items: [] }),
  searchLibrary: vi.fn().mockResolvedValue({ items: [] }),
  getJobs: vi.fn().mockResolvedValue({ items: [] }),
}));
import { getJobs, getRecordings, searchLibrary } from "../lib/api";
import { useLibrary } from "./useLibrary";

const ep = { base: "http://h", token: "t" };
const NO_CATS: string[] = [];

test("рост libraryTick перечитывает библиотеку, в том числе скачком через несколько", async () => {
  const { rerender } = renderHook(({ tick }) => useLibrary(ep, "", tick), { initialProps: { tick: 0 } });
  await act(async () => {});
  const before = vi.mocked(getRecordings).mock.calls.length;
  rerender({ tick: 2 });
  await act(async () => {});
  expect(vi.mocked(getRecordings).mock.calls.length).toBeGreaterThan(before);
});

test("с запросом — поиск по тексту: записи приходят с фрагментами", async () => {
  const found = { id: "a", title: "Планёрка", hits: [{ t: 3, speaker: "Анна", snippet: "Бюджет", ranges: [[0, 6]] }], total: 1 };
  vi.mocked(searchLibrary).mockResolvedValueOnce({ items: [found] } as never);
  const { result } = renderHook(() => useLibrary(ep, "бюджет"));
  await vi.waitFor(() => expect(result.current.items).toEqual([found]));
  expect(searchLibrary).toHaveBeenCalledWith(ep, "бюджет", expect.any(AbortSignal));
});

test("резидент без поиска по тексту (404) — прежний поиск по записям", async () => {
  vi.mocked(searchLibrary).mockRejectedValueOnce(new h.ApiError(404, "нет"));
  vi.mocked(getRecordings).mockClear();
  renderHook(() => useLibrary(ep, "бюджет"));
  await vi.waitFor(() => expect(getRecordings).toHaveBeenCalledWith(ep, "бюджет"));
});

test("пока идёт поиск, прогресс задач обновляет только задачи; поиск — по contentTick", async () => {
  const { rerender } = renderHook(({ tick, content }) => useLibrary(ep, "бюджет", tick, content),
    { initialProps: { tick: 0, content: 0 } });
  await vi.waitFor(() => expect(searchLibrary).toHaveBeenCalled());
  vi.mocked(searchLibrary).mockClear();
  vi.mocked(getJobs).mockClear();
  rerender({ tick: 3, content: 0 });
  await vi.waitFor(() => expect(getJobs).toHaveBeenCalledTimes(1));
  expect(searchLibrary).not.toHaveBeenCalled();
  rerender({ tick: 4, content: 1 });
  await vi.waitFor(() => expect(searchLibrary).toHaveBeenCalledTimes(1));
});

test("новый запрос отменяет прежний, ещё не ответивший", async () => {
  const signals: AbortSignal[] = [];
  vi.mocked(searchLibrary).mockImplementation(async (_ep, _q, signal) => {
    signals.push(signal!);
    return { items: [] };
  });
  const { rerender } = renderHook(({ q }) => useLibrary(ep, q), { initialProps: { q: "бюджет" } });
  await vi.waitFor(() => expect(signals).toHaveLength(1));
  rerender({ q: "бюджет квартал" });
  await vi.waitFor(() => expect(signals).toHaveLength(2));
  expect(signals[0]!.aborted).toBe(true);
  expect(signals[1]!.aborted).toBe(false);
  vi.mocked(searchLibrary).mockReset();
  vi.mocked(searchLibrary).mockResolvedValue({ items: [] });
});

test("одна буква — ещё не поиск: весь список", async () => {
  vi.mocked(searchLibrary).mockClear();
  vi.mocked(getRecordings).mockClear();
  renderHook(() => useLibrary(ep, "б"));
  await vi.waitFor(() => expect(getRecordings).toHaveBeenCalledWith(ep));
  expect(searchLibrary).not.toHaveBeenCalled();
});

test("фильтр по категориям уходит резиденту (до лимита списка) и при смене перечитывает", async () => {
  vi.mocked(getRecordings).mockClear();
  vi.mocked(searchLibrary).mockClear();
  const { rerender } = renderHook(({ cats, q }) => useLibrary(ep, q, 0, 0, cats),
    { initialProps: { cats: ["retro"], q: "" } });
  await vi.waitFor(() => expect(getRecordings).toHaveBeenCalledWith(ep, undefined, ["retro"]));
  rerender({ cats: ["retro", "_none"], q: "" });
  await vi.waitFor(() => expect(getRecordings).toHaveBeenCalledWith(ep, undefined, ["retro", "_none"]));
  rerender({ cats: ["retro", "_none"], q: "бюджет" });
  await vi.waitFor(() => expect(searchLibrary).toHaveBeenCalledWith(ep, "бюджет", expect.any(AbortSignal), ["retro", "_none"]));
});

test("прогресс задач (без смены содержимого) не перечитывает список — ни с фильтром, ни без", async () => {
  for (const cats of [["retro"], NO_CATS]) {
    const { rerender, unmount } = renderHook(({ tick, content }) => useLibrary(ep, "", tick, content, cats),
      { initialProps: { tick: 0, content: 0 } });
    await vi.waitFor(() => expect(getRecordings).toHaveBeenCalled());
    vi.mocked(getRecordings).mockClear();
    vi.mocked(getJobs).mockClear();
    // Десять событий job.progress подряд.
    for (let t = 1; t <= 10; t++) {
      rerender({ tick: t, content: 0 });
      await act(async () => {});
    }
    expect(getRecordings).not.toHaveBeenCalled();
    expect(vi.mocked(getJobs).mock.calls.length).toBe(10);
    // job.done: и задачи, и список — один раз.
    vi.mocked(getJobs).mockClear();
    rerender({ tick: 11, content: 1 });
    await vi.waitFor(() => expect(getRecordings).toHaveBeenCalledTimes(1));
    expect(getJobs).toHaveBeenCalledTimes(1);
    unmount();
    vi.mocked(getRecordings).mockClear();
  }
});
