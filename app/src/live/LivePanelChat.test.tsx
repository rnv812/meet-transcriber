import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  getState: vi.fn(),
  liveStop: vi.fn(),
}));
vi.mock("../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../lib/shell")>()),
  inTauri: () => true,
  invoke: vi.fn(),
  onLiveWindow: vi.fn(async () => () => {}),
  onFileDrop: vi.fn(async () => () => {}),
}));
import { getState } from "../lib/api";
import { invoke } from "../lib/shell";
import type { LiveStatus, Snapshot } from "../lib/types";
import { agentInfo, agentMsg } from "../test/chatFixtures";
import { FakeEventSource } from "../test/setup";
import { LivePanel } from "./LivePanel";

/** Плавающая панель с агентом-участником. */

const ep = { base: "http://h", token: "t" };
const status: LiveStatus = { active: true, starting: false, stopping: false, folder: "C:/r/x", error: null, started_at: null };
const snap = { status: "idle", folder: null, levels: {}, live: status } as Partial<Snapshot> as Snapshot;
const liveStream = () => FakeEventSource.instances.filter((s) => s.url.startsWith("http://h/live/events")).at(-1)!;
const liveState = { digest: "", summary: null, hints: [], hints_enabled: false, agent: agentInfo() };
let view = { expanded: false, maximized: false, pinned: true };

beforeEach(() => {
  vi.clearAllMocks();
  view = { expanded: false, maximized: false, pinned: true };
  vi.mocked(getState).mockResolvedValue(snap);
  vi.mocked(invoke).mockImplementation(async (cmd: string, args?: Record<string, unknown>) => {
    if (cmd === "live_window_state") return view;
    if (cmd === "live_set_expanded") { view = { ...view, expanded: !!args?.expanded }; return view; }
    return undefined;
  });
});

test("свёрнутая: последнее сообщение агента и счётчик новых; щелчок — развернуть в чат", async () => {
  render(<LivePanel endpoint={ep} />);
  act(() => {
    liveStream().emit("state", liveState);
    liveStream().emit("chat_snapshot", { messages: [agentMsg("m1", { text: "Первое" })], seq: 2, agent: agentInfo() });
  });
  expect(screen.getByRole("button", { name: /Ассистент: Первое/ })).toBeInTheDocument();
  act(() => liveStream().emit("chat", { seq: 3, op: "add", message: agentMsg("m2", { text: "Там **15.11**, а не 01.12" }) }));
  const row = screen.getByRole("button", { name: /Ассистент: Там 15.11, а не 01.12/ });
  expect(row).toHaveTextContent("1");
  await userEvent.click(row);
  expect(await screen.findByRole("log", { name: "Чат с ассистентом" })).toHaveTextContent("Там 15.11, а не 01.12");
});

test("свёрнутая: закреплённый вопрос перекрывает последнее сообщение", () => {
  render(<LivePanel endpoint={ep} />);
  act(() => {
    liveStream().emit("state", liveState);
    liveStream().emit("chat_snapshot", {
      messages: [agentMsg("m1", { text: "Сказать про срок?", pin: true }), agentMsg("m2", { text: "Позже" })], seq: 2,
    });
  });
  expect(screen.getByRole("button", { name: /Вопрос вам: Сказать про срок\?/ })).toBeInTheDocument();
});

test("без агента-участника — прежняя строка подсказок", () => {
  render(<LivePanel endpoint={ep} />);
  act(() => liveStream().emit("state", { ...liveState, agent: undefined, hints_enabled: true }));
  expect(screen.queryByRole("button", { name: /Ассистент:/ })).toBeNull();
});
