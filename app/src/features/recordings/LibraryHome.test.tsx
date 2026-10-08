import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as api from "../../lib/api";
import type { CommandResult, LibraryItem, Snapshot } from "../../lib/types";
import { LibraryHome, latestOf } from "./LibraryHome";

vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({ onDragDropEvent: vi.fn(async () => () => {}) }),
}));

/** Ничего не выбрано, а записи есть: «Записи» и счётчик, начать или импортировать, последняя встреча. */

const ep = { base: "/api", token: null };
afterEach(() => vi.restoreAllMocks());
const snap = (extra: Partial<Snapshot> = {}): Snapshot => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: true, processes: ["Zoom.exe"], browsers: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, ...extra,
});
const item = (id: string, started_at: string | null, extra: Partial<LibraryItem> = {}): LibraryItem => ({
  id, path: `C:/rec/${id}`, started_at, duration_s: 3720, tracks: { sys: "sys.ogg" }, has_transcript: true,
  has_voices: false, title: null, source: "record", ...extra,
});
const items = [
  item("a", "2026-10-05T15:30:00", { title: "Обсуждение релиза 0.4" }),
  item("b", "2026-10-06T11:00:00", { title: "Планирование спринта" }),
  item("c", null, { title: "Без даты" }),
];

test("последняя встреча — с самой поздней датой; без дат — первая в списке", () => {
  expect(latestOf(items)?.id).toBe("b");
  expect(latestOf([item("x", null), item("y", null)])?.id).toBe("x");
  expect(latestOf([])).toBeNull();
});

test("заголовок «Записи» со счётчиком, карточка «Новая встреча» на тихом сиянии — без полного сияния", () => {
  const { container } = render(<LibraryHome endpoint={ep} snapshot={snap()} items={items} jobs={[]} onOpen={() => {}} />);
  expect(screen.getByRole("heading", { level: 2, name: "Записи" })).toBeInTheDocument();
  expect(screen.getByText("3 записи")).toBeInTheDocument();
  const start = screen.getByRole("region", { name: "Новая встреча" });
  expect(start).toHaveClass("card", "aurora-wash");
  expect(within(start).getByRole("button", { name: "Начать запись" })).toHaveClass("btn--primary");
  expect(within(start).getByRole("button", { name: "Импортировать файл" })).toHaveClass("btn--outline");
  expect(within(start).getByText("Автозапись включена: Zoom")).toBeInTheDocument();
  // Сияние целиком — только у «Записей пока нет».
  expect(container.querySelector(".aurora")).toBeNull();
  expect(screen.queryByText("Выберите запись")).toBeNull();
});

test("«Последняя»: название, дата и длительность, состояние; нажатие открывает запись", async () => {
  const onOpen = vi.fn();
  render(<LibraryHome endpoint={ep} snapshot={snap()} items={[...items, item("d", "2026-10-07T09:00:00", {
    title: "Свежая", has_transcript: false })]} jobs={[]} onOpen={onOpen} />);
  const last = screen.getByRole("region", { name: "Последняя" });
  const open = within(last).getByRole("button", { name: /Свежая/ });
  expect(open).toHaveTextContent(/1 ч 02 мин/);
  expect(within(last).getByText("Не расшифровано")).toBeInTheDocument();
  await userEvent.click(open);
  expect(onOpen).toHaveBeenCalledWith("d");
});

test("«Начать запись» — команда start, снимок наверх; во время записи кнопка недоступна", async () => {
  const started = { ...snap(), status: "recording" as const, ok: true, action: "start" } as CommandResult;
  const command = vi.spyOn(api, "recordingCommand").mockResolvedValue(started);
  const onSnapshot = vi.fn();
  const { rerender } = render(<LibraryHome endpoint={ep} snapshot={snap()} items={items} jobs={[]} onOpen={() => {}}
    onSnapshot={onSnapshot} />);
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  expect(command).toHaveBeenCalledWith(ep, "start");
  await waitFor(() => expect(onSnapshot).toHaveBeenCalledWith(started));
  rerender(<LibraryHome endpoint={ep} snapshot={snap({ status: "recording" })} items={items} jobs={[]}
    onOpen={() => {}} />);
  expect(screen.getByRole("button", { name: "Начать запись" })).toBeDisabled();
});
