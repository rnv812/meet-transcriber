import { act, renderHook, waitFor } from "@testing-library/react";
import * as api from "../../lib/api";
import { UNDO_MS, useGroupsUi } from "./useGroupsUi";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getGroups: vi.fn(),
  deleteGroup: vi.fn(),
  createGroup: vi.fn(),
  setGroupMembers: vi.fn(),
}));

const ep = { base: "/api", token: null };
const INFO = { groups: [{ id: "g-a", name: "Альфа", color: "#4c8bf5", count: 2 }], unknown: [], none: 1 };

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  vi.mocked(api.getGroups).mockResolvedValue(INFO);
});

test("запомненная область сужает список, только когда /groups ответил и группа есть", async () => {
  window.localStorage.setItem("meet.groupScope", '"g-a"');
  let resolve: (v: typeof INFO) => void = () => {};
  vi.mocked(api.getGroups).mockReturnValue(new Promise((r) => { resolve = r; }));
  const { result } = renderHook(() => useGroupsUi(ep, 0, "", {}));
  expect(result.current.shown).toBe(false);
  // Без ответа список не сужается молча (нет ни заголовка, ни панели, где область видно).
  expect(result.current.libraryScope).toBeNull();
  await act(async () => resolve(INFO));
  expect(result.current.shown).toBe(true);
  expect(result.current.scope).toBe("g-a");
  expect(result.current.libraryScope).toBe("g-a");
  expect(result.current.supported).toBe(true);
  expect(result.current.scopeName).toBe("Альфа");
  expect(result.current.scopeCount).toBe(2);
  expect(result.current.total).toBe(3);
});

test("старый резидент: область не в фильтре, интерфейс скрыт", async () => {
  window.localStorage.setItem("meet.groupScope", '"g-a"');
  vi.mocked(api.getGroups).mockRejectedValue(new api.ApiError(404, "нет"));
  const { result } = renderHook(() => useGroupsUi(ep, 0, "", {}));
  await waitFor(() => expect(result.current.shown).toBe(false));
  await waitFor(() => expect(result.current.libraryScope).toBeNull());
  expect(result.current.shown).toBe(false);
  expect(result.current.scope).toBeNull();
  expect(result.current.scopeName).toBeNull();
});

test("/groups не ответил (500) при запомненной области — все записи и «Группы недоступны» с повтором", async () => {
  window.localStorage.setItem("meet.groupScope", '"g-a"');
  vi.mocked(api.getGroups).mockRejectedValueOnce(new api.ApiError(500, "сбой"));
  const { result } = renderHook(() => useGroupsUi(ep, 0, "", {}));
  await waitFor(() => expect(result.current.unavailable).toBe(true));
  expect(result.current.libraryScope).toBeNull();
  expect(result.current.shown).toBe(false);
  expect(result.current.supported).toBeNull();
  await act(async () => result.current.retry());
  await waitFor(() => expect(result.current.shown).toBe(true));
  expect(result.current.unavailable).toBe(false);
  expect(result.current.libraryScope).toBe("g-a");
});

test("с поиском или фильтром область неизвестной группы без найденного проверяется без фильтра", async () => {
  window.localStorage.setItem("meet.groupScope", '"g-old"');
  // С запросом неизвестной группы в ответе нет; без фильтра — есть.
  vi.mocked(api.getGroups).mockImplementation(async (_ep, q) =>
    (q ? INFO : { ...INFO, unknown: [{ id: "g-old", count: 2 }] }));
  const { result } = renderHook(() => useGroupsUi(ep, 0, "бюджет", {}));
  await waitFor(() => expect(result.current.shown).toBe(true));
  await act(async () => {});
  expect(api.getGroups).toHaveBeenCalledWith(ep);
  expect(result.current.scope).toBe("g-old");
  expect(result.current.libraryScope).toBe("g-old");
  expect(result.current.scopeName).toBe("Группа без названия");
});

test("удалённая группа не остаётся областью и при запомненном фильтре категорий", async () => {
  window.localStorage.setItem("meet.groupScope", '"g-gone"');
  const { result } = renderHook(() => useGroupsUi(ep, 0, "", { categories: ["retro"] }));
  await waitFor(() => expect(result.current.shown).toBe(true));
  await waitFor(() => expect(result.current.scope).toBeNull());
  expect(window.localStorage.getItem("meet.groupScope")).toBeNull();
});

test("уведомление: пока на нём указатель или фокус, время не идёт", async () => {
  vi.mocked(api.deleteGroup).mockResolvedValue({
    group: { id: "g-a", name: "Альфа", color: "#4c8bf5", created_at: "2026-10-01T10:00:00" }, index: 0,
  });
  const { result } = renderHook(() => useGroupsUi(ep, 0, "", {}));
  await waitFor(() => expect(result.current.shown).toBe(true));
  vi.useFakeTimers();
  try {
    await act(async () => { await result.current.remove("g-a"); });
    act(() => { vi.advanceTimersByTime(3000); });
    act(() => result.current.holdToast(true));
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(result.current.toast).not.toBeNull();
    act(() => result.current.holdToast(false));
    act(() => { vi.advanceTimersByTime(4900); });
    expect(result.current.toast).not.toBeNull();
    act(() => { vi.advanceTimersByTime(200); });
    expect(result.current.toast).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("файл снова повредился после «Скрыть» — предупреждение возвращается", async () => {
  vi.mocked(api.getGroups).mockResolvedValue({ ...INFO, broken: true });
  const { result, rerender } = renderHook(({ tick }) => useGroupsUi(ep, tick, "", {}), { initialProps: { tick: 0 } });
  await waitFor(() => expect(result.current.broken).toEqual({ copy: null }));
  act(() => result.current.dismissBroken());
  expect(result.current.broken).toBeNull();
  vi.mocked(api.getGroups).mockResolvedValue(INFO);
  rerender({ tick: 1 });
  await waitFor(() => expect(api.getGroups).toHaveBeenCalledTimes(2));
  vi.mocked(api.getGroups).mockResolvedValue({ ...INFO, broken: true });
  rerender({ tick: 2 });
  await waitFor(() => expect(result.current.broken).toEqual({ copy: null }));
});

test("«Отменить» перенос: каждая группа отдельно, неизвестная — с restore; упавшая не мешает остальным", async () => {
  vi.mocked(api.setGroupMembers).mockImplementation(async (_ep, gid, change) => {
    if (gid === "g-x" && change.add) throw new api.ApiError(503, "файл групп сейчас занят");
    return { changed: [...(change.add ?? []), ...(change.remove ?? [])], failed: [] };
  });
  const { result } = renderHook(() => useGroupsUi(ep, 0, "", {}));
  await waitFor(() => expect(result.current.shown).toBe(true));
  await act(async () => {
    await result.current.moveMeetings(["a", "b", "c", "d"], { a: "g-a", b: "g-old", c: "g-x", d: null }, "g-new", "Новая");
  });
  expect(result.current.toast?.text).toBe("Перемещено в «Новая»");
  vi.mocked(api.setGroupMembers).mockClear();
  await act(async () => { await result.current.toast!.undo!(); });
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-a", { add: ["a"], restore: true });
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-old", { add: ["b"], restore: true });
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-x", { add: ["c"], restore: true });
  expect(api.setGroupMembers).toHaveBeenCalledWith(ep, "g-new", { remove: ["d"] });
  expect(result.current.toast).toMatchObject({ error: true, text: "Не удалось вернуть 1 из 4: файл групп сейчас занят" });
});

test("уведомление с «Отменить» живёт 8 секунд", async () => {
  vi.mocked(api.deleteGroup).mockResolvedValue({
    group: { id: "g-a", name: "Альфа", color: "#4c8bf5", created_at: "2026-10-01T10:00:00" }, index: 0,
  });
  const { result } = renderHook(() => useGroupsUi(ep, 0, "", {}));
  await waitFor(() => expect(result.current.shown).toBe(true));
  vi.useFakeTimers();
  try {
    await act(async () => { await result.current.remove("g-a"); });
    expect(result.current.toast?.text).toBe("Группа «Альфа» удалена");
    expect(result.current.toast?.undo).toBeTypeOf("function");
    act(() => { vi.advanceTimersByTime(UNDO_MS - 100); });
    expect(result.current.toast).not.toBeNull();
    act(() => { vi.advanceTimersByTime(200); });
    expect(result.current.toast).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test("перенос туда же, где встречи уже есть, — без запросов", async () => {
  const { result } = renderHook(() => useGroupsUi(ep, 0, "", {}));
  await waitFor(() => expect(result.current.shown).toBe(true));
  await act(async () => { await result.current.moveMeetings(["a", "b"], { a: "g-a", b: "g-a" }, "g-a"); });
  await act(async () => { await result.current.moveMeetings(["c"], { c: null }, null); });
  expect(api.setGroupMembers).not.toHaveBeenCalled();
  expect(result.current.toast).toBeNull();
});

test("запись отложила повреждённый файл — предупреждение с путём", async () => {
  vi.mocked(api.createGroup).mockResolvedValue({ id: "g-n", name: "Новая", color: "#4c8bf5", created_at: "",
    moved_broken: "C:/rec/.meet-groups.json.broken-1" });
  const { result } = renderHook(() => useGroupsUi(ep, 0, "", {}));
  await waitFor(() => expect(result.current.shown).toBe(true));
  expect(result.current.broken).toBeNull();
  act(() => result.current.create());
  await act(async () => { expect(await result.current.submitDialog("Новая", "#4c8bf5")).toBeNull(); });
  expect(result.current.broken).toEqual({ copy: "C:/rec/.meet-groups.json.broken-1" });
  act(() => result.current.dismissBroken());
  expect(result.current.broken).toBeNull();
});
