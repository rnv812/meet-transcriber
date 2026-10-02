import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  resolveEndpoint: vi.fn(),
  getState: vi.fn(),
  liveStop: vi.fn(),
  liveAsk: vi.fn(),
}));
vi.mock("../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../lib/shell")>()),
  inTauri: () => true,
  invoke: vi.fn(async () => undefined),
  onLiveWindow: vi.fn(async () => () => {}),
}));
import { NoResidentError, getState, liveAsk, liveStop, resolveEndpoint } from "../lib/api";
import { invoke, onLiveWindow } from "../lib/shell";
import type { LiveStatus, Snapshot } from "../lib/types";
import { FakeEventSource } from "../test/setup";
import { LivePanel, LiveWindow } from "./LivePanel";
import type { LiveView } from "./useLiveWindow";

const ep = { base: "http://h", token: "t" };
const NOW_S = 1_800_000_000;
const status = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: true, starting: false, stopping: false, folder: "C:/r/x", error: null, started_at: NOW_S - 754, ...o,
});
const snap = (live: LiveStatus) => ({ status: "idle", folder: null, levels: {}, live }) as Partial<Snapshot> as Snapshot;
const bus = () => FakeEventSource.instances.find((s) => s.url.startsWith("http://h/events"))!;
const liveStream = () => FakeEventSource.instances.filter((s) => s.url.startsWith("http://h/live/events")).at(-1)!;

/** Оболочка: вид при открытии `initial`, команды вида отвечают итоговым видом. */
function shellWith(initial: Partial<LiveView> = {}) {
  let view: LiveView = { expanded: false, maximized: false, pinned: true, ...initial };
  vi.mocked(invoke).mockImplementation(async (cmd: string, args?: Record<string, unknown>) => {
    if (cmd === "live_window_state") return view;
    if (cmd === "live_set_expanded") view = { ...view, expanded: !!args?.expanded, maximized: false };
    else if (cmd === "live_set_maximized") view = { ...view, maximized: !!args?.maximized };
    else if (cmd === "live_set_pinned") view = { ...view, pinned: !!args?.pinned };
    else return undefined;
    return view;
  });
}
const calls = (cmd: string) => vi.mocked(invoke).mock.calls.filter(([c]) => c === cmd);
const head = () => screen.getByRole("banner");

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getState).mockResolvedValue(snap(status()));
  vi.mocked(onLiveWindow).mockImplementation(async () => () => {});
  shellWith();
});
afterEach(() => vi.useRealTimers());

test("свёрнутая: таймер от начала, «Ассистент слушает», последняя реплика", () => {
  vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval", "setTimeout", "clearTimeout"] });
  vi.setSystemTime(NOW_S * 1000);
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status())));
  expect(screen.getByRole("banner")).toHaveTextContent("12:34 · Ассистент слушает");
  act(() => { vi.advanceTimersByTime(1000); });
  expect(screen.getByRole("banner")).toHaveTextContent("12:35 · Ассистент слушает");
  act(() => {
    liveStream().emit("line", { t: 1, speaker: "Демьян", text: "первая" }, 0);
    liveStream().emit("line", { t: 2, speaker: "Мария", text: "вторая" }, 1);
  });
  expect(screen.getByText("вторая")).toBeInTheDocument();
  expect(screen.queryByText("первая")).toBeNull();
  expect(screen.queryByRole("log")).toBeNull();
});

test("Стоп вызывает liveStop и сразу показывает «Останавливаю…»", async () => {
  vi.mocked(liveStop).mockReturnValue(new Promise(() => {}));
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status())));
  await userEvent.click(screen.getByRole("button", { name: "Стоп" }));
  expect(liveStop).toHaveBeenCalledWith(ep);
  expect(screen.getByRole("banner")).toHaveTextContent("· Останавливаю…");
  expect(screen.getByRole("button", { name: "Стоп" })).toBeDisabled();
});

test("остановка снаружи (трей): снимок stopping — «Останавливаю…», Стоп неактивен", async () => {
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status())));
  vi.mocked(getState).mockResolvedValueOnce(snap(status({ active: false, stopping: true })));
  await act(async () => { bus().emit("live.stopping", { kind: "live.stopping", at: 1 }); });
  expect(getState).toHaveBeenCalledWith(ep);
  expect(screen.getByRole("banner")).toHaveTextContent("· Останавливаю…");
  expect(screen.getByRole("button", { name: "Стоп" })).toBeDisabled();
});

test("ошибка остановки видна, Стоп снова доступен", async () => {
  vi.mocked(liveStop).mockRejectedValue(new Error("резидент не отвечает"));
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status())));
  await userEvent.click(screen.getByRole("button", { name: "Стоп" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("резидент не отвечает");
  expect(screen.getByRole("button", { name: "Стоп" })).toBeEnabled();
});

test("Развернуть и Свернуть просят оболочку сменить вид, высоту она помнит сама", async () => {
  render(<LivePanel endpoint={ep} />);
  await act(async () => {});
  expect(invoke).toHaveBeenCalledWith("live_window_state");
  // При открытии размер не трогаем: окно уже такое, каким его оставили.
  expect(calls("live_set_expanded")).toEqual([]);
  act(() => {
    liveStream().emit("state", { digest: "- релиз в пятницу", transcript: [], status: null });
    liveStream().emit("line", { t: 1, speaker: "Демьян", text: "первая" }, 0);
    liveStream().emit("line", { t: 2, speaker: "Мария", text: "вторая" }, 1);
  });
  expect(screen.getByRole("button", { name: "Развернуть" })).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(screen.getByRole("button", { name: "Развернуть" }));
  expect(invoke).toHaveBeenLastCalledWith("live_set_expanded", { expanded: true });
  expect(screen.getByRole("button", { name: "Свернуть" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("log")).toHaveTextContent("первая");
  expect(screen.getByRole("log")).toHaveTextContent("вторая");
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Лента", "Сводка", "Подсказки", "Спросить"]);
  await userEvent.click(screen.getByRole("tab", { name: "Спросить" }));
  expect(screen.getByRole("textbox", { name: "Вопрос ассистенту" })).not.toHaveFocus();
  await userEvent.click(screen.getByRole("button", { name: "Свернуть" }));
  expect(invoke).toHaveBeenLastCalledWith("live_set_expanded", { expanded: false });
  expect(screen.queryByRole("log")).toBeNull();
});

test("панель, которую оставили развёрнутой, открывается развёрнутой", async () => {
  shellWith({ expanded: true });
  render(<LivePanel endpoint={ep} />);
  expect(await screen.findByRole("log")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Свернуть" })).toHaveAttribute("aria-expanded", "true");
  expect(calls("live_set_expanded")).toEqual([]);
});

test("растянули свёрнутую за край — оболочка сообщает, панель разворачивается", async () => {
  let push!: (view: unknown) => void;
  vi.mocked(onLiveWindow).mockImplementation(async (cb) => { push = cb; return () => {}; });
  render(<LivePanel endpoint={ep} />);
  await act(async () => {});
  expect(screen.queryByRole("log")).toBeNull();
  act(() => push({ expanded: true, maximized: false, pinned: true }));
  expect(screen.getByRole("log")).toBeInTheDocument();
  act(() => push({ expanded: false, maximized: false, pinned: true }));
  expect(screen.queryByRole("log")).toBeNull();
});

test("«На весь экран» и «Обычный размер»; на весь экран видно всё содержимое", async () => {
  render(<LivePanel endpoint={ep} />);
  await userEvent.click(screen.getByRole("button", { name: "На весь экран" }));
  expect(invoke).toHaveBeenLastCalledWith("live_set_maximized", { maximized: true });
  expect(screen.getByRole("log")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Обычный размер" }));
  expect(invoke).toHaveBeenLastCalledWith("live_set_maximized", { maximized: false });
  expect(screen.getByRole("button", { name: "На весь экран" })).toBeInTheDocument();
  expect(screen.queryByRole("log")).toBeNull();
});

test("Свернуть на весь экран — оболочка сворачивает (и возвращает обычный размер)", async () => {
  shellWith({ maximized: true });
  render(<LivePanel endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "Свернуть" }));
  expect(invoke).toHaveBeenLastCalledWith("live_set_expanded", { expanded: false });
  expect(screen.getByRole("button", { name: "На весь экран" })).toBeInTheDocument();
  expect(screen.queryByRole("log")).toBeNull();
});

test("Esc на весь экран возвращает обычный размер; в обычном Esc ничего не делает", async () => {
  render(<LivePanel endpoint={ep} />);
  await userEvent.keyboard("{Escape}");
  expect(calls("live_set_maximized")).toEqual([]);
  await userEvent.click(screen.getByRole("button", { name: "На весь экран" }));
  await userEvent.keyboard("{Escape}");
  expect(invoke).toHaveBeenLastCalledWith("live_set_maximized", { maximized: false });
  expect(screen.getByRole("button", { name: "На весь экран" })).toBeInTheDocument();
});

test("Esc в поле вопроса не возвращает обычный размер", async () => {
  render(<LivePanel endpoint={ep} />);
  await userEvent.click(screen.getByRole("button", { name: "На весь экран" }));
  await userEvent.click(screen.getByRole("tab", { name: "Спросить" }));
  await userEvent.click(screen.getByRole("textbox", { name: "Вопрос ассистенту" }));
  await userEvent.keyboard("{Escape}");
  expect(calls("live_set_maximized")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Обычный размер" })).toBeInTheDocument();
});

test("шапка: нажатие тащит окно, двойной щелчок — на весь экран и обратно", async () => {
  render(<LivePanel endpoint={ep} />);
  await act(async () => {});
  const title = screen.getByText(/Ассистент слушает/);
  fireEvent.mouseDown(title, { button: 0, detail: 1 });
  expect(invoke).toHaveBeenLastCalledWith("live_start_drag");
  await act(async () => { fireEvent.mouseDown(title, { button: 0, detail: 2 }); });
  expect(invoke).toHaveBeenLastCalledWith("live_set_maximized", { maximized: true });
  expect(screen.getByRole("button", { name: "Обычный размер" })).toBeInTheDocument();
  await act(async () => { fireEvent.mouseDown(head(), { button: 0, detail: 2 }); });
  expect(invoke).toHaveBeenLastCalledWith("live_set_maximized", { maximized: false });
  // Правая кнопка и кнопки шапки окно не таскают.
  vi.mocked(invoke).mockClear();
  fireEvent.mouseDown(title, { button: 2, detail: 1 });
  fireEvent.mouseDown(screen.getByRole("button", { name: "Стоп" }), { button: 0, detail: 1 });
  fireEvent.mouseDown(screen.getByRole("button", { name: "Развернуть" }), { button: 0, detail: 2 });
  expect(calls("live_start_drag")).toEqual([]);
  expect(calls("live_set_maximized")).toEqual([]);
});

test("«Поверх всех окон» включено по умолчанию и переключается", async () => {
  render(<LivePanel endpoint={ep} />);
  const pin = screen.getByRole("button", { name: "Поверх всех окон" });
  expect(pin).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(pin);
  expect(invoke).toHaveBeenLastCalledWith("live_set_pinned", { pinned: false });
  expect(pin).toHaveAttribute("aria-pressed", "false");
  await userEvent.click(pin);
  expect(invoke).toHaveBeenLastCalledWith("live_set_pinned", { pinned: true });
  expect(pin).toHaveAttribute("aria-pressed", "true");
});

test("оболочка отказала — вид возвращается прежним", async () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  render(<LivePanel endpoint={ep} />);
  await act(async () => {});
  vi.mocked(invoke).mockRejectedValueOnce("панели ассистента нет");
  await userEvent.click(screen.getByRole("button", { name: "На весь экран" }));
  expect(await screen.findByRole("button", { name: "На весь экран" })).toBeInTheDocument();
  expect(screen.queryByRole("log")).toBeNull();
  expect(warn).toHaveBeenCalled();
  warn.mockRestore();
});

test("«Что я пропустил?» — с момента, когда панель последний раз свернули", async () => {
  vi.mocked(liveAsk).mockResolvedValue({ answer: "Решили релиз в пятницу" });
  render(<LivePanel endpoint={ep} />);
  await userEvent.click(screen.getByRole("button", { name: "Развернуть" }));
  await userEvent.click(screen.getByRole("tab", { name: "Спросить" }));
  // Ещё не сворачивали — ассистент возьмёт последние минуты сам.
  await userEvent.click(screen.getByRole("button", { name: "Что я пропустил?" }));
  expect(liveAsk).toHaveBeenLastCalledWith(ep, "", { quick: "missed" });
  act(() => liveStream().emit("line", { t: 125, speaker: "Демьян", text: "до ухода" }, 0));
  await userEvent.click(screen.getByRole("button", { name: "Свернуть" }));
  act(() => liveStream().emit("line", { t: 300, speaker: "Демьян", text: "без меня" }, 1));
  await userEvent.click(screen.getByRole("button", { name: "Развернуть" }));
  await userEvent.click(screen.getByRole("button", { name: "Что я пропустил?" }));
  expect(liveAsk).toHaveBeenLastCalledWith(ep, "", { quick: "missed", since_t: 125 });
  // История — у ассистента: ответ приходит событием qa.
  act(() => liveStream().emit("qa", { qa: [
    { id: 1, q: "Что я пропустил?", a: "Решили релиз в пятницу [00:05:00]", error: null, pending: false, at: 1, quick: "missed" },
  ] }));
  expect(screen.getByText(/Решили релиз в пятницу/)).toBeInTheDocument();
  // Таймкод ответа ведёт к реплике в ленте (узкая панель — на вкладку «Лента»).
  await userEvent.click(screen.getByRole("button", { name: "00:05:00" }));
  expect(screen.getByRole("tab", { name: "Лента" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("без меня").closest("li")).toHaveClass("is-target");
});

test("панель не берёт фокус при появлении", () => {
  render(<LivePanel endpoint={ep} />);
  expect(document.activeElement).toBe(document.body);
});

test("LiveWindow: ищет резидента, пока не найдёт, затем показывает панель", async () => {
  vi.useFakeTimers();
  vi.mocked(resolveEndpoint).mockRejectedValueOnce(new NoResidentError("нет")).mockResolvedValue(ep);
  render(<LiveWindow />);
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(screen.getByText("Ассистент слушает…")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Стоп" })).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
  expect(screen.getByRole("button", { name: "Стоп" })).toBeInTheDocument();
});

test("время начала неизвестно — вместо таймера прочерк", () => {
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status({ started_at: null }))));
  expect(screen.getByRole("banner")).toHaveTextContent("— · Ассистент слушает");
  expect(screen.getByRole("banner")).not.toHaveTextContent("00:00");
});

test("снимок живого режима: применяется только ответ на последнее событие", async () => {
  let first!: (s: Snapshot) => void;
  vi.mocked(getState)
    .mockReturnValueOnce(new Promise((r) => { first = r; }))
    .mockResolvedValueOnce(snap(status({ active: false, stopping: true })));
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status())));
  await act(async () => {
    bus().emit("live.started", { kind: "live.started", at: 1 });
    bus().emit("live.stopping", { kind: "live.stopping", at: 2 });
  });
  expect(screen.getByRole("banner")).toHaveTextContent("· Останавливаю…");
  await act(async () => { first(snap(status())); });
  expect(screen.getByRole("banner")).toHaveTextContent("· Останавливаю…");
});

test("остановка: поток ассистента закрыт, пока он дописывает запись (active и stopping)", async () => {
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status())));
  const stream = liveStream();
  vi.mocked(getState).mockResolvedValueOnce(snap(status({ active: true, stopping: true })));
  await act(async () => { bus().emit("live.stopping", { kind: "live.stopping", at: 1 }); });
  expect(stream.closed).toBe(true);
  expect(liveStream()).toBe(stream); // нового потока не открывали
  expect(screen.queryByText(/Нет связи с ассистентом/)).toBeNull();
});

// --- L2: строка свёрнутой панели, «Не отвлекать», статус, широкая раскладка ----------

const hint = (id: string, kind: string, text: string, o: Record<string, unknown> = {}) => ({
  id, kind, text, why: "", source_t: 60, ref: null, pinned: false, dismissed: false, created_at: 1, updated_at: 1, ...o,
});
const state = (hints: unknown[], o: Record<string, unknown> = {}) => ({ digest: "", transcript: [], status: null, hints, ...o });

test("свёрнутая: самая важная подсказка и счётчик новых; щелчок — развернуть на «Подсказках»", async () => {
  render(<LivePanel endpoint={ep} />);
  await act(async () => {});
  act(() => liveStream().emit("state", state([])));
  act(() => liveStream().emit("state", state([
    hint("h1", "followup", "Зафиксировать созвон"), hint("h2", "unanswered", "Вопрос про цену без ответа"),
  ])));
  const line = screen.getByRole("button", { name: /Открыть подсказки/ });
  expect(line).toHaveTextContent("Без ответа");
  expect(line).toHaveTextContent("Вопрос про цену без ответа");
  expect(within(line).getByLabelText("новых подсказок: 2")).toBeInTheDocument();
  await userEvent.click(line);
  expect(invoke).toHaveBeenLastCalledWith("live_set_expanded", { expanded: true });
  expect(screen.getByRole("tab", { name: "Подсказки" })).toHaveAttribute("aria-selected", "true");
  await userEvent.click(screen.getByRole("button", { name: "Свернуть" }));
  expect(screen.queryByLabelText(/новых подсказок/)).toBeNull(); // увидел — не новые
});

test("строка меняется, только когда сменилась самая важная подсказка", async () => {
  render(<LivePanel endpoint={ep} />);
  await act(async () => {});
  act(() => liveStream().emit("state", state([hint("h1", "risk", "Риск по срокам")])));
  act(() => liveStream().emit("state", state([hint("h1", "risk", "Риск по срокам"), hint("h2", "term", "Термин дня")])));
  expect(screen.getByRole("button", { name: /Открыть подсказки/ })).toHaveTextContent("Риск по срокам");
  act(() => liveStream().emit("state", state([hint("h2", "term", "Термин дня"), hint("h3", "unanswered", "Без ответа: цена")])));
  expect(screen.getByRole("button", { name: /Открыть подсказки/ })).toHaveTextContent("Без ответа: цена");
});

test("«Не отвлекать»: строка не меняется, пока её подсказка жива; счётчика нет", async () => {
  render(<LivePanel endpoint={ep} />);
  await act(async () => {});
  act(() => liveStream().emit("state", state([hint("h1", "followup", "Следующий шаг: созвон")])));
  const quiet = screen.getByRole("button", { name: "Не отвлекать" });
  expect(quiet).toHaveAttribute("aria-pressed", "false");
  await userEvent.click(quiet);
  expect(quiet).toHaveAttribute("aria-pressed", "true");
  act(() => liveStream().emit("state", state([
    hint("h1", "followup", "Следующий шаг: созвон"), hint("h2", "unanswered", "Цена без ответа"),
  ])));
  const line = screen.getByRole("button", { name: /Открыть подсказки/ });
  expect(line).toHaveTextContent("Следующий шаг: созвон");
  expect(screen.queryByLabelText(/новых подсказок/)).toBeNull();
  act(() => liveStream().emit("state", state([hint("h2", "unanswered", "Цена без ответа")])));
  expect(screen.getByRole("button", { name: /Открыть подсказки/ })).toHaveTextContent("Цена без ответа");
});

test("модель недоступна — тихий статус в шапке, без всплывающих ошибок", async () => {
  render(<LivePanel endpoint={ep} />);
  await act(async () => {});
  act(() => liveStream().emit("state", state([], { status: "Подсказки временно недоступны" })));
  expect(within(head()).getByRole("status")).toHaveAttribute("title", "Подсказки временно недоступны");
  expect(screen.queryByRole("alert")).toBeNull();
  act(() => liveStream().emit("state", state([])));
  expect(within(head()).queryByRole("status")).toBeNull();
});

test("широкое окно (от 720) — две колонки; сузили — вкладки", async () => {
  const observers: (() => void)[] = [];
  vi.stubGlobal("ResizeObserver", class {
    constructor(cb: () => void) { observers.push(cb); }
    observe() {}
    disconnect() {}
  });
  let width = 900;
  const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockImplementation(() => ({ width, height: 600, top: 0, left: 0, right: width, bottom: 600, x: 0, y: 0 }) as DOMRect);
  try {
    shellWith({ expanded: true });
    render(<LivePanel endpoint={ep} />);
    expect(await screen.findByRole("region", { name: "Подсказки" })).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).toBeNull();
    width = 500;
    act(() => observers.forEach((cb) => cb()));
    expect(screen.getByRole("tablist")).toBeInTheDocument();
  } finally {
    rect.mockRestore();
    vi.unstubAllGlobals();
  }
});

test("«Не отвлекать по умолчанию» из настроек включает режим при открытии; дальше решает человек", async () => {
  render(<LivePanel endpoint={ep} />);
  await act(async () => {});
  const quiet = screen.getByRole("button", { name: "Не отвлекать" });
  act(() => liveStream().emit("state", state([], { prefs: { quiet_default: true, activity: "calm" } })));
  expect(quiet).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(quiet);
  act(() => liveStream().emit("state", state([], { prefs: { quiet_default: true, activity: "calm" } })));
  expect(quiet).toHaveAttribute("aria-pressed", "false");
});

test("«Вам вопрос» встаёт в строку свёрнутой панели — и в «Не отвлекать» тоже", async () => {
  render(<LivePanel endpoint={ep} />);
  await act(async () => {});
  act(() => liveStream().emit("state", state([hint("h1", "risk", "Риск по срокам", { pinned: true })])));
  await userEvent.click(screen.getByRole("button", { name: "Не отвлекать" }));
  act(() => liveStream().emit("state", state([
    hint("h1", "risk", "Риск по срокам", { pinned: true }),
    hint("h2", "ask_you", "Ольга спрашивает про отчёт", { reply: "Отчёт будет в четверг." }),
  ])));
  const line = screen.getByRole("button", { name: /Открыть подсказки/ });
  expect(line).toHaveTextContent("Вам вопрос");
  expect(line).toHaveTextContent("Ольга спрашивает про отчёт");
  expect(line).toHaveClass("live-last--urgent");
});
