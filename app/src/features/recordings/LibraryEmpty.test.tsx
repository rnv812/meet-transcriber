import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { CommandResult, Snapshot } from "../../lib/types";
import { LibraryEmpty } from "./LibraryEmpty";

/** Пустая библиотека: сияние, «Записей пока нет», «Начать запись», «Импортировать файл», автозапись. */

const ep = { base: "/api", token: null };
afterEach(() => vi.restoreAllMocks());
const snap = (o: Partial<Snapshot["auto_record"]> = {}, extra: Partial<Snapshot> = {}): Snapshot => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: true, processes: ["Zoom.exe", "ms-teams.exe", "Teams.exe", "YandexTelemost.exe", "Custom.exe"],
    browsers: ["chrome.exe"], grace_seconds: 0, state: null, mic: null, render: null, ...o },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, ...extra,
});

test("сияние с дрейфом, заголовок, пояснение и обе кнопки", () => {
  const { container } = render(<LibraryEmpty endpoint={ep} snapshot={snap()} />);
  const block = container.querySelector(".empty.aurora.aurora--live");
  expect(block).not.toBeNull();
  expect(screen.getByRole("heading", { name: "Записей пока нет" })).toBeInTheDocument();
  expect(screen.getByText(/Meet начнёт запись сам, когда начнётся звонок/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Начать запись" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Импортировать файл" })).toBeInTheDocument();
});

test("плашка автозаписи — понятные имена программ из auto_record.processes, без повторов, и браузеры", () => {
  render(<LibraryEmpty endpoint={ep} snapshot={snap()} />);
  expect(screen.getByText("Автозапись включена: Zoom, Microsoft Teams, Яндекс Телемост, Custom, браузеры"))
    .toBeInTheDocument();
});

test("автозапись выключена — так и сказано, пояснение про ручной запуск", () => {
  render(<LibraryEmpty endpoint={ep} snapshot={snap({ enabled: false })} />);
  expect(screen.getByText("Автозапись выключена")).toBeInTheDocument();
  expect(screen.getByText("Нажмите «Начать запись» или перетащите файл")).toBeInTheDocument();
});

test("без снимка резидента плашки нет, пояснение — прежнее", () => {
  render(<LibraryEmpty endpoint={ep} snapshot={null} />);
  expect(screen.queryByText(/Автозапись/)).toBeNull();
  expect(screen.getByText("Нажмите «Начать запись» или перетащите файл")).toBeInTheDocument();
});

test("«Начать запись» — команда start, снимок уходит наверх", async () => {
  const started = { ...snap(), status: "recording" as const, ok: true, action: "start" } as CommandResult;
  const command = vi.spyOn(api, "recordingCommand").mockResolvedValue(started);
  const onSnapshot = vi.fn();
  render(<LibraryEmpty endpoint={ep} snapshot={snap()} onSnapshot={onSnapshot} />);
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  expect(command).toHaveBeenCalledWith(ep, "start");
  expect(onSnapshot).toHaveBeenCalledWith(started);
});

test("«Импортировать файл» — выбор файла и импорт", async () => {
  vi.spyOn(shell, "inTauri").mockReturnValue(true);
  vi.spyOn(shell, "pickMedia").mockResolvedValue("D:/calls/call.m4a");
  const imp = vi.spyOn(api, "importFile").mockResolvedValue({ ok: true } as never);
  const onImported = vi.fn();
  render(<LibraryEmpty endpoint={ep} snapshot={snap()} onImported={onImported} />);
  await userEvent.click(screen.getByRole("button", { name: "Импортировать файл" }));
  expect(imp).toHaveBeenCalledWith(ep, "D:/calls/call.m4a");
  expect(onImported).toHaveBeenCalled();
});

test("ошибка импорта видна", async () => {
  vi.spyOn(shell, "inTauri").mockReturnValue(true);
  vi.spyOn(shell, "pickMedia").mockResolvedValue("D:/calls/call.m4a");
  vi.spyOn(api, "importFile").mockRejectedValue(new Error("формат не поддерживается"));
  render(<LibraryEmpty endpoint={ep} snapshot={snap()} />);
  await userEvent.click(screen.getByRole("button", { name: "Импортировать файл" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("call.m4a: формат не поддерживается");
});
