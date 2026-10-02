import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { LiveHint, LiveSummary } from "../lib/types";
import { LiveWorkspace, useLiveView } from "./LiveWorkspace";
import { FRESH_MS } from "./useAttention";
import type { Live } from "./useLive";

const summary = (o: Partial<LiveSummary> = {}): LiveSummary => ({
  topic: "Интеграция платежей", points: [{ id: "p1", text: "Партнёр готов к тестам" }],
  decisions: [{ id: "d1", text: "Стенд к пятнице" }], tasks: [{ id: "t1", who: "Ольга", what: "Выдать доступы", due: "среда" }],
  open_questions: [], ...o,
});
const hint = (o: Partial<LiveHint> = {}): LiveHint => ({
  id: "h1", kind: "risk", text: "У миграции нет ответственного", why: "срок назван, владельца нет", source_t: 125,
  ref: null, pinned: false, dismissed: false, created_at: 1, updated_at: 1, ...o,
});
const lines = [
  { t: 60, speaker: "Ольга", text: "начнём с миграции", id: 0 },
  { t: 125, speaker: "Игорь", text: "миграцию сделаем к пятнице", id: 1 },
];

function makeLive(o: Partial<Live> = {}): Live {
  return {
    status: null, lines, digest: "", summary: summary(), hints: [hint()], hintsEnabled: true, quietDefault: false, qa: [],
    loaded: true, error: null, asking: false, askError: null, hintError: null,
    ask: vi.fn(async () => {}), hint: vi.fn(async () => {}), setTask: vi.fn(async () => {}), ...o,
  };
}

function Host({ live, wide = false, quiet = false, open = true }: { live: Live; wide?: boolean; quiet?: boolean; open?: boolean }) {
  const view = useLiveView(live, { open, wide, quiet });
  return <LiveWorkspace live={live} view={view} onAsk={(q) => live.ask(q)} />;
}

afterEach(() => vi.useRealTimers());

test("узкая: четыре вкладки, по умолчанию лента; вкладки переключают содержимое", async () => {
  render(<Host live={makeLive()} />);
  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((t) => t.textContent)).toEqual(["Лента", "Сводка", "Подсказки", "Спросить"]);
  expect(screen.getByRole("tab", { name: "Лента" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("log")).toHaveTextContent("начнём с миграции");
  await userEvent.click(screen.getByRole("tab", { name: "Сводка" }));
  expect(screen.getByText("Интеграция платежей")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Задачи" })).toHaveTextContent("Ольга Выдать доступы · срок: среда");
  await userEvent.click(screen.getByRole("tab", { name: "Подсказки" }));
  expect(screen.getByText("У миграции нет ответственного")).toBeInTheDocument();
  expect(screen.getByText("Риск или неясность")).toBeInTheDocument();
  expect(screen.queryByRole("log")).toBeNull();
});

test("широкая: две колонки — лента слева, сводка и подсказки справа, «Спросить» внизу; вкладок нет", () => {
  render(<Host live={makeLive()} wide />);
  expect(screen.queryByRole("tablist")).toBeNull();
  expect(screen.getByRole("log")).toBeInTheDocument();
  const side = screen.getByRole("region", { name: "Сводка" }).parentElement!;
  const panes = within(side).getAllByRole("region").filter((r) => r.parentElement === side);
  expect(panes.map((r) => r.getAttribute("aria-label"))).toEqual(["Сводка", "Подсказки", "Спросить"]);
  expect(within(panes[2]!).getByRole("textbox", { name: "Вопрос ассистенту" })).toBeInTheDocument();
});

test("счётчики нового: появились подсказки — счётчик на вкладке; открыли вкладку — счётчик ушёл", async () => {
  const live = makeLive({ hints: [] });
  const { rerender } = render(<Host live={live} />);
  expect(screen.getByRole("tab", { name: "Подсказки" }).textContent).toBe("Подсказки");
  rerender(<Host live={{ ...live, hints: [hint(), hint({ id: "h2", kind: "question", text: "Спросить про бюджет" })] }} />);
  expect(within(screen.getByRole("tab", { name: /Подсказки/ })).getByLabelText("новых: 2")).toHaveTextContent("2");
  await userEvent.click(screen.getByRole("tab", { name: /Подсказки/ }));
  expect(screen.getByRole("tab", { name: "Подсказки" }).textContent).toBe("Подсказки");
  await userEvent.click(screen.getByRole("tab", { name: "Лента" }));
  expect(screen.getByRole("tab", { name: "Подсказки" }).textContent).toBe("Подсказки"); // увиденное не «новое»
});

test("состояние, с которым открыли панель, новым не считается", () => {
  render(<Host live={makeLive({ hints: [hint(), hint({ id: "h2", text: "Ещё одна подсказка" })] })} />);
  expect(screen.queryByLabelText(/новых:/)).toBeNull();
});

test("ответ на вопрос, пока вкладка «Спросить» не на экране, — счётчик у неё", () => {
  const live = makeLive({ qa: [{ id: 1, q: "срок?", a: null, error: null, pending: true, at: 1, quick: null }] });
  const { rerender } = render(<Host live={live} />);
  rerender(<Host live={{ ...live, qa: [{ id: 1, q: "срок?", a: "пятница", error: null, pending: false, at: 1, quick: null }] }} />);
  expect(within(screen.getByRole("tab", { name: /Спросить/ })).getByLabelText("новых: 1")).toBeInTheDocument();
});

test("новое и изменённое подсвечено 6 секунд; «Не отвлекать» — без подсветки и счётчиков", async () => {
  vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
  const live = makeLive();
  const { rerender } = render(<Host live={live} wide />);
  const changed = { ...live, hints: [hint({ text: "У миграции нет ответственного и срока" })] };
  rerender(<Host live={changed} wide />);
  expect(screen.getByText("У миграции нет ответственного и срока").closest("li")).toHaveClass("is-fresh");
  expect(screen.getByText("Партнёр готов к тестам")).not.toHaveClass("is-fresh");
  act(() => { vi.advanceTimersByTime(FRESH_MS); });
  expect(screen.getByText("У миграции нет ответственного и срока").closest("li")).not.toHaveClass("is-fresh");

  rerender(<Host live={{ ...changed, summary: summary({ points: [{ id: "p1", text: "Партнёр готов с понедельника" }] }) }} wide quiet />);
  expect(screen.getByText("Партнёр готов с понедельника")).not.toHaveClass("is-fresh");
});

test("«Не отвлекать»: счётчиков на вкладках нет", () => {
  const live = makeLive({ hints: [] });
  const { rerender } = render(<Host live={live} quiet />);
  rerender(<Host live={{ ...live, hints: [hint()] }} quiet />);
  expect(screen.queryByLabelText(/новых:/)).toBeNull();
});

test("действия подсказки: закрепить, скрыть, копировать", async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  const live = makeLive({ hints: [hint(), hint({ id: "h2", pinned: true, text: "Закреплённая" })] });
  render(<Host live={live} wide />);
  const [first, second] = within(screen.getByRole("list", { name: "Подсказки" })).getAllByRole("listitem");
  await userEvent.click(within(first!).getByRole("button", { name: "Закрепить" }));
  expect(live.hint).toHaveBeenLastCalledWith("h1", "pin");
  expect(within(second!).getByRole("button", { name: "Открепить" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(within(second!).getByRole("button", { name: "Открепить" }));
  expect(live.hint).toHaveBeenLastCalledWith("h2", "unpin");
  await userEvent.click(within(first!).getByRole("button", { name: "Скрыть" }));
  expect(live.hint).toHaveBeenLastCalledWith("h1", "dismiss");
  await userEvent.click(within(first!).getByRole("button", { name: "Копировать" }));
  expect(writeText).toHaveBeenCalledWith("У миграции нет ответственного");
  expect(await within(first!).findByText("Скопировано")).toBeInTheDocument();
});

test("«Спросить об этом» подставляет вопрос и открывает «Спросить», не отправляя", async () => {
  const live = makeLive();
  render(<Host live={live} />);
  await userEvent.click(screen.getByRole("tab", { name: "Подсказки" }));
  await userEvent.click(screen.getByRole("button", { name: "Спросить об этом" }));
  expect(screen.getByRole("tab", { name: "Спросить" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("textbox", { name: "Вопрос ассистенту" }))
    .toHaveValue("Расскажите подробнее: «У миграции нет ответственного»");
  expect(live.ask).not.toHaveBeenCalled();
});

test("момент подсказки ведёт к реплике в ленте", async () => {
  render(<Host live={makeLive()} />);
  await userEvent.click(screen.getByRole("tab", { name: "Подсказки" }));
  await userEvent.click(screen.getByRole("button", { name: "Момент 02:05" }));
  expect(screen.getByRole("tab", { name: "Лента" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("миграцию сделаем к пятнице").closest("li")).toHaveClass("is-target");
});

test("термин показывает файл базы знаний; режим «Только сводка» — пояснение вместо подсказок", async () => {
  const { rerender } = render(<Host live={makeLive({ hints: [hint({ kind: "term", text: "Шлюз — сервис платежей", ref: "Проекты/Шлюз.md" })] })} wide />);
  expect(screen.getByText("База знаний: Проекты/Шлюз.md")).toBeInTheDocument();
  expect(screen.getByText("Термин")).toBeInTheDocument();
  rerender(<Host live={makeLive({ hints: [], hintsEnabled: false })} wide />);
  expect(screen.getByText(/Подсказки выключены/)).toBeInTheDocument();
});

test("пустые сводка и подсказки — спокойные пояснения", () => {
  render(<Host live={makeLive({ hints: [], summary: { topic: "", points: [], decisions: [], tasks: [], open_questions: [] } })} wide />);
  expect(screen.getByText(/Сводка появится/)).toBeInTheDocument();
  expect(screen.getByText(/Подсказки появятся/)).toBeInTheDocument();
});

test("вкладки с клавиатуры: стрелки по кругу, Home/End; в порядке Tab — только выбранная", async () => {
  render(<Host live={makeLive()} />);
  const tab = (name: string) => screen.getByRole("tab", { name });
  expect(tab("Лента")).toHaveAttribute("tabindex", "0");
  expect(tab("Сводка")).toHaveAttribute("tabindex", "-1");
  await userEvent.click(tab("Лента"));
  await userEvent.keyboard("{ArrowRight}");
  expect(tab("Сводка")).toHaveAttribute("aria-selected", "true");
  expect(tab("Сводка")).toHaveFocus();
  await userEvent.keyboard("{End}");
  expect(tab("Спросить")).toHaveAttribute("aria-selected", "true");
  await userEvent.keyboard("{ArrowRight}");
  expect(tab("Лента")).toHaveAttribute("aria-selected", "true");
  await userEvent.keyboard("{ArrowLeft}");
  expect(tab("Спросить")).toHaveFocus();
  await userEvent.keyboard("{Home}");
  expect(tab("Лента")).toHaveAttribute("aria-selected", "true");
});

test("действие с подсказкой не дошло — ошибка у этой подсказки", () => {
  render(<Host live={makeLive({ hints: [hint(), hint({ id: "h2", text: "Другая" })], hintError: { id: "h2", text: "Ассистент не запущен" } })} wide />);
  const items = within(screen.getByRole("list", { name: "Подсказки" })).getAllByRole("listitem");
  expect(within(items[1]!).getByRole("alert")).toHaveTextContent("Ассистент не запущен");
  expect(within(items[0]!).queryByRole("alert")).toBeNull();
});
