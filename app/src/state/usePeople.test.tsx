import { act, renderHook, waitFor } from "@testing-library/react";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  getPeople: vi.fn().mockResolvedValue({ items: [] }),
}));
import { getPeople } from "../lib/api";
import { usePeople } from "./usePeople";

const ep = { base: "/api", token: null };

test("каждый job.done (doneTick) перечитывает базу людей", async () => {
  const { rerender } = renderHook(({ tick }) => usePeople(ep, tick), { initialProps: { tick: 0 } });
  await waitFor(() => expect(getPeople).toHaveBeenCalledTimes(1));
  await act(async () => { rerender({ tick: 1 }); });
  await waitFor(() => expect(getPeople).toHaveBeenCalledTimes(2));
  await act(async () => { rerender({ tick: 1 }); });
  expect(getPeople).toHaveBeenCalledTimes(2);
});
