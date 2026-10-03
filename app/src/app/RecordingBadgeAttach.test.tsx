import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import * as api from "../lib/api";
import type { AssistantInfo, LiveStatus, Snapshot } from "../lib/types";
import { RecordingBadge } from "./RecordingBadge";

/** «Включить ассистента» / «Выключить ассистента» посреди обычной записи. */

const ep = { base: "/api", token: null };
afterEach(() => vi.restoreAllMocks());
const live = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: false, starting: false, stopping: false, folder: null, error: null, started_at: null, ...o,
});
const recording = (l: LiveStatus = live()): Snapshot => ({
  status: "recording", source: "manual", folder: "D:/rec/f", elapsed_s: 754, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, live: l,
});
const assistant = (o: Partial<AssistantInfo> = {}): AssistantInfo => ({
  provider: "claude", setting: "auto", available: {}, knowledge_dir: null, checking: false, ...o,
});
const openMenu = () => userEvent.click(screen.getByRole("button", { name: "Ассистент в этой записи" }));

test("во время записи «Стоп ▾»: «Включить ассистента» — ответ применяется сразу", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const attach = vi.spyOn(api, "liveAttach").mockResolvedValue(
    { ok: true, ...live({ starting: true, attached: true, folder: "D:/rec/f" }) });
  const stop = vi.spyOn(api, "recordingCommand");
  function Harness() {
    const [s, setS] = useState(recording());
    return <RecordingBadge endpoint={ep} snapshot={s} onSnapshot={setS} />;
  }
  render(<Harness />);
  expect(screen.getByText(/REC/)).toHaveTextContent("12:34");
  await openMenu();
  const item = await screen.findByRole("menuitem", { name: /Включить ассистента/ });
  await waitFor(() => expect(item).toBeEnabled());
  expect(item).toHaveTextContent("догонит начало встречи");
  await userEvent.click(item);
  expect(attach).toHaveBeenCalledWith(ep);
  expect(stop).not.toHaveBeenCalled(); // запись не останавливали
  expect(await screen.findByText("Ассистент запускается…")).toBeInTheDocument();
  expect(screen.getByText(/REC/)).toHaveTextContent("12:34"); // всё та же запись
  expect(screen.queryByRole("menu")).toBeNull();
});

test("без подключённой модели «Включить ассистента» неактивен, с подсказкой", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant({ provider: null }));
  const attach = vi.spyOn(api, "liveAttach");
  render(<RecordingBadge endpoint={ep} snapshot={recording()} />);
  await openMenu();
  const item = await screen.findByRole("menuitem", { name: /Включить ассистента/ });
  await waitFor(() => expect(item).toBeDisabled());
  expect(screen.getByRole("menu")).toHaveTextContent("Подключите Claude Code или Codex в настройках");
  await userEvent.click(item);
  expect(attach).not.toHaveBeenCalled();
});

test("включён — «· ассистент» у REC и «Выключить ассистента» (запись продолжается)", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  const detach = vi.spyOn(api, "liveDetach").mockResolvedValue(
    { ok: true, action: "stopping", ...live({ active: true, stopping: true, attached: true }) });
  function Harness() {
    const [s, setS] = useState(recording(live({ active: true, attached: true, folder: "D:/rec/f" })));
    return <RecordingBadge endpoint={ep} snapshot={s} onSnapshot={setS} />;
  }
  render(<Harness />);
  expect(screen.getByText(/REC/)).toHaveTextContent("· ассистент");
  await openMenu();
  const item = await screen.findByRole("menuitem", { name: /Выключить ассистента/ });
  expect(item).toHaveTextContent("запись продолжится");
  await userEvent.click(item);
  expect(detach).toHaveBeenCalledWith(ep);
  expect(await screen.findByText("Ассистент выключается…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Стоп" })).toBeEnabled(); // запись можно остановить
});

test("отказ резидента виден рядом с кнопкой", async () => {
  vi.spyOn(api, "getAssistant").mockResolvedValue(assistant());
  vi.spyOn(api, "liveAttach").mockRejectedValue(new Error("Ассистент ещё запускается или останавливается"));
  render(<RecordingBadge endpoint={ep} snapshot={recording()} />);
  await openMenu();
  const item = await screen.findByRole("menuitem", { name: /Включить ассистента/ });
  await waitFor(() => expect(item).toBeEnabled());
  await userEvent.click(item);
  expect(await screen.findByRole("alert")).toHaveTextContent("Ассистент ещё запускается");
});

test("Стоп во время записи с ассистентом — остановка самой записи", async () => {
  const stop = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
  const liveStop = vi.spyOn(api, "liveStop");
  render(<RecordingBadge endpoint={ep} snapshot={recording(live({ active: true, attached: true }))} />);
  await userEvent.click(screen.getByRole("button", { name: "Стоп" }));
  expect(stop).toHaveBeenCalledWith(ep, "stop");
  expect(liveStop).not.toHaveBeenCalled();
});
