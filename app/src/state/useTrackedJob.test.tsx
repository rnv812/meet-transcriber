import { act, renderHook } from "@testing-library/react";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  getJobs: vi.fn(),
}));
import { getJobs } from "../lib/api";
import type { Job } from "../lib/types";
import { POLL_JOB_MS, useTrackedJobs } from "./useTrackedJob";

const ep = { base: "/api", token: null };
const job = (id: string, folder: string, state: Job["state"]): Job => ({
  id, kind: "download-model", folder, state, stage: null, label: null, done: null, total: null,
  note: null, result: state === "done" ? folder : null, error: null,
});

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

test("две загрузки: кончилась одна — onSettled, вторая идёт дальше", async () => {
  vi.mocked(getJobs)
    .mockResolvedValueOnce({ items: [job("j1", "a", "running"), job("j2", "b", "running")] })
    .mockResolvedValue({ items: [job("j1", "a", "done"), job("j2", "b", "running")] });
  const settled = vi.fn();
  const { result } = renderHook(() => useTrackedJobs(ep, "download-model", settled));
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(Object.keys(result.current[0]).sort()).toEqual(["a", "b"]);

  await act(async () => { await vi.advanceTimersByTimeAsync(POLL_JOB_MS); });
  expect(result.current[0].a?.state).toBe("done");
  expect(result.current[0].b?.state).toBe("running");
  expect(settled).toHaveBeenCalledTimes(1);

  // Опрос продолжается, пока идёт вторая; первая второй раз «кончившейся» не считается.
  await act(async () => { await vi.advanceTimersByTimeAsync(POLL_JOB_MS); });
  expect(settled).toHaveBeenCalledTimes(1);
});

test("модель поставили заново — ход показывается по новой задаче", async () => {
  vi.mocked(getJobs)
    .mockResolvedValueOnce({ items: [] })
    .mockResolvedValue({ items: [job("old", "a", "failed"), job("new", "a", "running")] });
  const settled = vi.fn();
  const { result } = renderHook(() => useTrackedJobs(ep, "download-model", settled));
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  act(() => result.current[1](job("old", "a", "running")));
  act(() => result.current[1](job("new", "a", "running")));
  await act(async () => { await vi.advanceTimersByTimeAsync(POLL_JOB_MS); });
  expect(result.current[0].a?.id).toBe("new");
});
