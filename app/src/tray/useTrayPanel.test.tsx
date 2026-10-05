import { act, renderHook, waitFor } from "@testing-library/react";

import * as api from "../lib/api";
import type { Snapshot } from "../lib/types";
import { FakeEventSource } from "../test/setup";

const shown: { cb: ((visible: boolean) => void) | null } = { cb: null };
vi.mock("../lib/shell", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/shell")>()),
  onTrayPanel: async (cb: (visible: boolean) => void) => { shown.cb = cb; return () => {}; },
}));

const { useTrayPanel } = await import("./useTrayPanel");

const snap = (extra: Partial<Snapshot> = {}): Snapshot => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, ...extra,
} as Snapshot);

beforeEach(() => {
  vi.spyOn(api, "getRecentRecordings").mockResolvedValue({ root: "", items: [] });
  vi.spyOn(api, "getAssistant").mockRejectedValue(new Error("нет"));
});
afterEach(() => vi.restoreAllMocks());

test("поток событий открыт, только пока панель видна; остановку панель замечает сама", async () => {
  const { result } = renderHook(() => useTrayPanel());
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const first = FakeEventSource.instances[0]!;
  act(() => first.emit("state", snap({ status: "recording", folder: "D:\\rec\\a" })));
  expect(result.current.snapshot?.status).toBe("recording");
  expect(result.current.online).toBe(true);
  act(() => first.emit("state", snap()));
  expect(result.current.stopped?.id).toBe("a");

  act(() => shown.cb?.(false));
  expect(first.closed).toBe(true);
  expect(result.current.visible).toBe(false);

  act(() => shown.cb?.(true));
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
  expect(result.current.shownTick).toBe(1);
  expect(api.getRecentRecordings).toHaveBeenCalledTimes(2);
});

test("уровни звука снимок не перечитывают — только события, что его меняют", async () => {
  const getState = vi.spyOn(api, "getState").mockResolvedValue(snap({ status: "recording" }));
  renderHook(() => useTrayPanel());
  await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
  const es = FakeEventSource.instances.at(-1)!;
  act(() => es.emit("state", snap({ status: "recording" })));
  for (let i = 0; i < 3; i++) act(() => es.emit("record.level", { kind: "record.level", levels: { mic: 0.3 } }));
  act(() => es.emit("record.silence", { kind: "record.silence" }));
  act(() => es.emit("log", { kind: "log", line: "x" }));
  expect(getState).not.toHaveBeenCalled();
  act(() => es.emit("record.stopped", { kind: "record.stopped" }));
  act(() => es.emit("record.device_fallback", { kind: "record.device_fallback" }));
  await waitFor(() => expect(getState).toHaveBeenCalledTimes(2));
});
