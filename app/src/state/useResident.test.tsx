import { act, renderHook } from "@testing-library/react";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  resolveEndpoint: vi.fn(),
  getState: vi.fn().mockResolvedValue({ status: "idle", folder: null, levels: {} }),
}));
import { NoResidentError, resolveEndpoint } from "../lib/api";
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
