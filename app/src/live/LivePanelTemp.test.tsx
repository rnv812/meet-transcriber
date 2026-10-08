import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  getState: vi.fn(),
  liveStop: vi.fn(),
  recordingCommand: vi.fn(),
}));
vi.mock("../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../lib/shell")>()),
  inTauri: () => true,
  invoke: vi.fn(async () => undefined),
  onLiveWindow: vi.fn(async () => () => {}),
}));
import { getState, liveStop, recordingCommand } from "../lib/api";
import { invoke, onLiveWindow } from "../lib/shell";
import type { CommandResult, LiveStatus, Snapshot } from "../lib/types";
import { FakeEventSource } from "../test/setup";
import { LivePanel } from "./LivePanel";
import type { LiveView } from "./useLiveWindow";

/** Плавающая панель: «Остановить без сохранения» и временная встреча. */

const ep = { base: "http://h", token: "t" };
const live = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: true, starting: false, stopping: false, folder: "C:/data/tmp-meetings/ab/2026-10-07_15-00",
  error: null, started_at: 1_800_000_000 - 60, attached: true, ready: true, ...o,
});
const snap = (o: Partial<Snapshot> = {}) =>
  ({ status: "recording", source: "live", folder: "C:/r/x", levels: {}, live: live(), ...o }) as Partial<Snapshot> as Snapshot;
const bus = () => FakeEventSource.instances.find((s) => s.url.startsWith("http://h/events"))!;
const head = () => screen.getByRole("banner");

function shellWith(initial: Partial<LiveView> = {}) {
  let view: LiveView = { expanded: false, maximized: false, pinned: true, ...initial };
  vi.mocked(invoke).mockImplementation(async (cmd: string, args?: Record<string, unknown>) => {
    if (cmd === "live_window_state") return view;
    if (cmd === "live_set_expanded") view = { ...view, expanded: !!args?.expanded, maximized: false };
    else return undefined;
    return view;
  });
}
const expandCalls = () => vi.mocked(invoke).mock.calls.filter(([c]) => c === "live_set_expanded").map(([, a]) => a);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getState).mockResolvedValue(snap());
  vi.mocked(onLiveWindow).mockImplementation(async () => () => {});
  vi.mocked(recordingCommand).mockResolvedValue({ ...snap(), ok: true, action: "kept" } as CommandResult);
  vi.mocked(liveStop).mockReturnValue(new Promise(() => {}));
  shellWith();
});

test("временная встреча: пометка в шапке и «Сохранить как обычную встречу»", async () => {
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap({ temporary: true })));
  expect(screen.getByRole("note")).toHaveTextContent("Временная — не сохранится");
  // Удалять её отдельно незачем: «Закончить» и так удаляет. «Сохранить…» — в меню «Ещё» (0.5).
  await userEvent.click(screen.getByRole("button", { name: "Ещё" }));
  expect(screen.queryByRole("menuitem", { name: "Остановить без сохранения" })).toBeNull();
  await userEvent.click(screen.getByRole("menuitem", { name: "Сохранить как обычную встречу" }));
  expect(recordingCommand).toHaveBeenCalledWith(ep, "keep");
  // Сохранили (record.kept → снимок) — пометка снимается.
  vi.mocked(getState).mockResolvedValueOnce(snap({ temporary: false }));
  await act(async () => { bus().emit("record.kept", { kind: "record.kept", at: 1 }); });
  expect(screen.queryByText("Временная — не сохранится")).toBeNull();
});

test("конец временной встречи спрашивает: «Продолжить» по умолчанию, Esc — отмена", async () => {
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap({ temporary: true })));
  await userEvent.click(screen.getByRole("button", { name: "Закончить временную встречу" }));
  // Свёрнутую панель разворачивают: в строку вопрос не помещается.
  expect(expandCalls()).toEqual([{ expanded: true }]);
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Временная встреча закончится и будет удалена.");
  expect(screen.getByRole("button", { name: "Продолжить" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(liveStop).not.toHaveBeenCalled();
  expect(expandCalls()).toEqual([{ expanded: true }, { expanded: false }]);
  await userEvent.click(screen.getByRole("button", { name: "Закончить временную встречу" }));
  await userEvent.click(await screen.findByRole("button", { name: "Закончить" }));
  expect(liveStop).toHaveBeenCalledWith(ep);
  expect(head()).toHaveTextContent("Останавливаю…");
});

test("«Остановить без сохранения»: вопрос, «Продолжить запись» по умолчанию, «Удалить запись» — /recording/cancel", async () => {
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap()));
  expect(screen.queryByText("Временная — не сохранится")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Ещё" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Остановить без сохранения" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Остановить без сохранения?");
  expect(dialog).toHaveTextContent("будут удалены без возможности восстановления");
  expect(screen.getByRole("button", { name: "Продолжить запись" })).toHaveFocus();
  await userEvent.click(screen.getByRole("button", { name: "Продолжить запись" }));
  expect(recordingCommand).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Ещё" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Остановить без сохранения" }));
  await userEvent.click(await screen.findByRole("button", { name: "Удалить запись" }));
  expect(recordingCommand).toHaveBeenCalledWith(ep, "cancel");
  expect(liveStop).not.toHaveBeenCalled();
  expect(head()).toHaveTextContent("Останавливаю…");
});

test("ассистент, который пишет сам (не к записи резидента), без «Остановить без сохранения»", () => {
  render(<LivePanel endpoint={ep} />);
  act(() => bus().emit("state", snap({ status: "idle", live: live({ attached: false }) })));
  expect(screen.queryByRole("button", { name: "Остановить без сохранения" })).toBeNull();
  expect(screen.getByRole("button", { name: "Остановить и сохранить" })).toBeInTheDocument();
});
