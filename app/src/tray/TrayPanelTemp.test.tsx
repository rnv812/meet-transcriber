import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as api from "../lib/api";
import type { AssistantInfo, CommandResult, LiveStatus, Snapshot } from "../lib/types";
import { TrayPanel } from "./TrayPanel";

/** Панель строки меню: «Остановить без сохранения» и временная встреча. */

const ep = { base: "/api", token: null };
afterEach(() => vi.restoreAllMocks());

const live = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: false, starting: false, stopping: false, folder: null, error: null, started_at: null, ...o,
});
const snap = (extra: Partial<Snapshot> = {}): Snapshot => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, live: live(), ...extra,
} as Snapshot);
const recording = (extra: Partial<Snapshot> = {}) =>
  snap({ status: "recording", source: "manual", folder: "D:/rec/f", elapsed_s: 30, ...extra });
const temporary = () => recording({
  source: "live", temporary: true, folder: "C:/data/tmp-meetings/ab/2026-10-07_15-00",
  live: live({ active: true, attached: true, ready: true }),
});
const provider = (p: string | null): AssistantInfo =>
  ({ provider: p, setting: "auto", available: {}, knowledge_dir: null, checking: false });
const done = (s: Snapshot, action: string): CommandResult => ({ ...s, ok: true, action });

function panel(props: Partial<Parameters<typeof TrayPanel>[0]> = {}) {
  const onSnapshot = vi.fn();
  render(
    <TrayPanel endpoint={ep} snapshot={snap()} snapshotAt={Date.now()} online recent={null}
      justStopped={false} assistant={null} onSnapshot={onSnapshot} onOpen={vi.fn()} {...props} />,
  );
  return { onSnapshot };
}

test("простой: «Временная встреча с ассистентом» — /live/start с temporary", async () => {
  const start = vi.spyOn(api, "liveStart").mockResolvedValue({ ok: true, ...live({ starting: true }) });
  panel();
  const button = screen.getByRole("button", { name: "Временная встреча с ассистентом" });
  expect(button).toHaveAttribute("title", expect.stringContaining("не сохранится"));
  await userEvent.click(button);
  expect(start).toHaveBeenCalledWith(ep, { temporary: true });
});

test("без подключённой модели временная встреча неактивна", () => {
  panel({ assistant: provider(null) });
  expect(screen.getByRole("button", { name: "Временная встреча с ассистентом" })).toBeDisabled();
});

test("запись: «Остановить без сохранения…» спрашивает; «Отмена» по умолчанию, Esc панель не прячет", async () => {
  const command = vi.spyOn(api, "recordingCommand").mockResolvedValue(done(snap(), "cancelled"));
  // Как у окна панели (TrayWindow): Esc на документе прячет панель.
  const hide = vi.fn();
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") hide(); };
  document.addEventListener("keydown", onKey);
  try {
    const { onSnapshot } = panel({ snapshot: recording() });
    await userEvent.click(screen.getByRole("button", { name: /Остановить без сохранения/ }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("Остановить без сохранения?");
    expect(dialog).toHaveTextContent("Запись и всё, что с ней связано, будут удалены без возможности восстановления.");
    expect(screen.getByRole("button", { name: "Отмена" })).toHaveFocus();
    // Вопрос — вместо кнопок: «Остановить» сейчас не нажать по ошибке.
    expect(screen.queryByRole("button", { name: "Остановить" })).toBeNull();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(hide).not.toHaveBeenCalled();
    expect(command).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Остановить без сохранения/ }));
    await userEvent.click(screen.getByRole("button", { name: "Удалить запись" }));
    expect(command).toHaveBeenCalledWith(ep, "cancel");
    expect(onSnapshot).toHaveBeenCalledWith(done(snap(), "cancelled"));
  } finally {
    document.removeEventListener("keydown", onKey);
  }
});

test("временная встреча: пометка, «Закончить» спрашивает, «Сохранить как обычную встречу»", async () => {
  const command = vi.spyOn(api, "recordingCommand").mockResolvedValue(done(snap(), "stopped"));
  panel({ snapshot: temporary() });
  expect(screen.getByText("Временная — не сохранится")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Остановить без сохранения/ })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Закончить" }));
  expect(screen.getByRole("alertdialog")).toHaveTextContent("Временная встреча закончится и будет удалена.");
  expect(screen.getByRole("button", { name: "Продолжить" })).toHaveFocus();
  await userEvent.click(screen.getByRole("button", { name: "Продолжить" }));
  expect(command).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить как обычную встречу" }));
  expect(command).toHaveBeenCalledWith(ep, "keep");
  await userEvent.click(screen.getByRole("button", { name: "Закончить" }));
  await userEvent.click(screen.getByRole("button", { name: "Закончить" }));
  expect(command).toHaveBeenLastCalledWith(ep, "stop");
});
