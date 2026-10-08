import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import * as api from "../lib/api";
import type { AssistantInfo, CommandResult, LiveStatus, Snapshot } from "../lib/types";
import { RecordingBadge } from "./RecordingBadge";

/** «Остановить без сохранения» и «Временная встреча с ассистентом» у кнопки записи. */

const ep = { base: "/api", token: null };
afterEach(() => vi.restoreAllMocks());
const live = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: false, starting: false, stopping: false, folder: null, error: null, started_at: null, ...o,
});
const base = (o: Partial<Snapshot> = {}): Snapshot => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, live: live(), ...o,
});
const recording = (o: Partial<Snapshot> = {}) =>
  base({ status: "recording", source: "manual", folder: "D:/rec/f", elapsed_s: 60, ...o });
const assistant = (o: Partial<AssistantInfo> = {}): AssistantInfo => ({
  provider: "claude", setting: "auto", available: {}, knowledge_dir: null, checking: false, ...o,
});
const reply = (s: Snapshot, action: string): CommandResult => ({ ...s, ok: true, action });

test("простой: в меню кнопки записи есть «Временная встреча с ассистентом» — /live/start с temporary", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const start = vi.spyOn(api, "liveStart").mockResolvedValue({ ok: true, ...live({ starting: true }) });
  render(<RecordingBadge endpoint={ep} snapshot={base()} />);
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  const item = await screen.findByRole("menuitem", { name: /Временная встреча с ассистентом/ });
  await waitFor(() => expect(item).toBeEnabled());
  expect(item).toHaveTextContent("не сохранится");
  await userEvent.click(item);
  expect(start).toHaveBeenCalledWith(ep, { temporary: true });
});

test("без подключённой модели временная встреча недоступна", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant({ provider: null }));
  const start = vi.spyOn(api, "liveStart");
  render(<RecordingBadge endpoint={ep} snapshot={base()} />);
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  const item = await screen.findByRole("menuitem", { name: /Временная встреча/ });
  await waitFor(() => expect(item).toBeDisabled());
  await userEvent.click(item);
  expect(start).not.toHaveBeenCalled();
});

test("«Остановить без сохранения…»: вопрос, фокус на «Продолжить запись», Esc — ничего не удаляет", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const command = vi.spyOn(api, "recordingCommand");
  render(<RecordingBadge endpoint={ep} snapshot={recording()} />);
  await userEvent.click(screen.getByRole("button", { name: "Остановить и сохранить" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: /Остановить без сохранения/ }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Остановить без сохранения?");
  expect(dialog).toHaveTextContent(
    "Запись и всё, что с ней связано, будут удалены без возможности восстановления.");
  expect(screen.getByRole("button", { name: "Продолжить запись" })).toHaveFocus();
  expect(screen.getByRole("button", { name: "Удалить запись" })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(command).not.toHaveBeenCalled();
  // Вопрос закрыт — фокус снова на кнопке записи.
  await waitFor(() => expect(screen.getByRole("button", { name: "Остановить и сохранить" })).toHaveFocus());
});

test("«Удалить запись» — /recording/cancel, ответ применяется сразу", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const command = vi.spyOn(api, "recordingCommand").mockResolvedValue(reply(base(), "cancelled"));
  function Harness() {
    const [s, setS] = useState(recording());
    return <RecordingBadge endpoint={ep} snapshot={s} onSnapshot={setS} />;
  }
  render(<Harness />);
  await userEvent.click(screen.getByRole("button", { name: "Остановить и сохранить" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: /Остановить без сохранения/ }));
  await userEvent.click(screen.getByRole("button", { name: "Удалить запись" }));
  expect(command).toHaveBeenCalledWith(ep, "cancel");
  expect(await screen.findByRole("button", { name: "Начать запись" })).toBeInTheDocument();
});

test("временная встреча: пометка в подсказке, «Закончить временную встречу» спрашивает, «Продолжить» по умолчанию", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const command = vi.spyOn(api, "recordingCommand").mockResolvedValue(reply(base(), "stopped"));
  render(<RecordingBadge endpoint={ep} snapshot={recording({ source: "live", temporary: true })} />);
  const end = screen.getByRole("button", { name: "Закончить временную встречу" });
  expect(end).toHaveAccessibleDescription(/Идёт запись · 01:00.*Временная — не сохранится/);
  expect(screen.queryByRole("button", { name: "Остановить и сохранить" })).toBeNull();
  await userEvent.click(end);
  // Первый пункт меню — то же действие, что раньше у кнопки: закончить (с вопросом).
  const first = (await screen.findAllByRole("menuitem"))[0]!;
  expect(first).toHaveTextContent(/^Закончить временную встречу/);
  await userEvent.click(first);
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Временная встреча закончится и будет удалена.");
  expect(screen.getByRole("button", { name: "Продолжить" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(command).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Закончить временную встречу" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: /^Закончить временную встречу/ }));
  await userEvent.click(await screen.findByRole("button", { name: "Закончить" }));
  expect(command).toHaveBeenCalledWith(ep, "stop");
});

test("временная встреча: «Сохранить как обычную встречу» в меню кнопки, пометка снимается", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const kept = recording({ source: "live", temporary: false });
  const command = vi.spyOn(api, "recordingCommand").mockResolvedValue(reply(kept, "kept"));
  function Harness() {
    const [s, setS] = useState(recording({ source: "live", temporary: true }));
    return <RecordingBadge endpoint={ep} snapshot={s} onSnapshot={setS} />;
  }
  render(<Harness />);
  await userEvent.click(screen.getByRole("button", { name: "Закончить временную встречу" }));
  await screen.findByRole("menu", { name: "Действия с записью" });
  expect(screen.queryByRole("menuitem", { name: /Остановить без сохранения/ })).toBeNull();
  await userEvent.click(await screen.findByRole("menuitem", { name: /Сохранить как обычную встречу/ }));
  expect(command).toHaveBeenCalledWith(ep, "keep");
  await waitFor(() => expect(screen.getByRole("tooltip")).not.toHaveTextContent("Временная — не сохранится"));
  // Теперь это обычная запись: «Остановить и сохранить» без вопроса.
  await userEvent.click(screen.getByRole("button", { name: "Остановить и сохранить" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: /^Остановить и сохранить/ }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(command).toHaveBeenLastCalledWith(ep, "stop");
});

test("вопрос честно говорит, чью историю ассистента удалить нечем (forget_gaps)", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  render(<RecordingBadge endpoint={ep} snapshot={recording({ forget_gaps: ["Codex"] })} />);
  await userEvent.click(screen.getByRole("button", { name: "Остановить и сохранить" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: /Остановить без сохранения/ }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("будут удалены без возможности восстановления");
  expect(dialog).toHaveTextContent("История ассистента в Codex останется в самом Codex.");
});
