import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  getState: vi.fn(),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  setAgentYolo: vi.fn(async () => ({ on: true, live: true })),
}));
vi.mock("../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../lib/shell")>()),
  inTauri: () => true,
  invoke: vi.fn(),
  onLiveWindow: vi.fn(async () => () => {}),
  onFileDrop: vi.fn(async () => () => {}),
}));
import { getSettings, getState, patchSettings, setAgentYolo } from "../lib/api";
import { invoke } from "../lib/shell";
import type { AgentInfo, LiveStatus, Snapshot } from "../lib/types";
import { agentInfo, agentMsg } from "../test/chatFixtures";
import { FakeEventSource } from "../test/setup";
import { LivePanel } from "./LivePanel";
import { SESSION_BAR_KEY } from "./useSessionBar";

/** Плавающая панель (0.6.2): YOLO из меню «Ещё», строка сессии скрывается и это запоминается. */

const ep = { base: "http://h", token: "t" };
const status: LiveStatus = { active: true, starting: false, stopping: false, folder: "C:/r/x", error: null, started_at: null };
const snap = { status: "idle", folder: null, levels: {}, live: status } as Partial<Snapshot> as Snapshot;
const liveStream = () => FakeEventSource.instances.filter((s) => s.url.startsWith("http://h/live/events")).at(-1)!;
const free = (o: Partial<AgentInfo> = {}) => agentInfo({
  can: { mode: "consent", mcp: null, agent_mode: "auto", auto: true }, yolo: false, yolo_available: true, ...o,
});
let view = { expanded: true, maximized: false, pinned: true };
let settings: Record<string, unknown> = {};

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.removeItem(SESSION_BAR_KEY);
  view = { expanded: true, maximized: false, pinned: true };
  settings = { ui: { terms_accepted: "x", live_session_bar: true } };
  vi.mocked(getState).mockResolvedValue(snap);
  vi.mocked(getSettings).mockImplementation(async () => structuredClone(settings));
  vi.mocked(patchSettings).mockImplementation(async (_ep, updates) => {
    const ui = (updates as { ui?: Record<string, unknown> }).ui ?? {};
    settings = { ...settings, ui: { ...(settings.ui as object), ...ui } };
    return { settings, restart_required: [] };
  });
  vi.mocked(invoke).mockImplementation(async (cmd: string, args?: Record<string, unknown>) => {
    if (cmd === "live_window_state") return view;
    if (cmd === "live_set_expanded") { view = { ...view, expanded: !!args?.expanded }; return view; }
    return undefined;
  });
});

function start(agent: AgentInfo) {
  render(<LivePanel endpoint={ep} />);
  act(() => {
    liveStream().emit("state", { digest: "", summary: null, hints: [], hints_enabled: false, agent });
    liveStream().emit("chat_snapshot", { messages: [agentMsg("m1", { text: "Привет" })], seq: 2, agent });
  });
}

async function menu() {
  await userEvent.click(screen.getByRole("button", { name: "Ещё" }));
  return screen.getByRole("menu", { name: "Ещё" });
}

test("«Ещё» → «Включить YOLO» — вопрос на месте содержимого; «Включить» — PUT /live/yolo", async () => {
  start(free());
  expect(await screen.findByRole("group", { name: "Сессия ассистента" })).toBeInTheDocument();
  await userEvent.click(within(await menu()).getByRole("menuitem", { name: "Включить YOLO" }));
  const ask = screen.getByRole("alertdialog", { name: "Включить YOLO до конца встречи?" });
  expect(setAgentYolo).not.toHaveBeenCalled();
  await userEvent.click(within(ask).getByRole("button", { name: "Включить YOLO" }));
  expect(setAgentYolo).toHaveBeenCalledWith(ep, true);
  act(() => liveStream().emit("agent", free({ yolo: true })));
  expect(within(await menu()).getByRole("menuitem", { name: "Выключить YOLO" })).toBeInTheDocument();
});

test("YOLO выключается из меню сразу, без вопроса", async () => {
  start(free({ yolo: true }));
  await userEvent.click(within(await menu()).getByRole("menuitem", { name: "Выключить YOLO" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(setAgentYolo).toHaveBeenCalledWith(ep, false);
});

test("без расширенных возможностей пункта YOLO нет; строку сессии скрыть можно", async () => {
  start(agentInfo({ yolo_available: false }));
  const items = within(await menu()).getAllByRole("menuitem").map((b) => b.textContent);
  expect(items).not.toContain("Включить YOLO");
  expect(items).toContain("Скрыть строку сессии");
});

test("строка сессии скрывается из меню и из самой строки — в настройки; YOLO и ошибка — в шапке", async () => {
  start(free({ yolo: true, state: "error", error: "Claude Code упал" }));
  const banner = screen.getByRole("banner");
  expect(await screen.findByRole("group", { name: "Сессия ассистента" })).toBeInTheDocument();
  expect(banner.querySelector(".live-head__yolo")).toBeNull();                  // строка видна — бейдж в ней
  await userEvent.click(within(screen.getByRole("group", { name: "Сессия ассистента" }))
    .getByRole("button", { name: "Скрыть строку сессии" }));
  expect(screen.queryByRole("group", { name: "Сессия ассистента" })).toBeNull();
  expect(patchSettings).toHaveBeenCalledWith(ep, { ui: { live_session_bar: false } });
  expect(window.localStorage.getItem(SESSION_BAR_KEY)).toBe("0");
  expect(banner.querySelector(".live-head__yolo")).toHaveTextContent("YOLO");
  expect(banner).toHaveTextContent("Claude Code упал");
  await userEvent.click(within(await menu()).getByRole("menuitem", { name: "Показать строку сессии" }));
  expect(screen.getByRole("group", { name: "Сессия ассистента" })).toBeInTheDocument();
  expect(patchSettings).toHaveBeenLastCalledWith(ep, { ui: { live_session_bar: true } });
});

test("скрытая строка остаётся скрытой на следующей встрече (настройка ui.live_session_bar)", async () => {
  settings = { ui: { terms_accepted: "x", live_session_bar: false } };
  start(free());
  await screen.findByRole("log", { name: "Чат с ассистентом" });
  await act(async () => {});
  expect(screen.queryByRole("group", { name: "Сессия ассистента" })).toBeNull();
  expect(within(await menu()).getByRole("menuitem", { name: "Показать строку сессии" })).toBeInTheDocument();
});
