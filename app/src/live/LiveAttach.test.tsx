import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  getState: vi.fn(),
  liveStop: vi.fn(),
  liveDetach: vi.fn(),
}));
vi.mock("../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../lib/shell")>()),
  inTauri: () => true,
  invoke: vi.fn(async (cmd: string) =>
    cmd === "live_window_state" ? { expanded: true, maximized: false, pinned: true } : undefined),
  onLiveWindow: vi.fn(async () => () => {}),
}));
import { getState, liveDetach, liveStop } from "../lib/api";
import type { LiveStatus, Snapshot } from "../lib/types";
import { FakeEventSource } from "../test/setup";
import { LivePanel } from "./LivePanel";
import { type FeedLine, addLine } from "./useLive";

/** Ассистент, включённый посреди обычной записи: панель и лента. */

const ep = { base: "http://h", token: "t" };
const status = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: true, starting: false, stopping: false, folder: "C:/r/x", error: null, started_at: null,
  attached: true, ...o,
});
const snap = (live: LiveStatus) =>
  ({ status: "recording", folder: "C:/r/x", levels: {}, live }) as Partial<Snapshot> as Snapshot;
const bus = () => FakeEventSource.instances.find((s) => s.url.startsWith("http://h/events"))!;
const liveStream = () => FakeEventSource.instances.filter((s) => s.url.startsWith("http://h/live/events")).at(-1)!;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getState).mockResolvedValue(snap(status()));
});

test("подключённый к записи: «Выключить ассистента» вместо «Стоп», запись идёт", async () => {
  vi.mocked(liveDetach).mockReturnValue(new Promise(() => {}));
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status())));
  expect(screen.queryByRole("button", { name: "Стоп" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Выключить ассистента" }));
  expect(liveDetach).toHaveBeenCalledWith(ep);
  expect(liveStop).not.toHaveBeenCalled();
  expect(screen.getByRole("banner")).toHaveTextContent("Выключаю…");
});

test("догоняет начало встречи: ход в шапке и полоса над рабочей областью", async () => {
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status())));
  await act(async () => {});
  act(() => liveStream().emit("state", {
    digest: "", status: null, summary: undefined, hints: [],
    catchup: { active: true, percent: 42, from_t: 600, to_t: 2400, capped: true, complete: false },
  }));
  expect(screen.getByRole("banner")).toHaveTextContent("Догоняю 42 %");
  const note = screen.getByText(/Догоняю начало встречи/).closest(".live-catchup")!;
  expect(note).toHaveTextContent("42 %");
  expect(note).toHaveTextContent("с 10:00; раньше — в итогах по полной расшифровке");
  act(() => liveStream().emit("state", {
    digest: "", status: null, hints: [],
    catchup: { active: false, percent: 100, from_t: 600, to_t: 2400, capped: true, complete: true },
  }));
  expect(screen.queryByText(/Догоняю начало встречи/)).toBeNull();
  expect(screen.getByRole("banner")).toHaveTextContent("Слушает");
});

test("обычный ассистент (не подключён к записи) — прежний «Стоп»", async () => {
  vi.mocked(getState).mockResolvedValue(snap(status({ attached: false })));
  vi.mocked(liveStop).mockReturnValue(new Promise(() => {}));
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap(status({ attached: false }))));
  await userEvent.click(screen.getByRole("button", { name: "Стоп" }));
  expect(liveStop).toHaveBeenCalledWith(ep);
  expect(liveDetach).not.toHaveBeenCalled();
});

const line = (t: number, id: number, catchup = false): FeedLine =>
  ({ t, speaker: "Демьян", text: `в ${t}`, id, ...(catchup ? { catchup: true } : {}) });

test("строки догнанного начала встают выше живых, по порядку", () => {
  let lines: FeedLine[] = [];
  lines = addLine(lines, line(600, 0));
  lines = addLine(lines, line(10, 1, true));
  lines = addLine(lines, line(605, 2));
  lines = addLine(lines, line(20, 3, true));
  expect(lines.map((l) => l.t)).toEqual([10, 20, 600, 605]);
  // Переполнение срезает самые старые — начало встречи.
  expect(addLine(lines, line(610, 4), 3).map((l) => l.t)).toEqual([600, 605, 610]);
  // Только начало (живых ещё нет) — по порядку прихода.
  expect(addLine([line(10, 0, true)], line(20, 1, true)).map((l) => l.t)).toEqual([10, 20]);
});


test("запись с ассистентом: в панели и «Остановить и сохранить», и «Выключить ассистента»", async () => {
  vi.mocked(liveStop).mockReturnValue(new Promise(() => {}));
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", { ...snap(status()), source: "live" } as Snapshot));
  expect(screen.getByRole("button", { name: "Выключить ассистента" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Остановить и сохранить" }));
  expect(liveStop).toHaveBeenCalledWith(ep);
  expect(liveDetach).not.toHaveBeenCalled();
  expect(screen.getByRole("banner")).toHaveTextContent("Останавливаю…");
});
