import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { LiveHint, LiveSummary } from "../lib/types";
import { LIVE_PANES, type LivePlace, LiveWorkspace, UNDO_MS, useLiveView } from "./LiveWorkspace";
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
    status: null, lines, digest: "", summary: summary(), hints: [hint()], hintsEnabled: true, quietDefault: false, catchup: null, qa: [],
    loaded: true, error: null, asking: false, askError: null, hintError: null,
    ask: vi.fn(async () => {}), hint: vi.fn(async () => {}), setTask: vi.fn(async () => {}), ...o,
  };
}

function Host({ live, wide = false, quiet = false, open = true, onAskHint, place }: {
  live: Live; wide?: boolean; quiet?: boolean; open?: boolean; onAskHint?: (h: LiveHint) => void; place?: LivePlace;
}) {
  const view = useLiveView(live, { open, wide, quiet });
  return <LiveWorkspace live={live} view={view} onAsk={(q) => live.ask(q)} onAskHint={onAskHint} place={place} />;
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

test("широкая: две колонки — лента слева, справа подсказки (первыми), сводка и «Спросить» внизу; вкладок нет", () => {
  render(<Host live={makeLive()} wide />);
  expect(screen.queryByRole("tablist")).toBeNull();
  expect(screen.getByRole("log")).toBeInTheDocument();
  const side = screen.getByRole("region", { name: "Сводка" }).parentElement!;
  const panes = within(side).getAllByRole("region").filter((r) => r.parentElement === side);
  expect(panes.map((r) => r.getAttribute("aria-label"))).toEqual(["Подсказки", "Сводка", "Спросить"]);
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
  await userEvent.click(within(first!).getByRole("button", { name: "Копировать" }));
  expect(writeText).toHaveBeenCalledWith("У миграции нет ответственного");
  expect(await within(first!).findByText("Скопировано")).toBeInTheDocument();
});

test("«Скрыть» уходит ассистенту сразу; «Вернуть» — с фокусом — возвращает её (restore)", async () => {
  const live = makeLive({ hint: vi.fn(async () => true) });
  render(<Host live={live} wide />);
  const list = screen.getByRole("list", { name: "Подсказки" });
  await userEvent.click(within(list).getByRole("button", { name: "Скрыть" }));
  // Сразу — ассистент запоминает текст («не предлагать снова») в тот же момент.
  expect(live.hint).toHaveBeenCalledWith("h1", "dismiss");
  expect(screen.getByText("Подсказка скрыта")).toBeInTheDocument();
  const back = await screen.findByRole("button", { name: "Вернуть" });
  expect(back).toHaveFocus(); // кнопка «Скрыть» ушла с карточкой — фокус не потерян
  await userEvent.click(back);
  expect(live.hint).toHaveBeenLastCalledWith("h1", "restore");
  expect(screen.queryByRole("button", { name: "Вернуть" })).toBeNull();
});

test("подсказку уже убрал ассистент — «Вернуть» не предлагается", async () => {
  const live = makeLive({ hint: vi.fn(async () => false) });
  render(<Host live={live} wide />);
  await userEvent.click(within(screen.getByRole("list", { name: "Подсказки" })).getByRole("button", { name: "Скрыть" }));
  await vi.waitFor(() => expect(screen.queryByRole("button", { name: "Вернуть" })).toBeNull());
});

test("скрытие не дошло — «Вернуть» не предлагается", async () => {
  const live = makeLive({ hint: vi.fn(async () => undefined) });
  render(<Host live={live} wide />);
  await userEvent.click(within(screen.getByRole("list", { name: "Подсказки" })).getByRole("button", { name: "Скрыть" }));
  await vi.waitFor(() => expect(screen.queryByRole("button", { name: "Вернуть" })).toBeNull());
});

test("широкая: вопрос не дошёл — «Спросить» развёрнут, ошибку видно", () => {
  render(<Host live={makeLive({ askError: "Ассистент не отвечает" })} wide />);
  const ask = screen.getByRole("region", { name: "Спросить" });
  expect(ask).not.toHaveClass("live-ws__ask--compact");
  expect(within(ask).getByRole("alert")).toHaveTextContent("Ассистент не отвечает");
});

test("«Вернуть» — только несколько секунд", () => {
  vi.useFakeTimers();
  const live = makeLive({ hint: vi.fn(async () => true) });
  render(<Host live={live} wide />);
  act(() => { within(screen.getByRole("list", { name: "Подсказки" })).getByRole("button", { name: "Скрыть" }).click(); });
  expect(screen.getByRole("button", { name: "Вернуть" })).toBeInTheDocument();
  act(() => { vi.advanceTimersByTime(UNDO_MS); });
  expect(screen.queryByRole("button", { name: "Вернуть" })).toBeNull();
  expect(live.hint).toHaveBeenCalledTimes(1);
});

test("широкая: новый «Вам вопрос» прокручивается в поле зрения", () => {
  const scroll = vi.fn();
  const original = HTMLElement.prototype.scrollIntoView;
  HTMLElement.prototype.scrollIntoView = scroll;
  try {
    const live = makeLive();
    const { rerender } = render(<Host live={live} wide />);
    expect(scroll).not.toHaveBeenCalled();
    const urgent = hint({ id: "h9", kind: "ask_you", text: "Вас спросили про сроки", reply: "К пятнице" });
    rerender(<Host live={{ ...live, hints: [hint(), urgent] }} wide />);
    expect(scroll).toHaveBeenCalledTimes(1);
    const first = within(screen.getByRole("list", { name: "Подсказки" })).getAllByRole("listitem")[0]!;
    expect(first).toHaveTextContent("Вас спросили про сроки");
    expect(scroll.mock.contexts[0]).toBe(first);
  } finally {
    HTMLElement.prototype.scrollIntoView = original;
  }
});

test("новый «Вам вопрос» объявляется экранному диктору, прежний — нет", () => {
  const urgent = hint({ id: "h9", kind: "ask_you", text: "Вас спросили про сроки", reply: "К пятнице" });
  const live = makeLive({ hints: [hint(), urgent] });
  const { rerender } = render(<Host live={live} wide />);
  // Уже был при открытии (восстановлен) — не объявляется.
  expect(screen.getByRole("alert")).toHaveTextContent("");
  const next = hint({ id: "h10", kind: "ask_you", text: "Вас спросили про бюджет", reply: "Уточню" });
  rerender(<Host live={{ ...live, hints: [hint(), next] }} wide />);
  expect(screen.getByRole("alert")).toHaveTextContent("Вам вопрос: Вас спросили про бюджет");
});

test("широкая: «Спросить» в одну строку, пока им не пользуются; фокус — разворачивает", async () => {
  const live = makeLive({ qa: [{ id: 1, q: "Что я пропустил?", a: "Ничего важного", pending: false, at: 1, quick: null, error: null }] });
  render(<Host live={live} wide />);
  const ask = screen.getByRole("region", { name: "Спросить" });
  expect(ask).toHaveClass("live-ws__ask--compact");
  await userEvent.click(within(ask).getByRole("textbox", { name: "Вопрос ассистенту" }));
  expect(ask).not.toHaveClass("live-ws__ask--compact");
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

test("в карточке «Спросить об этом» уходит агенту (onAskHint), поле вопроса не трогается", async () => {
  const live = makeLive();
  const onAskHint = vi.fn();
  render(<Host live={live} onAskHint={onAskHint} />);
  await userEvent.click(screen.getByRole("tab", { name: "Подсказки" }));
  const ask = screen.getByRole("button", { name: "Спросить об этом" });
  expect(ask).toHaveAttribute("title", expect.stringContaining("агента"));
  await userEvent.click(ask);
  expect(onAskHint).toHaveBeenCalledWith(expect.objectContaining({ id: "h1", text: "У миграции нет ответственного" }));
  expect(screen.getByRole("tab", { name: "Подсказки" })).toHaveAttribute("aria-selected", "true");
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

const urgent = (o: Partial<LiveHint> = {}) => hint({
  id: "h9", kind: "ask_you", text: "Ольга спрашивает, готов ли отчёт к четвергу", why: "ждут ответа",
  reply: "Отчёт будет к четвергу, черновик пришлю завтра.", source_t: 300, created_at: 5, updated_at: 5, ...o,
});

test("«Вам вопрос» — наверху «Подсказок», с черновиком ответа; «Спросить агента» задаёт вопрос", async () => {
  const live = makeLive({ hints: [hint(), urgent()] });
  render(<Host live={live} />);
  await userEvent.click(screen.getByRole("tab", { name: "Подсказки" }));
  const items = screen.getAllByRole("listitem");
  expect(items[0]).toHaveClass("live-hint--ask_you");
  expect(within(items[0]!).getByText("Вам вопрос")).toBeInTheDocument();
  expect(within(items[0]!).getByText("Что ответить:")).toBeInTheDocument();
  expect(within(items[0]!).getByText("Отчёт будет к четвергу, черновик пришлю завтра.")).toBeInTheDocument();
  await userEvent.click(within(items[0]!).getByRole("button", { name: "Спросить агента" }));
  expect(live.ask).toHaveBeenCalledWith(expect.stringContaining("Ольга спрашивает, готов ли отчёт"));
  expect(screen.getByRole("tab", { name: "Спросить" })).toHaveAttribute("aria-selected", "true");
});

test("«Вам вопрос» появляется со всплеском, а в «Не отвлекать» — без анимации, но всё равно наверху", () => {
  const live = makeLive();
  const { rerender, unmount } = render(<Host live={live} wide />);
  rerender(<Host live={{ ...live, hints: [hint(), urgent()] }} wide />);
  const first = () => screen.getAllByRole("listitem").find((li) => li.classList.contains("live-hint"))!;
  expect(first()).toHaveClass("live-hint--ask_you", "is-urgent-new");
  unmount();
  const quietRender = render(<Host live={live} wide quiet />);
  quietRender.rerender(<Host live={{ ...live, hints: [hint(), urgent()] }} wide quiet />);
  expect(first()).toHaveClass("live-hint--ask_you");
  expect(first()).not.toHaveClass("is-urgent-new");
});

test("в карточке «Спросить агента» у «Вам вопрос» уходит агенту (onAskHint)", async () => {
  const live = makeLive({ hints: [urgent()] });
  const onAskHint = vi.fn();
  render(<Host live={live} onAskHint={onAskHint} />);
  await userEvent.click(screen.getByRole("tab", { name: "Подсказки" }));
  await userEvent.click(screen.getByRole("button", { name: "Спросить агента" }));
  expect(onAskHint).toHaveBeenCalledWith(expect.objectContaining({ id: "h9" }));
  expect(live.ask).not.toHaveBeenCalled();
});

/**
 * Раскладка широкой области (jsdom не раскладывает): ширина области 1000,
 * высота правой колонки 600; панели — своих размеров по CSS.
 */
function layout({ room = 1000, column = 600 } = {}) {
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get")
    .mockImplementation(function (this: HTMLElement) { return this.classList.contains("live-ws--wide") ? room : 0; });
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get")
    .mockImplementation(function (this: HTMLElement) { return this.classList.contains("live-ws__side") ? column : 0; });
  const size = (el: HTMLElement) => el.classList.contains("live-ws__side") ? 400
    : el.classList.contains("live-ws__pane--summary") ? 200
      : el.classList.contains("live-ws__ask") ? (el.classList.contains("live-ws__ask--compact") ? 40 : 150) : 0;
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (this: HTMLElement) { return size(this); });
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (this: HTMLElement) { return size(this); });
}

const ws = () => document.querySelector<HTMLElement>(".live-ws")!;
const side = () => document.querySelector<HTMLElement>(".live-ws__side")!;
const split = (name: string) => screen.getByRole("separator", { name });

describe("размеры областей широкой раскладки", () => {
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  test("узкая (вкладки) — без разделителей; широкая — ширина колонки и высота сводки, по CSS, пока не тянули", () => {
    layout();
    const { rerender } = render(<Host live={makeLive()} />);
    expect(screen.queryByRole("separator")).toBeNull();
    rerender(<Host live={makeLive()} wide />);
    const col = split("Ширина колонки подсказок");
    expect(col).toHaveAttribute("aria-orientation", "vertical");
    expect(col).toHaveAttribute("aria-valuenow", "400");
    expect(col).toHaveAttribute("aria-valuemin", String(LIVE_PANES.side.min));
    // Ленте остаётся не меньше её минимума.
    expect(col).toHaveAttribute("aria-valuemax", String(1000 - LIVE_PANES.feedMin - 12));
    const sum = split("Высота сводки");
    expect(sum).toHaveAttribute("aria-orientation", "horizontal");
    expect(sum).toHaveAttribute("aria-valuenow", "200");
    expect(ws().style.getPropertyValue("--live-side")).toBe("");
    expect(side().style.getPropertyValue("--live-summary")).toBe("");
  });

  test("ширина колонки: клавиши и мышь, пределы; запоминается и возвращается после перезапуска", async () => {
    layout();
    const { unmount } = render(<Host live={makeLive()} wide />);
    const col = split("Ширина колонки подсказок");
    fireEvent.keyDown(col, { key: "ArrowLeft" }); // колонка справа: ← — шире
    expect(ws().style.getPropertyValue("--live-side")).toBe("416px");
    expect(localStorage.getItem("meet.pane.live-side")).toBe("416");
    fireEvent.pointerDown(col, { button: 0, clientX: 500, pointerId: 1 });
    fireEvent.pointerMove(col, { clientX: 2000, pointerId: 1 }); // вправо до упора — уже, не уже минимума
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    fireEvent.pointerUp(col, { clientX: 2000, pointerId: 1 });
    expect(ws().style.getPropertyValue("--live-side")).toBe(`${LIVE_PANES.side.min}px`);
    expect(localStorage.getItem("meet.pane.live-side")).toBe(String(LIVE_PANES.side.min));
    unmount();

    render(<Host live={makeLive()} wide />);
    expect(ws().style.getPropertyValue("--live-side")).toBe(`${LIVE_PANES.side.min}px`);
  });

  test("двойной щелчок — снова как по умолчанию: переменная и запомненное снимаются", () => {
    layout();
    localStorage.setItem("meet.pane.live-side", "500");
    localStorage.setItem("meet.pane.live-summary", "300");
    render(<Host live={makeLive()} wide />);
    expect(ws().style.getPropertyValue("--live-side")).toBe("500px");
    expect(side().style.getPropertyValue("--live-summary")).toBe("0 1 300px");
    fireEvent.doubleClick(split("Ширина колонки подсказок"));
    fireEvent.doubleClick(split("Высота сводки"));
    expect(ws().style.getPropertyValue("--live-side")).toBe("");
    expect(side().style.getPropertyValue("--live-summary")).toBe("");
    expect(localStorage.getItem("meet.pane.live-side")).toBeNull();
    expect(localStorage.getItem("meet.pane.live-summary")).toBeNull();
  });

  test("запомненное в маленьком окне ужимается: лента и подсказки не схлопываются", () => {
    localStorage.setItem("meet.pane.live-side", "900");
    localStorage.setItem("meet.pane.live-summary", "900");
    layout({ room: 720, column: 360 });
    render(<Host live={makeLive()} wide />);
    expect(ws().style.getPropertyValue("--live-side")).toBe(`${720 - LIVE_PANES.feedMin - 12}px`);
    // Подсказкам — их минимум, «Спросить» — сколько занимает (одна строка), и промежутки.
    expect(side().style.getPropertyValue("--live-summary")).toBe(`0 1 ${360 - LIVE_PANES.hintsMin - 40 - 20}px`);
    // Запомненное — пожелание: окно снова больше — размеры вернутся.
    expect(localStorage.getItem("meet.pane.live-side")).toBe("900");
  });

  test("высота сводки: ↑ — выше (сводка под разделителем), Home — к минимуму", () => {
    layout();
    render(<Host live={makeLive()} wide />);
    const sum = split("Высота сводки");
    fireEvent.keyDown(sum, { key: "ArrowUp" });
    expect(side().style.getPropertyValue("--live-summary")).toBe("0 1 216px");
    expect(localStorage.getItem("meet.pane.live-summary")).toBe("216");
    fireEvent.keyDown(sum, { key: "Home" });
    expect(side().style.getPropertyValue("--live-summary")).toBe(`0 1 ${LIVE_PANES.summary.min}px`);
  });

  test("высота «Спросить»: разделитель — когда «Спросить» развёрнут; нажатие на него «Спросить» не сворачивает", async () => {
    layout();
    render(<Host live={makeLive()} wide />);
    expect(screen.queryByRole("separator", { name: "Высота «Спросить»" })).toBeNull();
    await userEvent.click(screen.getByRole("textbox", { name: "Вопрос ассистенту" }));
    const ask = split("Высота «Спросить»");
    expect(ask).toHaveAttribute("aria-valuenow", "150");
    fireEvent.pointerDown(ask, { button: 0, clientY: 400, pointerId: 1 });
    expect(ask).toHaveFocus();
    fireEvent.pointerUp(ask, { clientY: 400, pointerId: 1 });
    expect(screen.getByRole("region", { name: "Спросить" })).not.toHaveClass("live-ws__ask--compact");
    fireEvent.keyDown(ask, { key: "ArrowUp" });
    expect(side().style.getPropertyValue("--live-ask")).toBe("166px");
    expect(localStorage.getItem("meet.pane.live-ask")).toBe("166");
    // Подсказкам и сводке — их минимумы.
    expect(ask).toHaveAttribute("aria-valuemax", String(600 - LIVE_PANES.hintsMin - LIVE_PANES.summary.min - 20));
  });

  test("карточка записи помнит свои размеры, плавающая панель — свои", () => {
    layout();
    localStorage.setItem("meet.pane.live-side", "500");
    render(<Host live={makeLive()} wide place="card" />);
    expect(ws().style.getPropertyValue("--live-side")).toBe("");
    fireEvent.keyDown(split("Ширина колонки подсказок"), { key: "ArrowLeft" });
    expect(localStorage.getItem("meet.pane.live-card-side")).toBe("416");
    expect(localStorage.getItem("meet.pane.live-side")).toBe("500");
  });

  test("строки идут — размеры не прыгают", () => {
    layout();
    localStorage.setItem("meet.pane.live-side", "480");
    const live = makeLive();
    const { rerender } = render(<Host live={live} wide />);
    for (let i = 0; i < 5; i += 1) {
      rerender(<Host live={{ ...live, lines: [...live.lines, { t: 200 + i, speaker: "Ольга", text: `ещё ${i}`, id: 10 + i }] }} wide />);
      expect(ws().style.getPropertyValue("--live-side")).toBe("480px");
      expect(side().style.getPropertyValue("--live-summary")).toBe("");
    }
  });
});
