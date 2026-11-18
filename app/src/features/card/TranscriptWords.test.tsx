/**
 * Звучащее слово в «Расшифровке»: щелчок по слову играет с него, звучащее
 * слово подсвечено по времени плеера. Элементы слов — только у играющей
 * реплики и у той, по которой щёлкнули. Тексты выдуманные.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Turn } from "../../lib/speakers";
import type { SegmentWords } from "../../lib/words";
import { createPlayClock, PlayTime } from "./playClock";
import { TranscriptView } from "./TranscriptView";
import { FindAsk } from "./transcriptShown";

const TURNS: Turn[] = [
  { start: 0, end: 4, speaker: "Анна", texts: ["Начнём с бюджета."], uncertain: false, idx: [0] },
  // Реплика из двух сегментов: у второго (правленого) слов нет.
  { start: 12, end: 16, speaker: "Борис", texts: ["Склад готов к поставке.", "Остальное позже."], uncertain: false,
    idx: [1, 2] },
  { start: 30, end: 33, speaker: "Анна", texts: ["Вопросов нет."], uncertain: false, idx: [3] },
];
const WORDS: SegmentWords = [
  [[0, 0.8, " Начнём"], [0.8, 1.1, " с"], [1.1, 2, " бюджета."]],
  [[12, 12.3, " Склад"], [12.34, 12.9, " готов"], [12.9, 13.1, " к"], [13.1, 14, " поставке."]],
  null,
  [[30, 31, " Вопросов"], [31, 32, " нет."]],
];

function setup(props: Partial<Parameters<typeof TranscriptView>[0]> = {}, ask = 0) {
  const onPlay = vi.fn();
  const onPlayAt = vi.fn();
  const clock = createPlayClock();
  const ui = (p: Partial<Parameters<typeof TranscriptView>[0]>) => (
    <PlayTime.Provider value={clock}>
      <FindAsk.Provider value={ask}>
        <TranscriptView turns={TURNS} colors={new Map()} playable onPlay={onPlay} onPlayAt={onPlayAt}
          words={WORDS} {...props} {...p} />
      </FindAsk.Provider>
    </PlayTime.Provider>
  );
  const view = render(ui({}));
  return { onPlay, onPlayAt, clock, ...view, rerender: (p: Partial<Parameters<typeof TranscriptView>[0]>) => view.rerender(ui(p)) };
}

const turnEl = (i: number) => document.querySelector<HTMLElement>(`[data-turn="${i}"]`)!;
const textOf = (i: number) => turnEl(i).querySelector<HTMLElement>(".turn__text")!;
const wordsOf = (i: number) => [...turnEl(i).querySelectorAll<HTMLElement>("[data-w]")];
const word = (i: number, text: string) => wordsOf(i).find((w) => w.textContent === text)!;

afterEach(() => { window.getSelection()?.removeAllRanges(); });

test("элементы слов — только у играющей реплики; у остальных текст как был", () => {
  setup({ nowTurn: 1 });
  expect(wordsOf(1).map((w) => w.textContent)).toEqual(["Склад", "готов", "к", "поставке."]);
  // Текст реплики не меняется: правка и разделение считают позиции по нему.
  expect(textOf(1)).toHaveTextContent("Склад готов к поставке. Остальное позже.");
  expect(wordsOf(0)).toEqual([]);
  expect(wordsOf(2)).toEqual([]);
  // Слова — не кнопки и не точки Tab: клавиатура по-прежнему играет реплику с начала.
  expect(wordsOf(1).every((w) => !w.hasAttribute("tabindex") && w.tagName === "SPAN")).toBe(true);
});

test("щелчок по слову на 12,34 с — играть с него", async () => {
  const { onPlayAt, onPlay } = setup({ nowTurn: 1 });
  await userEvent.click(word(1, "готов"));
  expect(onPlayAt).toHaveBeenCalledWith(12.34);
  expect(onPlay).not.toHaveBeenCalled();
});

test("щелчок по реплике, которая не играет: слово под указателем, у реплики появляются слова", async () => {
  const { onPlayAt } = setup({ nowTurn: 0 });
  const node = textOf(2).firstChild!;
  // Место под указателем — 10-й символ «Вопросов нет.», внутри слова «нет.».
  // jsdom места под указателем не знает — как в браузере (WebKit, Chromium).
  const doc = document as unknown as { caretRangeFromPoint?: (x: number, y: number) => Range | null };
  doc.caretRangeFromPoint = () => {
    const r = document.createRange();
    r.setStart(node, 10);
    return r;
  };
  try {
    await userEvent.click(textOf(2));
  } finally {
    delete doc.caretRangeFromPoint;
  }
  expect(onPlayAt).toHaveBeenCalledWith(31);
  expect(wordsOf(2).map((w) => w.textContent)).toEqual(["Вопросов", "нет."]);
  // Играющая реплика свои слова сохраняет.
  expect(wordsOf(0)).toHaveLength(3);
});

test("выделили текст в реплике и отпустили мышь — воспроизведение не началось", () => {
  const { onPlayAt } = setup({ nowTurn: 1 });
  const target = word(1, "готов");
  window.getSelection()!.selectAllChildren(target);
  fireEvent.mouseUp(target);
  fireEvent.click(target);
  expect(onPlayAt).not.toHaveBeenCalled();
});

test("реплика в правке — щелчок по слову не играет; правку закрыли — играет", async () => {
  const { onPlayAt, rerender } = setup({ nowTurn: 1, editingTurn: 1 });
  await userEvent.click(word(1, "готов"));
  expect(onPlayAt).not.toHaveBeenCalled();
  rerender({ nowTurn: 1, editingTurn: null });
  await userEvent.click(word(1, "готов"));
  expect(onPlayAt).toHaveBeenCalledWith(12.34);
});

test("Ctrl+щелчок по слову — выбор реплики, не воспроизведение", async () => {
  const onSelect = vi.fn();
  const { onPlayAt } = setup({ nowTurn: 1, onSelect });
  fireEvent.click(word(1, "готов"), { ctrlKey: true });
  expect(onPlayAt).not.toHaveBeenCalled();
  expect(onSelect).toHaveBeenCalledWith(1, "toggle");
});

test("время плеера 12,5 с — подсвечено слово, покрывающее 12,5; дальше — следующее", () => {
  const { clock } = setup({ nowTurn: 1 });
  act(() => clock.set(12.5));
  expect(turnEl(1).querySelectorAll("[data-word-now]")).toHaveLength(1);
  expect(word(1, "готов")).toHaveAttribute("data-word-now");
  act(() => clock.set(13.5));
  expect(word(1, "готов")).not.toHaveAttribute("data-word-now");
  expect(word(1, "поставке.")).toHaveAttribute("data-word-now");
  // Отметка «сейчас играет» у реплики остаётся.
  expect(turnEl(1)).toHaveAttribute("data-now", "true");
});

test("время уже идёт, когда реплика стала играющей, — слово подсвечено сразу", () => {
  const { clock, rerender } = setup({ nowTurn: 0 });
  act(() => clock.set(12.5));
  rerender({ nowTurn: 1 });
  expect(word(1, "готов")).toHaveAttribute("data-word-now");
});

test("найденное поиском слово сохраняет подсветку поиска", async () => {
  const { clock } = setup({ nowTurn: 1 }, 1);
  await userEvent.type(screen.getByRole("searchbox", { name: "Найти в расшифровке" }), "склад");
  await vi.waitFor(() => expect(textOf(1).querySelector("mark.hit")).not.toBeNull());
  act(() => clock.set(12.1));
  const hit = textOf(1).querySelector("mark.hit")!;
  expect(hit).toHaveTextContent("Склад");
  expect(hit.querySelector("[data-word-now]")).toHaveTextContent("Склад");
  expect(hit).toHaveClass("hit--current");
});

test("сегмент без слов — реплика подсвечена, как сейчас: без слова", () => {
  const { clock } = setup({ nowTurn: 1 });
  act(() => clock.set(15));
  expect(turnEl(1)).toHaveAttribute("data-now", "true");
  expect(document.querySelector("[data-word-now]")).toBeNull();
  // Текст правленого сегмента — без элементов слов.
  expect(wordsOf(1).some((w) => w.textContent?.includes("Остальное"))).toBe(false);
});

test("слова ещё не загрузились или не пришли — реплики как раньше", async () => {
  const { onPlayAt } = setup({ nowTurn: 1, words: null });
  expect(document.querySelector("[data-w]")).toBeNull();
  await userEvent.click(textOf(1));
  expect(onPlayAt).not.toHaveBeenCalled();
});

test("клавиатура: время реплики — играть с её начала", async () => {
  const { onPlay, onPlayAt } = setup({ nowTurn: 1 });
  screen.getByRole("button", { name: "Слушать с 00:12" }).focus();
  await userEvent.keyboard("{Enter}");
  expect(onPlay).toHaveBeenCalledWith(TURNS[1]);
  expect(onPlayAt).not.toHaveBeenCalled();
});
