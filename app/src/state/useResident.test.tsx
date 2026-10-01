import { act, renderHook } from "@testing-library/react";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  resolveEndpoint: vi.fn(),
  getState: vi.fn().mockResolvedValue({ status: "idle", folder: null, levels: {} }),
}));
import { NoResidentError, getState, resolveEndpoint } from "../lib/api";
import type { Snapshot } from "../lib/types";
import { useResident } from "./useResident";
import { FakeEventSource } from "../test/setup";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

test("нет резидента: offline без ошибки, потом online", async () => {
  vi.mocked(resolveEndpoint).mockRejectedValue(new NoResidentError("нет"));
  const { result } = renderHook(() => useResident());
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(result.current.status).toBe("offline");
  expect(result.current.endpoint).toBeNull();

  vi.mocked(resolveEndpoint).mockResolvedValue({ base: "http://127.0.0.1:1", token: "t" });
  await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
  expect(result.current.endpoint).not.toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(result.current.status).toBe("online");
});

test("пачка событий в одном act не теряется: счётчик растёт на каждое", async () => {
  vi.mocked(resolveEndpoint).mockResolvedValue({ base: "http://127.0.0.1:1", token: "t" });
  const { result } = renderHook(() => useResident());
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  const es = FakeEventSource.instances.at(-1)!;
  act(() => {
    es.emit("record.stopped", { kind: "record.stopped", at: 1 });
    es.emit("job.queued", { kind: "job.queued", at: 2 });
    es.emit("log", { kind: "log", at: 3 });
  });
  expect(result.current.libraryTick).toBe(2);
});

test("applySnapshot сразу меняет снимок и время его прихода", async () => {
  vi.mocked(resolveEndpoint).mockResolvedValue({ base: "http://127.0.0.1:1", token: "t" });
  const { result } = renderHook(() => useResident());
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  const before = result.current.snapshotAt;
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  act(() => result.current.applySnapshot({ status: "recording", folder: "C:/r/x", elapsed_s: 0, levels: {} } as never));
  expect(result.current.snapshot?.status).toBe("recording");
  expect(result.current.snapshotAt).toBeGreaterThan(before);
});

test("record.started/stopped: снимок перечитывается сразу, не ждёт опроса", async () => {
  vi.mocked(resolveEndpoint).mockResolvedValue({ base: "http://127.0.0.1:1", token: "t" });
  const { result } = renderHook(() => useResident());
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(result.current.snapshot?.status).toBe("idle");
  vi.mocked(getState).mockResolvedValueOnce({ status: "recording", folder: "C:/r/x", elapsed_s: 1, levels: {} } as Partial<Snapshot> as Snapshot);
  const es = FakeEventSource.instances.at(-1)!;
  await act(async () => {
    es.emit("record.started", { kind: "record.started", at: 1 });
    await vi.advanceTimersByTimeAsync(10);
  });
  expect(result.current.snapshot?.status).toBe("recording");
});

test("doneTick растёт только на job.done", async () => {
  vi.mocked(resolveEndpoint).mockResolvedValue({ base: "http://127.0.0.1:1", token: "t" });
  const { result } = renderHook(() => useResident());
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  const es = FakeEventSource.instances.at(-1)!;
  act(() => {
    es.emit("job.progress", { kind: "job.progress", at: 1 });
    es.emit("job.done", { kind: "job.done", at: 2 });
  });
  expect(result.current.doneTick).toBe(1);
  expect(result.current.libraryTick).toBe(2);
});

test("live.*: библиотека и снимок перечитываются сразу — папка живой записи появляется в списке", async () => {
  vi.mocked(resolveEndpoint).mockResolvedValue({ base: "http://127.0.0.1:1", token: "t" });
  const { result } = renderHook(() => useResident());
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  const live = { active: true, starting: false, stopping: false, folder: "C:/r/x", error: null, started_at: 1 };
  vi.mocked(getState).mockResolvedValueOnce({ status: "idle", folder: null, levels: {}, live } as Partial<Snapshot> as Snapshot);
  const es = FakeEventSource.instances.at(-1)!;
  const before = result.current.libraryTick;
  await act(async () => {
    es.emit("live.started", { kind: "live.started", at: 1, folder: "C:/r/x" });
    await vi.advanceTimersByTimeAsync(10);
  });
  expect(result.current.libraryTick).toBe(before + 1);
  expect(result.current.snapshot?.live?.active).toBe(true);
  act(() => {
    for (const kind of ["live.starting", "live.stopping", "live.stopped", "live.failed"]) es.emit(kind, { kind, at: 2 });
  });
  expect(result.current.libraryTick).toBe(before + 5);
});

test("contentTick — без прогресса задач: поставлена, готова, упала, запись", async () => {
  vi.mocked(resolveEndpoint).mockResolvedValue({ base: "http://127.0.0.1:1", token: "t" });
  const { result } = renderHook(() => useResident());
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  const es = FakeEventSource.instances.at(-1)!;
  act(() => {
    for (const kind of ["job.queued", "job.started", "job.progress", "job.progress", "job.done", "job.failed", "record.stopped"]) {
      es.emit(kind, { kind, at: 1 });
    }
  });
  expect(result.current.libraryTick).toBe(7);
  expect(result.current.contentTick).toBe(4);
});
