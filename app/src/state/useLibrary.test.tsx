import { act, renderHook } from "@testing-library/react";

vi.mock("../lib/api", () => ({
  getRecordings: vi.fn().mockResolvedValue({ root: "r", items: [] }),
  getJobs: vi.fn().mockResolvedValue({ items: [] }),
}));
import { getRecordings } from "../lib/api";
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
