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
import { getRecordings, searchLibrary } from "../lib/api";
import { useLibrary } from "./useLibrary";

const ep = { base: "http://h", token: "t" };

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
  expect(searchLibrary).toHaveBeenCalledWith(ep, "бюджет");
});

test("резидент без поиска по тексту (404) — прежний поиск по записям", async () => {
  vi.mocked(searchLibrary).mockRejectedValueOnce(new h.ApiError(404, "нет"));
  vi.mocked(getRecordings).mockClear();
  renderHook(() => useLibrary(ep, "бюджет"));
  await vi.waitFor(() => expect(getRecordings).toHaveBeenCalledWith(ep, "бюджет"));
});
