import { act, renderHook } from "@testing-library/react";

import type { FeedLine } from "./useLive";
import { useLastLook, useLiveAsk } from "./useLastLook";
import type { Live } from "./useLive";

const line = (t: number): FeedLine => ({ t, speaker: "Демьян", text: `в ${t}`, id: t });

test("отметка — последняя реплика в миг, когда перестали смотреть; ответ двигает её", () => {
  const { result, rerender } = renderHook(({ lines, looking }) => useLastLook(lines, looking), {
    initialProps: { lines: [line(10)], looking: true },
  });
  expect(result.current()).toBeNull(); // ещё не уходили
  rerender({ lines: [line(10), line(40)], looking: false }); // свернули
  rerender({ lines: [line(10), line(40), line(90)], looking: true });
  expect(result.current()).toBe(40);
  expect(result.current()).toBe(90); // после «Что я пропустил?» — с ответа
});

test("уход из окна, пока смотрели, — тоже отметка; свёрнутая панель её не двигает", () => {
  const { result, rerender } = renderHook(({ lines, looking }) => useLastLook(lines, looking), {
    initialProps: { lines: [line(5)], looking: true },
  });
  act(() => { window.dispatchEvent(new Event("blur")); });
  rerender({ lines: [line(5), line(60)], looking: true });
  expect(result.current()).toBe(5);
  rerender({ lines: [line(5), line(60)], looking: false });
  rerender({ lines: [line(5), line(60), line(120)], looking: false });
  act(() => { window.dispatchEvent(new Event("blur")); });
  expect(result.current()).toBe(60);
});

test("useLiveAsk: свой вопрос, быстрые действия, since_t только у «Что я пропустил?»", async () => {
  const ask = vi.fn(async () => {});
  const live = { lines: [line(30)], ask } as unknown as Live;
  const { result } = renderHook(() => useLiveAsk(live, true));
  await result.current("кто за релиз?");
  await result.current("Что мне ответить?", "reply");
  await result.current("Что я пропустил?", "missed");
  expect(ask.mock.calls).toEqual([
    ["кто за релиз?"], ["", { quick: "reply" }], ["", { quick: "missed" }],
  ]);
});
