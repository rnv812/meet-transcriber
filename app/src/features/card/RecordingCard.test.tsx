import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingCard } from "./RecordingCard";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { Job, Recording, Transcript } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(),
  patchRecording: vi.fn(),
  deleteRecording: vi.fn(),
  transcribe: vi.fn(),
  exportRecording: vi.fn(),
}));
vi.mock("../../lib/shell", () => ({
  inTauri: () => true,
  saveText: vi.fn(async () => "x"),
  openFolder: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
const transcript: Transcript = {
  version: 1, title: null,
  segments: [
    { start: 0, end: 5, speaker: "Демьян Петров", text: "Привет всем", uncertain: false },
    { start: 6, end: 9, speaker: "Спикер 2", text: "Здравствуйте", uncertain: true },
  ],
};
const base: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-09-30T10:00:00", duration_s: 1800,
  tracks: { sys: "s.wav" }, has_transcript: true, has_voices: false, title: "Встреча", source: "record",
};
const load = (extra: Partial<Recording> = {}, t: Transcript | null = transcript) =>
  vi.mocked(api.getRecording).mockResolvedValue({ ...base, ...extra, transcript: t });

beforeEach(() => vi.clearAllMocks());

test("ready: реплики и участники", async () => {
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  expect(await screen.findByText("Привет всем")).toBeInTheDocument();
  expect(screen.getByText("Здравствуйте")).toBeInTheDocument();
  expect(screen.getByText("(нахлёст)")).toBeInTheDocument();
  expect(screen.getAllByText("Демьян Петров").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: /▶ 00:00/ })).toBeInTheDocument();
});

test("клик по безымянному спикеру зовёт onNameSpeaker", async () => {
  load();
  const onNameSpeaker = vi.fn();
  render(<RecordingCard id="r1" endpoint={ep} onNameSpeaker={onNameSpeaker} />);
  await screen.findByText("Привет всем");
  await userEvent.click(screen.getAllByRole("button", { name: /Спикер 2/ })[0]!);
  expect(onNameSpeaker).toHaveBeenCalledWith("Спикер 2");
});

test("удаление во время расшифровки: ошибка видна", async () => {
  load();
  vi.mocked(api.deleteRecording).mockRejectedValue(new api.ApiError(400, "идёт расшифровка, подождите"));
  const onDeleted = vi.fn();
  render(<RecordingCard id="r1" endpoint={ep} onDeleted={onDeleted} />);
  await screen.findByText("Привет всем");
  await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
  expect(screen.getByText("Удалить запись и транскрипт? Это необратимо.")).toBeInTheDocument();
  await userEvent.click(screen.getAllByRole("button", { name: "Удалить" })[0]!);
  expect(await screen.findByRole("alert")).toHaveTextContent("идёт расшифровка, подождите");
  expect(onDeleted).not.toHaveBeenCalled();
});

test("удаление: onDeleted после успеха", async () => {
  load();
  vi.mocked(api.deleteRecording).mockResolvedValue({ ok: true });
  const onDeleted = vi.fn();
  render(<RecordingCard id="r1" endpoint={ep} onDeleted={onDeleted} />);
  await screen.findByText("Привет всем");
  await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
  await userEvent.click(screen.getAllByRole("button", { name: "Удалить" })[0]!);
  await waitFor(() => expect(onDeleted).toHaveBeenCalled());
});

test("failed import: «Повторить» зовёт transcribe", async () => {
  load({ has_transcript: false, source: "import", tracks: {} }, null);
  vi.mocked(api.transcribe).mockResolvedValue({} as Job);
  const jobs: Job[] = [{
    id: "j1", kind: "import", folder: "C:/rec/r1", state: "failed", stage: null, label: null,
    done: null, total: null, note: null, result: null, error: "ffmpeg упал",
  }];
  render(<RecordingCard id="r1" endpoint={ep} jobs={jobs} />);
  expect(await screen.findByText("ffmpeg упал")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(api.transcribe).toHaveBeenCalledWith(ep, "r1");
  expect(screen.getByRole("button", { name: "Удалить" })).toBeInTheDocument();
});

test("untranscribed: пустое состояние и «Расшифровать»", async () => {
  load({ has_transcript: false }, null);
  vi.mocked(api.transcribe).mockResolvedValue({} as Job);
  render(<RecordingCard id="r1" endpoint={ep} />);
  expect(await screen.findByText("Запись не расшифрована")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Расшифровать" }));
  expect(api.transcribe).toHaveBeenCalledWith(ep, "r1");
});

test("recording: «Идёт запись…»", async () => {
  load({ has_transcript: false }, null);
  const snapshot = { status: "recording", folder: "C:/rec/r1" } as never;
  render(<RecordingCard id="r1" endpoint={ep} snapshot={snapshot} />);
  expect(await screen.findByText("Идёт запись…")).toBeInTheDocument();
});

test("экспорт srt сохраняет файл", async () => {
  load();
  vi.mocked(api.exportRecording).mockResolvedValue({ filename: "Acme.srt", content: "1\n..." });
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Привет всем");
  await userEvent.click(screen.getByRole("button", { name: /Экспорт/ }));
  await userEvent.click(screen.getByRole("menuitem", { name: "srt" }));
  await waitFor(() => expect(shell.saveText).toHaveBeenCalledWith("Acme.srt", "1\n..."));
  expect(api.exportRecording).toHaveBeenCalledWith(ep, "r1", "srt");
});

test("переименование: Enter сохраняет, Esc отменяет", async () => {
  load();
  vi.mocked(api.patchRecording).mockResolvedValue({ ...base, title: "Acme" });
  render(<RecordingCard id="r1" endpoint={ep} />);
  await userEvent.click(await screen.findByRole("heading", { name: "Встреча" }));
  let input = screen.getByRole("textbox", { name: "Название записи" });
  await userEvent.clear(input);
  await userEvent.type(input, "Acme{Enter}");
  await waitFor(() => expect(api.patchRecording).toHaveBeenCalledWith(ep, "r1", { title: "Acme" }));
  expect(await screen.findByRole("heading", { name: "Acme" })).toBeInTheDocument();

  vi.mocked(api.patchRecording).mockClear();
  await userEvent.click(screen.getByRole("heading", { name: "Acme" }));
  input = screen.getByRole("textbox", { name: "Название записи" });
  await userEvent.type(input, "лишнее{Escape}");
  expect(api.patchRecording).not.toHaveBeenCalled();
  expect(screen.getByRole("heading", { name: "Acme" })).toBeInTheDocument();
});

test("пустое название не сохраняется", async () => {
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  await userEvent.click(await screen.findByRole("heading", { name: "Встреча" }));
  const input = screen.getByRole("textbox", { name: "Название записи" });
  await userEvent.clear(input);
  await userEvent.type(input, "   {Enter}");
  expect(api.patchRecording).not.toHaveBeenCalled();
});
