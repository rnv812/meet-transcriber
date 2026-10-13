import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Turn } from "../../lib/speakers";
import { CardTabs } from "./CardTabs";
import { TranscriptView } from "./TranscriptView";
import { FindAsk } from "./transcriptShown";

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

/** Лента с открытой строкой поиска (0.6.1: строку открывает «Найти» или Ctrl+F — просьба `FindAsk`). */
function setup(props: Partial<Parameters<typeof TranscriptView>[0]> = {}) {
  const onPlay = vi.fn();
  const view = render(
    <FindAsk.Provider value={1}>
      <TranscriptView turns={TURNS} colors={new Map()} playable onPlay={onPlay} {...props} />
    </FindAsk.Provider>);
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

test("строка поиска (0.6.1): поле Aurora с подсказкой «Ctrl F», ↑/↓ при запросе, «Закрыть поиск»", async () => {
  setup();
  const bar = screen.getByRole("search", { name: "Поиск по расшифровке" });
  expect(field()).toHaveClass("field", "field--md");
  expect(field().closest(".search")).not.toBeNull();
  expect(within(bar).getByText("Ctrl F")).toHaveClass("kbd");
  // Пока поле пустое, ↑/↓ не нужны; подсказка не наезжает на введённый текст.
  expect(within(bar).queryByRole("button", { name: "Следующее совпадение" })).toBeNull();
  await userEvent.type(field(), "бюджет");
  expect(within(bar).queryByText("Ctrl F")).toBeNull();
  expect(await within(bar).findByRole("button", { name: "Следующее совпадение" })).toBeInTheDocument();
  // «Исправить…» и «Улучшить» — в строке вкладок, не в строке поиска.
  expect(within(bar).queryByRole("button", { name: "Исправить распознанное" })).toBeNull();
  await userEvent.click(within(bar).getByRole("button", { name: "Закрыть поиск" }));
  expect(screen.queryByRole("searchbox", { name: "Найти в расшифровке" })).toBeNull();
});

test("строки поиска нет, пока её не попросили; просьба открывает её и ставит фокус в поле", () => {
  const props = { turns: TURNS, colors: new Map<string, string>(), playable: true, onPlay: () => {} };
  const { rerender } = render(<FindAsk.Provider value={0}><TranscriptView {...props} /></FindAsk.Provider>);
  expect(screen.queryByRole("search", { name: "Поиск по расшифровке" })).toBeNull();
  rerender(<FindAsk.Provider value={1}><TranscriptView {...props} /></FindAsk.Provider>);
  expect(field()).toHaveFocus();
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

test("Esc очищает поиск, закрывает строку и возвращает фокус туда, где он был", async () => {
  const { container } = setup();
  const before = screen.getAllByRole("button", { name: /^(Слушать|Прослушать) с / })[0]!;
  before.focus();
  await userEvent.type(field(), "бюджет");
  await vi.waitFor(() => expect(counter()).toHaveTextContent("1 из 3"));
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("searchbox", { name: "Найти в расшифровке" })).toBeNull();
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
  await userEvent.click(screen.getByRole("button", { name: "Слушать с 00:30" }));
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

test("инструменты «Расшифровки» (0.6.1) — в строке вкладок и только на ней; «Найти» открывает строку поиска", async () => {
  render(
    <CardTabs endpoint={{ base: "/api", token: null }} id="r" folder="C:/r" jobs={[]}
      tools={<button type="button">Улучшить расшифровку</button>}
      transcript={<TranscriptView turns={TURNS} colors={new Map()} playable onPlay={() => {}} />} />);
  const bar = document.querySelector<HTMLElement>(".card-tabs__bar")!;
  expect(within(bar).getByRole("button", { name: "Улучшить расшифровку" })).toBeInTheDocument();
  const find = within(bar).getByRole("button", { name: "Найти в расшифровке" });
  expect(find).toHaveAttribute("aria-keyshortcuts", "Control+F");
  await userEvent.click(find);
  expect(field()).toHaveFocus();
  await userEvent.click(screen.getByRole("tab", { name: "Итоги" }));
  expect(within(bar).queryByRole("button", { name: "Улучшить расшифровку" })).toBeNull();
  expect(within(bar).queryByRole("button", { name: "Найти в расшифровке" })).toBeNull();
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
  const before = screen.getAllByRole("button", { name: /^(Слушать|Прослушать) с / })[0]!;
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

test("переход из списка на «Итогах»: прокрутка к совпадению — когда «Расшифровка» уже видна", async () => {
  // Просьба может дойти до расшифровки раньше, чем вкладки переключатся (`show` — отдельно).
  const Card = ({ find, show }: { find: { q: string; t: number | null; n: number }; show: number }) => (
    <CardTabs endpoint={{ base: "/api", token: null }} id="r" folder="C:/r" jobs={[]} showTranscript={show}
      transcript={<TranscriptView turns={TURNS} colors={new Map()} playable onPlay={() => {}} find={find} />} />
  );
  const calls: { text: string | null; hidden: boolean }[] = [];
  const original = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
    calls.push({ text: this.textContent, hidden: this.closest("[hidden]") !== null });
  });
  try {
    const { rerender } = render(<Card find={{ q: "бюджет", t: 0, n: 1 }} show={1} />);
    await userEvent.click(screen.getByRole("tab", { name: "Итоги" }));
    calls.length = 0;
    rerender(<Card find={{ q: "бюджет", t: 30, n: 2 }} show={1} />);
    await vi.waitFor(() => expect(counter()).toHaveTextContent("2 из 3"));
    expect(calls).toEqual([]); // панель скрыта — прокручивать нечего
    rerender(<Card find={{ q: "бюджет", t: 30, n: 2 }} show={2} />);
    expect(screen.getByRole("tab", { name: "Расшифровка" })).toHaveAttribute("aria-selected", "true");
    await vi.waitFor(() => expect(calls).toEqual([{ text: "Бюджет", hidden: false }]));
    // Дальше вкладку переключают руками — прокрутка не прыгает к совпадению.
    await userEvent.click(screen.getByRole("tab", { name: "Итоги" }));
    await userEvent.click(screen.getByRole("tab", { name: "Расшифровка" }));
    expect(calls).toHaveLength(1);
  } finally {
    Element.prototype.scrollIntoView = original;
  }
});
