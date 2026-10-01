import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Turn } from "../../lib/speakers";
import { CardTabs } from "./CardTabs";
import { TranscriptView } from "./TranscriptView";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getAssistant: vi.fn(async () => ({ provider: null, setting: "auto", available: {}, knowledge_dir: null, checking: false })),
  getSummary: vi.fn(async () => { throw new Error("итогов нет"); }),
  getQa: vi.fn(async () => ({ items: [] })),
}));

const turn = (start: number, speaker: string, text: string): Turn =>
  ({ start, end: start + 1, speaker, texts: [text], uncertain: false });

const TURNS = [
  turn(0, "Анна", "Начнём с бюджета и задач."),
  turn(30, "Борис", "Бюджет утвердим завтра, бюджет большой."),
  turn(60, "Анна", "Вопросов нет."),
];

function setup(props: Partial<Parameters<typeof TranscriptView>[0]> = {}) {
  const onPlay = vi.fn();
  const view = render(
    <TranscriptView turns={TURNS} colors={new Map()} playable onPlay={onPlay} {...props} />);
  return { onPlay, ...view };
}

const field = () => screen.getByRole("searchbox", { name: "Найти в расшифровке" });
const counter = () => document.querySelector<HTMLElement>(".find__count[aria-live=\"polite\"]")!;
const current = (c: HTMLElement) => c.querySelector(".hit--current");

test("находит слова в любой форме, считает совпадения и подсвечивает их", async () => {
  const { container } = setup();
  await userEvent.type(field(), "бюджет");
  await vi.waitFor(() => expect(counter()).toHaveTextContent("1 из 3"));
  const marks = container.querySelectorAll("mark.hit");
  expect([...marks].map((m) => m.textContent)).toEqual(["бюджета", "Бюджет", "бюджет"]);
  expect(current(container)).toHaveTextContent("бюджета");
});

test("Enter и Shift+Enter, кнопки ↑/↓ — по кругу", async () => {
  const { container } = setup();
  await userEvent.type(field(), "бюджет");
  await vi.waitFor(() => expect(counter()).toHaveTextContent("1 из 3"));
  await userEvent.keyboard("{Enter}");
  expect(counter()).toHaveTextContent("2 из 3");
  expect(current(container)).toHaveTextContent("Бюджет");
  await userEvent.keyboard("{Shift>}{Enter}{/Shift}{Shift>}{Enter}{/Shift}");
  expect(counter()).toHaveTextContent("3 из 3");
  await userEvent.click(screen.getByRole("button", { name: "Следующее совпадение" }));
  expect(counter()).toHaveTextContent("1 из 3");
  await userEvent.click(screen.getByRole("button", { name: "Предыдущее совпадение" }));
  expect(counter()).toHaveTextContent("3 из 3");
  expect(current(container)!.textContent).toBe("бюджет");
});

test("ничего не нашлось — так и сказано; фраза в кавычках — точно", async () => {
  setup();
  await userEvent.type(field(), "отпуск");
  await vi.waitFor(() => expect(counter()).toHaveTextContent("Ничего не найдено"));
  await userEvent.clear(field());
  await userEvent.type(field(), "\"бюджет большой\"");
  await vi.waitFor(() => expect(counter()).toHaveTextContent("1 из 1"));
});

test("Esc очищает поиск и возвращает фокус туда, где он был", async () => {
  const { container } = setup();
  const before = screen.getAllByRole("button", { name: /▶/ })[0]!;
  before.focus();
  await userEvent.type(field(), "бюджет");
  await vi.waitFor(() => expect(counter()).toHaveTextContent("1 из 3"));
  await userEvent.keyboard("{Escape}");
  expect(field()).toHaveValue("");
  expect(counter()).toHaveTextContent("");
  expect(container.querySelector("mark")).toBeNull();
  expect(before).toHaveFocus();
});

test("спикер: — реплики спикера целиком, без подсветки слов", async () => {
  const { container } = setup();
  await userEvent.type(field(), "спикер:Анна");
  await vi.waitFor(() => expect(counter()).toHaveTextContent("1 из 2"));
  expect(container.querySelector("mark")).toBeNull();
  expect(current(container)).toHaveTextContent("Начнём с бюджета и задач.");
});

test("просьба из списка: запрос уже введён, текущее — в нужной реплике", async () => {
  const { container } = setup({ find: { q: "бюджет", t: 30, n: 1 } });
  expect(field()).toHaveValue("бюджет");
  await vi.waitFor(() => expect(counter()).toHaveTextContent("2 из 3"));
  expect(current(container)).toHaveTextContent("Бюджет");
});

test("таймкод найденной реплики по-прежнему перематывает запись", async () => {
  const { onPlay } = setup({ find: { q: "бюджет", t: null, n: 1 } });
  await userEvent.click(screen.getByRole("button", { name: "▶ 00:30" }));
  expect(onPlay).toHaveBeenCalledWith(TURNS[1]);
});

test("Ctrl+F с другой вкладки открывает «Расшифровку» и ставит фокус в поиск", async () => {
  render(
    <CardTabs endpoint={{ base: "/api", token: null }} id="r" folder="C:/r" jobs={[]}
      transcript={<TranscriptView turns={TURNS} colors={new Map()} playable onPlay={() => {}} />} />);
  await userEvent.click(screen.getByRole("tab", { name: "Итоги" }));
  fireEvent.keyDown(window, { key: "а", code: "KeyF", ctrlKey: true });
  expect(screen.getByRole("tab", { name: "Расшифровка" })).toHaveAttribute("aria-selected", "true");
  expect(field()).toHaveFocus();
});

test("вторая просьба из списка с тем же запросом переходит к другой реплике", async () => {
  const props = { turns: TURNS, colors: new Map<string, string>(), playable: true, onPlay: () => {} };
  const { container, rerender } = render(<TranscriptView {...props} find={{ q: "бюджет", t: 0, n: 1 }} />);
  await vi.waitFor(() => expect(counter()).toHaveTextContent("1 из 3"));
  rerender(<TranscriptView {...props} find={{ q: "бюджет", t: 30, n: 2 }} />);
  await vi.waitFor(() => expect(counter()).toHaveTextContent("2 из 3"));
  expect(current(container)).toHaveTextContent("Бюджет");
  // Та же реплика ещё раз (пользователь ушёл стрелками) — снова к ней.
  await userEvent.click(screen.getByRole("button", { name: "Следующее совпадение" }));
  expect(counter()).toHaveTextContent("3 из 3");
  rerender(<TranscriptView {...props} find={{ q: "бюджет", t: 30, n: 3 }} />);
  await vi.waitFor(() => expect(counter()).toHaveTextContent("2 из 3"));
});

const tabs = (show?: number) => (
  <CardTabs endpoint={{ base: "/api", token: null }} id="r" folder="C:/r" jobs={[]} showTranscript={show}
    transcript={<TranscriptView turns={TURNS} colors={new Map()} playable onPlay={() => {}} />} />
);

test("просьба из списка, пока открыты «Итоги», возвращает на «Расшифровку»", async () => {
  const { rerender } = render(tabs(1));
  await userEvent.click(screen.getByRole("tab", { name: "Итоги" }));
  rerender(tabs(1));
  expect(screen.getByRole("tab", { name: "Итоги" })).toHaveAttribute("aria-selected", "true");
  rerender(tabs(2));
  expect(screen.getByRole("tab", { name: "Расшифровка" })).toHaveAttribute("aria-selected", "true");
});

test("Ctrl+F в диалоге, всплывающем окне или поле переименования не уводит к поиску", async () => {
  render(<>
    {tabs()}
    <div role="dialog"><input aria-label="в диалоге" /></div>
    <input className="rec-item__input" aria-label="название в списке" />
  </>);
  await userEvent.click(screen.getByRole("tab", { name: "Итоги" }));
  for (const name of ["в диалоге", "название в списке"]) {
    const el = screen.getByRole("textbox", { name });
    el.focus();
    fireEvent.keyDown(el, { key: "f", code: "KeyF", ctrlKey: true });
    expect(screen.getByRole("tab", { name: "Итоги" })).toHaveAttribute("aria-selected", "true");
    expect(el).toHaveFocus();
  }
});

test("Esc после фокуса без источника не возвращает фокус на старое место", async () => {
  setup();
  const before = screen.getAllByRole("button", { name: /▶/ })[0]!;
  before.focus();
  await userEvent.type(field(), "бюджет"); // фокус пришёл с «▶»
  field().blur();
  fireEvent.focus(field(), { relatedTarget: null }); // а теперь — ниоткуда
  field().focus();
  await userEvent.keyboard("{Escape}");
  expect(before).not.toHaveFocus();
});

test("перерыв объединённой встречи — разделитель, не реплика и не находка поиска", async () => {
  const brk: Turn = { start: 30, end: 30, speaker: "", texts: ["— перерыв 15 мин —"], uncertain: false, kind: "break" };
  const { container } = setup({ turns: [TURNS[0]!, brk, TURNS[1]!] });
  const sep = screen.getByRole("separator", { name: "— перерыв 15 мин —" });
  expect(sep).toHaveTextContent("— перерыв 15 мин —");
  expect(container.querySelectorAll(".turn")).toHaveLength(2);
  await userEvent.type(field(), "перерыв");
  await vi.waitFor(() => expect(counter()).toHaveTextContent(/0|Не найдено|нет/i));
  expect(container.querySelectorAll("mark.hit")).toHaveLength(0);
});
