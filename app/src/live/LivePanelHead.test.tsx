import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  getState: vi.fn(),
  liveStop: vi.fn(),
  liveStart: vi.fn(),
  liveAttach: vi.fn(),
}));
vi.mock("../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../lib/shell")>()),
  inTauri: () => true,
  invoke: vi.fn(),
  onLiveWindow: vi.fn(async () => () => {}),
  onFileDrop: vi.fn(async () => () => {}),
  trayPanelOpen: vi.fn(async () => {}),
}));
import { getState, liveAttach, liveStart } from "../lib/api";
import { invoke, trayPanelOpen } from "../lib/shell";
import type { LiveStatus, Snapshot } from "../lib/types";
import { agentInfo, agentMsg } from "../test/chatFixtures";
import { FakeEventSource } from "../test/setup";
import { LivePanel, markOf, startFailed } from "./LivePanel";

/** Шапка 48 px, знак агента и три состояния свёрнутой панели (MeetLiveMini). */

const ep = { base: "http://h", token: "t" };
const NOW_S = 1_800_000_000;
const status = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: true, starting: false, stopping: false, folder: "C:/r/x", error: null, started_at: NOW_S - 754, ...o,
});
const snap = (live: LiveStatus, o: Partial<Snapshot> = {}) =>
  ({ status: "idle", folder: null, levels: {}, live, ...o }) as Partial<Snapshot> as Snapshot;
const bus = () => FakeEventSource.instances.find((s) => s.url.startsWith("http://h/events"))!;
const liveStream = () => FakeEventSource.instances.filter((s) => s.url.startsWith("http://h/live/events")).at(-1)!;
const liveState = { digest: "", summary: null, hints: [], hints_enabled: false, agent: agentInfo() };
const head = () => screen.getByRole("banner");
const mark = () => head().querySelector(".agent-mark");
let view = { expanded: false, maximized: false, pinned: true };

beforeEach(() => {
  vi.clearAllMocks();
  view = { expanded: false, maximized: false, pinned: true };
  vi.mocked(getState).mockResolvedValue(snap(status()));
  vi.mocked(invoke).mockImplementation(async (cmd: string, args?: Record<string, unknown>) => {
    if (cmd === "live_window_state") return view;
    if (cmd === "live_set_expanded") { view = { ...view, expanded: !!args?.expanded, maximized: false }; return view; }
    return undefined;
  });
});

test("знак агента по словам состояния: слушает, ищет, пишет, ждёт, покой; ошибка — без знака", () => {
  expect(markOf("listening")).toBe("listen");
  expect(markOf("thinking")).toBe("search");
  expect(markOf("searching")).toBe("search");
  expect(markOf("writing")).toBe("write");
  expect(markOf("answering")).toBe("write");
  expect(markOf("waiting")).toBe("wait");
  expect(markOf("idle")).toBe("rest");
  expect(markOf("error")).toBeNull();
});

test("шапка: точка записи, таймер, знак агента «слушает» и слово; «Стоп» — красная кнопка-значок", () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW_S * 1000);
  try {
    render(<LivePanel endpoint={ep} />);
    act(() => bus().emit("state", snap(status())));
    act(() => liveStream().emit("state", liveState));
    expect(head()).toHaveTextContent("12:34 Слушает");
    expect(mark()).toHaveAttribute("data-state", "listen");
    const stop = within(head()).getByRole("button", { name: "Стоп" });
    expect(stop).toHaveClass("btn", "btn--danger", "btn--icon", "btn--sm");
    expect(stop).not.toHaveClass("btn--ghost");
  } finally {
    vi.useRealTimers();
  }
});

test("шапка: догоняет начало — знак «ищет»; модель грузится — «ждёт»", () => {
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status({ ready: false, stage: "загружаю модель распознавания…" }))));
  expect(mark()).toHaveAttribute("data-state", "wait");
  act(() => bus().emit("state", snap(status({ ready: true }))));
  act(() => liveStream().emit("state", {
    ...liveState, catchup: { active: true, percent: 42, from_t: 0, to_t: 1466, capped: false, complete: false },
  }));
  expect(head()).toHaveTextContent("Догоняю 42 %");
  expect(mark()).toHaveAttribute("data-state", "search");
});

test("шапка: ошибка агента — «Ошибка» без знака агента", () => {
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status())));
  act(() => liveStream().emit("state", { ...liveState, agent: agentInfo({ state: "error", error: "лимит" }) }));
  expect(head()).toHaveTextContent("Ошибка");
  expect(mark()).toBeNull();
});

test("свёрнутая: непрочитанное — бейджем в шапке", () => {
  render(<LivePanel endpoint={ep} />);
  act(() => {
    liveStream().emit("state", liveState);
    liveStream().emit("chat_snapshot", { messages: [agentMsg("m1", { text: "Первое" })], seq: 2, agent: agentInfo() });
  });
  act(() => liveStream().emit("chat", { seq: 3, op: "add", message: agentMsg("m2", { text: "Второе" }) }));
  expect(within(head()).getByLabelText("новых сообщений: 1")).toHaveTextContent("1");
});

test("свёрнутая: вопрос вам — «Копировать» и «Показать в ленте»", async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  const scroll = vi.fn();
  Element.prototype.scrollIntoView = scroll;
  render(<LivePanel endpoint={ep} />);
  act(() => {
    liveStream().emit("state", liveState);
    liveStream().emit("chat_snapshot", {
      messages: [agentMsg("m1", { text: "Виктор, успеете **до пятницы**?", pin: true, t: 1340 })], seq: 2, agent: agentInfo(),
    });
  });
  const card = screen.getByRole("group", { name: "Вопрос вам" });
  expect(card).toHaveTextContent("Вопрос вам");
  expect(card).toHaveTextContent("22:20");
  expect(card).toHaveTextContent("Виктор, успеете до пятницы?");
  await userEvent.click(within(card).getByRole("button", { name: "Копировать" }));
  expect(writeText).toHaveBeenCalledWith("Виктор, успеете до пятницы?");
  expect(within(card).getByRole("button", { name: "Скопировано" })).toBeInTheDocument();
  await userEvent.click(within(card).getByRole("button", { name: "Показать в ленте" }));
  expect(invoke).toHaveBeenCalledWith("live_set_expanded", { expanded: true });
  await screen.findByRole("log", { name: "Чат с ассистентом" });
  expect(scroll).toHaveBeenCalledWith({ block: "center" });
  expect(scroll.mock.contexts.some((el) => (el as HTMLElement).dataset?.id === "m1")).toBe(true);
});

test("свёрнутая: догоняет начало встречи — ход полосой (progressbar)", () => {
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status({ attached: true }), { status: "recording" })));
  act(() => liveStream().emit("state", {
    ...liveState, catchup: { active: true, percent: 42, from_t: 0, to_t: 1466, capped: false, complete: false },
  }));
  const bar = screen.getByRole("progressbar", { name: "Догоняю начало встречи" });
  expect(bar).toHaveAttribute("aria-valuenow", "42");
  expect(bar).toHaveClass("progress");
  expect(screen.getByRole("status")).toHaveTextContent("Запись не прерывается");
});

test("не запустился: карточка-предупреждение, «Повторить» — снова старт, «Открыть настройки» — «Модели ИИ»", async () => {
  vi.mocked(liveStart).mockResolvedValue({ ok: true, ...status({ active: false, starting: true }) });
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status({ active: false, error: "Claude Code не найден", ended_by: "crash" }))));
  expect(head()).toHaveTextContent("Ошибка");
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("Не удалось запустить ассистента");
  expect(alert).toHaveTextContent("Claude Code не найден");
  await userEvent.click(within(alert).getByRole("button", { name: "Повторить" }));
  expect(liveStart).toHaveBeenCalledWith(ep, {});
  expect(liveAttach).not.toHaveBeenCalled();
  await userEvent.click(within(alert).getByRole("button", { name: "Открыть настройки" }));
  expect(trayPanelOpen).toHaveBeenCalledWith({ section: "models" });
});

test("не подключился к идущей записи: «Повторить» — снова подключить, ошибка повтора видна", async () => {
  vi.mocked(liveAttach).mockRejectedValue(new Error("резидент занят"));
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status({ active: false, error: "упал", ended_by: "crash" }), { status: "recording" })));
  await userEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Повторить" }));
  expect(liveAttach).toHaveBeenCalledWith(ep, undefined);
  expect(liveStart).not.toHaveBeenCalled();
  expect(await screen.findByText(/резидент занят/)).toBeInTheDocument();
});

test("startFailed: только кончившийся сбоем запуск с причиной", () => {
  expect(startFailed(status({ active: false, error: "x", ended_by: "crash" }))).toBe(true);
  expect(startFailed(status({ active: false, error: "x", ended_by: "stop" }))).toBe(false);
  expect(startFailed(status({ active: false, starting: true, error: "x", ended_by: "crash" }))).toBe(false);
  expect(startFailed(status({ active: true, error: "x", ended_by: "crash" }))).toBe(false);
  expect(startFailed(status({ active: false, error: null, ended_by: "crash" }))).toBe(false);
  expect(startFailed(null)).toBe(false);
});
