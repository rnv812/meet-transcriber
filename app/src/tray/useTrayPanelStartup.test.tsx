import { act, renderHook, waitFor } from "@testing-library/react";

import * as api from "../lib/api";
import { FakeEventSource } from "../test/setup";

// Оболочка: окно создано спрятанным; подписка на `tray-panel` готова, только
// когда тест её «зарегистрирует» (`register`), — события до этого теряются.
const shell = vi.hoisted(() => ({
  windowVisible: false,
  register: null as (() => void) | null,
  cb: null as ((visible: boolean) => void) | null,
}));
vi.mock("../lib/shell", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/shell")>()),
  onTrayPanel: (cb: (visible: boolean) => void) =>
    new Promise<() => void>((resolve) => {
      shell.register = () => { shell.cb = cb; resolve(() => {}); };
    }),
  trayPanelVisible: async () => shell.windowVisible,
}));

const { useTrayPanel } = await import("./useTrayPanel");

beforeEach(() => {
  shell.windowVisible = false;
  shell.register = null;
  shell.cb = null;
  vi.spyOn(api, "getRecentRecordings").mockResolvedValue({ root: "", items: [] });
  vi.spyOn(api, "getAssistant").mockRejectedValue(new Error("нет"));
});
afterEach(() => vi.restoreAllMocks());

test("первый показ, случившийся пока подписка регистрировалась, не теряется", async () => {
  const { result } = renderHook(() => useTrayPanel());
  // Оболочка показала окно, а событие пришло до подписки — потерялось.
  shell.windowVisible = true;
  await act(async () => { shell.register?.(); });
  await waitFor(() => expect(result.current.visible).toBe(true));
  // Поток событий открыт: панель не пустая.
  await waitFor(() => expect(FakeEventSource.instances.some((es) => !es.closed)).toBe(true));
});

test("окно так и не показано — панель считает себя спрятанной и поток закрывает", async () => {
  const { result } = renderHook(() => useTrayPanel());
  await act(async () => { shell.register?.(); });
  await waitFor(() => expect(result.current.visible).toBe(false));
  expect(FakeEventSource.instances.every((es) => es.closed)).toBe(true);
  // Потом показали — событие уже доходит.
  act(() => shell.cb?.(true));
  expect(result.current.visible).toBe(true);
  expect(result.current.shownTick).toBe(1);
  await waitFor(() => expect(FakeEventSource.instances.some((es) => !es.closed)).toBe(true));
});
