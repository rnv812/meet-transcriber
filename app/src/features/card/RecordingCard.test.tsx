import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingCard } from "./RecordingCard";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { Job, Recording, Transcript } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(),
  getSettings: vi.fn(),
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

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue({ recording: { speaker_name: "Демьян Петров" } });
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.load = vi.fn();
});

test("ready: реплики и участники", async () => {
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  expect(await screen.findByText("Привет всем")).toBeInTheDocument();
  expect(screen.getByText("Здравствуйте")).toBeInTheDocument();
  expect(screen.getByText("(нахлёст)")).toBeInTheDocument();
  expect(screen.getAllByText("Демьян Петров").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: /▶ 00:00/ })).toBeInTheDocument();
});

test("клик по безымянному спикеру открывает «Кто это?», Esc закрывает", async () => {
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Привет всем");
  await userEvent.click(screen.getAllByRole("button", { name: /Спикер 2/ })[0]!);
  expect(await screen.findByRole("dialog", { name: "Кто это?" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Кто это?" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
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

test("первый клик по реплике: src, currentTime и play сразу", async () => {
  load({ tracks: { sys: "s.wav", mic: "m.wav" } });
  const { container } = render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Привет всем");
  await userEvent.click(screen.getAllByRole("button", { name: /▶/ })[1]!);
  const a = container.querySelector("audio")!;
  expect(a.getAttribute("src")).toBe(api.audioUrl(ep, "r1", "sys"));
  expect(a.currentTime).toBe(6);
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
});

test("реплика владельца микрофона играет дорожку mic", async () => {
  load({ tracks: { sys: "s.wav", mic: "m.wav" } });
  const { container } = render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Привет всем");
  await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
  await new Promise((r) => setTimeout(r, 0));
  await userEvent.click(screen.getAllByRole("button", { name: /▶/ })[0]!);
  expect(container.querySelector("audio")!.getAttribute("src")).toBe(api.audioUrl(ep, "r1", "mic"));
});

test("без дорожек кнопок воспроизведения нет", async () => {
  load({ tracks: {} });
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Привет всем");
  expect(screen.queryByRole("button", { name: /▶/ })).toBeNull();
});

test("устаревший ответ не перекрывает текущую запись", async () => {
  let resolveA!: (v: never) => void;
  vi.mocked(api.getRecording).mockImplementation((_ep, rid) =>
    rid === "a" ? new Promise((r) => { resolveA = r as never; })
      : Promise.resolve({ ...base, id: "b", title: "Запись B", transcript }));
  const { rerender } = render(<RecordingCard id="a" endpoint={ep} />);
  rerender(<RecordingCard id="b" endpoint={ep} />);
  expect(await screen.findByRole("heading", { name: "Запись B" })).toBeInTheDocument();
  resolveA({ ...base, id: "a", title: "Запись A", transcript } as never);
  await new Promise((r) => setTimeout(r, 0));
  expect(screen.getByRole("heading", { name: "Запись B" })).toBeInTheDocument();
});

test("переименование вызывает onChanged", async () => {
  load();
  vi.mocked(api.patchRecording).mockResolvedValue({ ...base, title: "Z" });
  const onChanged = vi.fn();
  render(<RecordingCard id="r1" endpoint={ep} onChanged={onChanged} />);
  await userEvent.click(await screen.findByRole("heading", { name: "Встреча" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Название записи" }), "Z{Enter}");
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
});

test("записи нет (404, например старая ссылка из уведомления) — понятное пустое состояние", async () => {
  vi.mocked(api.getRecording).mockRejectedValue(new api.ApiError(404, "записи нет"));
  render(<RecordingCard id="gone" endpoint={ep} jobs={[]} snapshot={null} people={[]} />);
  expect(await screen.findByText("Запись не найдена")).toBeInTheDocument();
  expect(screen.getByText("Возможно, её удалили. Выберите другую в списке.")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("другая ошибка загрузки показывается как есть", async () => {
  vi.mocked(api.getRecording).mockRejectedValue(new api.ApiError(500, "сломалось"));
  render(<RecordingCard id="x" endpoint={ep} jobs={[]} snapshot={null} people={[]} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("сломалось");
});
