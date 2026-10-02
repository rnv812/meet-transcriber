import { act, fireEvent, render, screen } from "@testing-library/react";
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
  expect(screen.getByRole("button", { name: /Дайджест/ })).toBeInTheDocument();
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

test("«Что я пропустил?» спрашивает ассистента фиксированным текстом, ответ — под полем", async () => {
  vi.mocked(liveAsk).mockResolvedValue({ answer: "Решили релиз в пятницу" });
  render(<LivePanel endpoint={ep} />);
  await userEvent.click(screen.getByRole("button", { name: "Развернуть" }));
  await userEvent.click(screen.getByRole("button", { name: "Что я пропустил?" }));
  expect(liveAsk).toHaveBeenCalledWith(ep, "Что я пропустил за последние минуты?");
  expect(await screen.findByText("Решили релиз в пятницу")).toBeInTheDocument();
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
