import { act, renderHook } from "@testing-library/react";

const h = vi.hoisted(() => ({
  ApiError: class extends Error {
    constructor(public status: number, message: string) { super(message); }
  },
}));
vi.mock("../lib/api", async (orig) => ({
  libraryFilterKey: (await orig<typeof import("../lib/api")>()).libraryFilterKey,
  ApiError: h.ApiError,
  getGroups: vi.fn(),
}));
import { getGroups } from "../lib/api";
import { useGroups } from "./useGroups";

const ep = { base: "http://h", token: "t" };
const info = {
  groups: [{ id: "g-1", name: "Проект Альфа", color: "#4f8cc9", count: 3 }],
  unknown: [{ id: "g-old", count: 1 }],
};

beforeEach(() => {
  vi.mocked(getGroups).mockReset();
  vi.mocked(getGroups).mockResolvedValue(info);
});

test("группы и неизвестные id приходят, резидент их умеет", async () => {
  const { result } = renderHook(() => useGroups(ep));
  expect(result.current.supported).toBeNull();
  await vi.waitFor(() => expect(result.current.loaded).toBe(true));
  expect(result.current.groups).toEqual(info.groups);
  expect(result.current.unknown).toEqual(info.unknown);
  expect(result.current.supported).toBe(true);
  expect(getGroups).toHaveBeenCalledWith(ep, undefined, undefined);
});

test("старый резидент без /groups — интерфейс групп прячется", async () => {
  vi.mocked(getGroups).mockRejectedValue(new h.ApiError(404, "нет такого адреса"));
  const { result } = renderHook(() => useGroups(ep));
  await vi.waitFor(() => expect(result.current.supported).toBe(false));
  expect(result.current.groups).toEqual([]);
  expect(result.current.loaded).toBe(true);
});

test("сбой чтения — прежний список", async () => {
  const { result, rerender } = renderHook(({ tick }) => useGroups(ep, tick), { initialProps: { tick: 0 } });
  await vi.waitFor(() => expect(result.current.groups).toEqual(info.groups));
  vi.mocked(getGroups).mockRejectedValueOnce(new h.ApiError(500, "сбой"));
  rerender({ tick: 1 });
  await act(async () => {});
  expect(result.current.groups).toEqual(info.groups);
  expect(result.current.supported).toBe(true);
});

test("перечитываются по тику (groups.changed), запросу и фильтру — не по новому объекту фильтра", async () => {
  const filter = { people: ["Анна"] };
  const { rerender } = renderHook(({ tick, q, f }) => useGroups(ep, tick, q, f),
    { initialProps: { tick: 0, q: "", f: filter } });
  await vi.waitFor(() => expect(getGroups).toHaveBeenCalledTimes(1));
  expect(getGroups).toHaveBeenLastCalledWith(ep, undefined, filter);
  rerender({ tick: 0, q: "", f: { ...filter } });
  await act(async () => {});
  expect(getGroups).toHaveBeenCalledTimes(1);
  rerender({ tick: 1, q: "", f: filter });
  await vi.waitFor(() => expect(getGroups).toHaveBeenCalledTimes(2));
  rerender({ tick: 1, q: "бюджет", f: filter });
  await vi.waitFor(() => expect(getGroups).toHaveBeenLastCalledWith(ep, "бюджет", filter));
});

test("запрос — с задержкой, как у списка: на набор слова один вызов; короткий — как без запроса", async () => {
  vi.useFakeTimers();
  try {
    const { rerender } = renderHook(({ q }) => useGroups(ep, 0, q), { initialProps: { q: "" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(getGroups).toHaveBeenCalledTimes(1);
    for (const q of ["б", "бю", "бюд", "бюдж"]) {
      rerender({ q });
      await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    }
    expect(getGroups).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(getGroups).toHaveBeenCalledTimes(2);
    expect(getGroups).toHaveBeenLastCalledWith(ep, "бюдж", undefined);
    rerender({ q: "б" }); // одна буква — счётчики по всей библиотеке
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(getGroups).toHaveBeenLastCalledWith(ep, undefined, undefined);
  } finally {
    vi.useRealTimers();
  }
});

test("повреждённый файл групп и файл новой версии видны окну", async () => {
  vi.mocked(getGroups).mockResolvedValue({ ...info, broken: true, broken_copy: "C:/r/.meet-groups.json.broken-1", newer: true });
  const { result } = renderHook(() => useGroups(ep));
  await vi.waitFor(() => expect(result.current.broken).toBe(true));
  expect(result.current.brokenCopy).toBe("C:/r/.meet-groups.json.broken-1");
  expect(result.current.newer).toBe(true);
});
