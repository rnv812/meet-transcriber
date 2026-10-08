import { act, renderHook, waitFor } from "@testing-library/react";

import * as api from "../../lib/api";
import { TERMS_ACCEPTED_EVENT, TERMS_VERSION } from "../../lib/terms";
import { useTermsAccepted } from "./useTermsAccepted";

const ep = { base: "/api", token: "t" };
afterEach(() => vi.restoreAllMocks());

test("без резидента не знаем (null) и не спрашиваем", () => {
  const spy = vi.spyOn(api, "getSettings");
  const { result } = renderHook(() => useTermsAccepted(null));
  expect(result.current).toBeNull();
  expect(spy).not.toHaveBeenCalled();
});

test("ответ резидента: не приняты → false; приняли в окне Meet → панель узнаёт при фокусе", async () => {
  const spy = vi.spyOn(api, "getSettings").mockResolvedValue({ ui: { terms_accepted: "" } });
  const { result } = renderHook(() => useTermsAccepted(ep));
  await waitFor(() => expect(result.current).toBe(false));
  spy.mockResolvedValue({ ui: { terms_accepted: TERMS_VERSION } });
  act(() => { window.dispatchEvent(new Event("focus")); });
  await waitFor(() => expect(result.current).toBe(true));
  expect(spy).toHaveBeenCalledTimes(2);
});

test("новый показ панели (refresh) перечитывает; резидент не ответил — прежнее значение", async () => {
  const spy = vi.spyOn(api, "getSettings").mockResolvedValue({ ui: { terms_accepted: "" } });
  const { result, rerender } = renderHook(({ tick }) => useTermsAccepted(ep, tick), { initialProps: { tick: 0 } });
  await waitFor(() => expect(result.current).toBe(false));
  spy.mockRejectedValue(new Error("нет связи"));
  rerender({ tick: 1 });
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  expect(result.current).toBe(false);
});

test("приняли в этом же окне (acceptTerms) — сразу true", async () => {
  vi.spyOn(api, "getSettings").mockResolvedValue({ ui: {} });
  const { result } = renderHook(() => useTermsAccepted(ep));
  await waitFor(() => expect(result.current).toBe(false));
  act(() => { window.dispatchEvent(new Event(TERMS_ACCEPTED_EVENT)); });
  expect(result.current).toBe(true);
});
