import { act, renderHook } from "@testing-library/react";

const h = vi.hoisted(() => ({
  ApiError: class extends Error {
    constructor(public status: number, message: string) { super(message); }
  },
}));
vi.mock("../lib/api", async (orig) => ({
  libraryFilterKey: (await orig<typeof import("../lib/api")>()).libraryFilterKey,
  ApiError: h.ApiError,
  getRecordings: vi.fn().mockResolvedValue({ root: "r", items: [] }),
  searchLibrary: vi.fn().mockResolvedValue({ items: [] }),
  getJobs: vi.fn().mockResolvedValue({ items: [] }),
}));
import { getJobs, getRecordings, searchLibrary } from "../lib/api";
import { useLibrary } from "./useLibrary";
import type { LibraryFilter } from "../lib/types";

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
  // Набор текста ждёт SEARCH_DELAY_MS; на медленном CI-раннере это дольше секунды по умолчанию.
  await vi.waitFor(() => expect(signals).toHaveLength(2), { timeout: 5000 });
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
  await vi.waitFor(() => expect(getRecordings).toHaveBeenCalledWith(ep, undefined, { categories: ["retro"] }));
  rerender({ cats: ["retro", "_none"], q: "" });
  await vi.waitFor(() => expect(getRecordings).toHaveBeenCalledWith(ep, undefined, { categories: ["retro", "_none"] }));
  rerender({ cats: ["retro", "_none"], q: "бюджет" });
  await vi.waitFor(() => expect(searchLibrary).toHaveBeenCalledWith(ep, "бюджет", expect.any(AbortSignal),
    { categories: ["retro", "_none"] }));
});

test("фильтр библиотеки целиком уходит резиденту; тот же фильтр новым объектом не перечитывает", async () => {
  vi.mocked(getRecordings).mockClear();
  const filter = { groups: ["g-1"], people: ["Анна"], from: "2026-09-01", has: ["summary" as const] };
  const { rerender } = renderHook(({ f }) => useLibrary(ep, "", 0, 0, f), { initialProps: { f: filter } });
  await vi.waitFor(() => expect(getRecordings).toHaveBeenCalledWith(ep, undefined, filter));
  await act(async () => {});
  const calls = vi.mocked(getRecordings).mock.calls.length;
  rerender({ f: { ...filter } });
  await act(async () => { await new Promise((r) => setTimeout(r, 300)); });
  expect(vi.mocked(getRecordings).mock.calls.length).toBe(calls);
  rerender({ f: { ...filter, groups: ["g-2"] } });
  await vi.waitFor(() => expect(getRecordings).toHaveBeenCalledWith(ep, undefined, { ...filter, groups: ["g-2"] }));
  // пустой фильтр — прежний вызов, как у старого резидента
  vi.mocked(getRecordings).mockClear();
  rerender({ f: {} as typeof filter });
  await vi.waitFor(() => expect(getRecordings).toHaveBeenCalledWith(ep));
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

test("с задержкой — только набор текста; смена области или метки (щелчок) — запрос сразу", async () => {
  vi.useFakeTimers();
  try {
    const { rerender } = renderHook(({ q, f, typed }) => useLibrary(ep, q, 0, 0, f, typed),
      { initialProps: { q: "", f: {} as LibraryFilter, typed: "" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    vi.mocked(getRecordings).mockClear();
    // Щелчок по группе в панели: только фильтр — сразу, без 250 мс старого списка под новым заголовком.
    rerender({ q: "", f: { groups: ["g-a"] }, typed: "" });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(getRecordings).toHaveBeenCalledTimes(1);
    expect(getRecordings).toHaveBeenLastCalledWith(ep, undefined, { groups: ["g-a"] });
    // Набор префикса меняет фильтр на каждую букву — это набор: с задержкой, один запрос.
    vi.mocked(getRecordings).mockClear();
    rerender({ q: "", f: { groups: ["g-a"], people: ["Ан"] }, typed: "участник:Ан" });
    rerender({ q: "", f: { groups: ["g-a"], people: ["Анн"] }, typed: "участник:Анн" });
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(getRecordings).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(getRecordings).toHaveBeenCalledTimes(1);
    expect(getRecordings).toHaveBeenLastCalledWith(ep, undefined, { groups: ["g-a"], people: ["Анн"] });
    // Набранное, которое список не поменяло («уч»), не делает следующий щелчок «набором».
    vi.mocked(getRecordings).mockClear();
    rerender({ q: "", f: { groups: ["g-a"], people: ["Анн"] }, typed: "участник:Анн уч" });
    rerender({ q: "", f: { groups: ["g-b"], people: ["Анн"] }, typed: "участник:Анн уч" });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(getRecordings).toHaveBeenCalledTimes(1);
  } finally {
    vi.useRealTimers();
  }
});
