import { act, renderHook } from "@testing-library/react";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  resolveEndpoint: vi.fn(),
  getState: vi.fn().mockResolvedValue(null),
}));
import { NoResidentError, resolveEndpoint } from "../lib/api";
import { useResident } from "./useResident";

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
