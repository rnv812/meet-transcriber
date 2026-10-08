import { act, renderHook, waitFor } from "@testing-library/react";
import * as api from "../lib/api";
import * as shell from "../lib/shell";
import { CACHE_KEY, DEFAULT_APPEARANCE } from "./appearance";
import { useAppearance } from "./useAppearance";

vi.mock("../lib/api", async (orig) => ({ ...(await orig<typeof import("../lib/api")>()), getSettings: vi.fn() }));
vi.mock("../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../lib/shell")>()), shareAppearance: vi.fn(), onAppearance: vi.fn(),
}));

const ep = { base: "/api", token: null };
let systemDark = true;
const listeners = new Set<(e: { matches: boolean }) => void>();

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  systemDark = true;
  listeners.clear();
  window.matchMedia = vi.fn().mockImplementation(() => ({
    get matches() { return systemDark; },
    addEventListener: (_: string, cb: (e: { matches: boolean }) => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: (e: { matches: boolean }) => void) => listeners.delete(cb),
  }));
  vi.mocked(shell.onAppearance).mockResolvedValue(() => {});
});

const root = () => document.documentElement.dataset;

test("без резидента — кеш или умолчания, без ошибок", () => {
  const { result } = renderHook(() => useAppearance(null));
  expect(result.current.appearance).toEqual(DEFAULT_APPEARANCE);
  expect(root().theme).toBe("dark");
  expect(api.getSettings).not.toHaveBeenCalled();
});

test("настройки резидента применяются и попадают в кеш", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({ ui: { theme: "light", aurora: "amber", aurora_style: "waves", motion: true } });
  renderHook(() => useAppearance(ep));
  await waitFor(() => expect(root().theme).toBe("light"));
  expect(root().aurora).toBe("amber");
  expect(root().auroraStyle).toBe("waves");
  expect(JSON.parse(localStorage.getItem(CACHE_KEY)!)).toMatchObject({ theme: "light", aurora: "amber" });
});

test("системная тема меняется вместе с ОС", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({ ui: { theme: "system" } });
  renderHook(() => useAppearance(ep));
  await waitFor(() => expect(root().theme).toBe("dark"));
  systemDark = false;
  act(() => listeners.forEach((cb) => cb({ matches: false })));
  expect(root().theme).toBe("light");
});

test("выбор из другого окна (событие оболочки) применяется", async () => {
  let push: (a: typeof DEFAULT_APPEARANCE) => void = () => {};
  vi.mocked(shell.onAppearance).mockImplementation(async (cb) => { push = cb; return () => {}; });
  vi.mocked(api.getSettings).mockResolvedValue({ ui: {} });
  renderHook(() => useAppearance(ep));
  await waitFor(() => expect(shell.onAppearance).toHaveBeenCalled());
  act(() => push({ ...DEFAULT_APPEARANCE, theme: "light", aurora: "green" }));
  expect(root().theme).toBe("light");
  expect(root().aurora).toBe("green");
});

test("preview: окно, кеш и оболочка сразу", () => {
  const { result } = renderHook(() => useAppearance(null));
  const next = { ...DEFAULT_APPEARANCE, theme: "light" as const, aurora: "red" as const };
  act(() => result.current.preview(next));
  expect(root().theme).toBe("light");
  expect(root().aurora).toBe("red");
  expect(shell.shareAppearance).toHaveBeenCalledWith(next);
  expect(JSON.parse(localStorage.getItem(CACHE_KEY)!)).toEqual(next);
});

test("поздний ответ настроек не затирает более новый выбор (последняя запись выигрывает)", async () => {
  let resolve: (v: Record<string, unknown>) => void = () => {};
  vi.mocked(api.getSettings).mockImplementation(() => new Promise((r) => { resolve = r; }));
  const { result } = renderHook(() => useAppearance(ep));
  await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
  const next = { ...DEFAULT_APPEARANCE, theme: "light" as const, aurora: "red" as const };
  act(() => result.current.preview(next));
  await act(async () => { resolve({ ui: { theme: "dark", aurora: "blue" } }); });
  expect(root().theme).toBe("light");
  expect(root().aurora).toBe("red");
  expect(result.current.appearance).toEqual(next);
});

test("событие оболочки новее ответа настроек", async () => {
  let push: (a: typeof DEFAULT_APPEARANCE) => void = () => {};
  vi.mocked(shell.onAppearance).mockImplementation(async (cb) => { push = cb; return () => {}; });
  let resolve: (v: Record<string, unknown>) => void = () => {};
  vi.mocked(api.getSettings).mockImplementation(() => new Promise((r) => { resolve = r; }));
  renderHook(() => useAppearance(ep));
  await waitFor(() => expect(shell.onAppearance).toHaveBeenCalled());
  act(() => push({ ...DEFAULT_APPEARANCE, theme: "light", aurora: "green" }));
  await act(async () => { resolve({ ui: { theme: "dark", aurora: "blue" } }); });
  expect(root().theme).toBe("light");
  expect(root().aurora).toBe("green");
});

test("onAppearance отклонён — без необработанного отказа", async () => {
  vi.mocked(shell.onAppearance).mockRejectedValue(new Error("no tauri"));
  vi.mocked(api.getSettings).mockResolvedValue({ ui: {} });
  const unhandled = vi.fn();
  process.on("unhandledRejection", unhandled);
  try {
    renderHook(() => useAppearance(ep));
    await waitFor(() => expect(shell.onAppearance).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(unhandled).not.toHaveBeenCalled();
  } finally {
    process.off("unhandledRejection", unhandled);
  }
});

test("резидент не ответил — остаётся кеш, без исключения", async () => {
  localStorage.setItem(CACHE_KEY, JSON.stringify({ ...DEFAULT_APPEARANCE, theme: "light" }));
  vi.mocked(api.getSettings).mockRejectedValue(new Error("offline"));
  renderHook(() => useAppearance(ep));
  await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
  expect(root().theme).toBe("light");
});
