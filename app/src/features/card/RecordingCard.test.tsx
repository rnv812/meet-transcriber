import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingCard } from "./RecordingCard";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { Job, LiveStatus, Recording, Transcript } from "../../lib/types";
import { FakeEventSource } from "../../test/setup";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(),
  getSettings: vi.fn(),
  patchRecording: vi.fn(),
  deleteRecording: vi.fn(),
  transcribe: vi.fn(),
  exportRecording: vi.fn(),
  cancelJob: vi.fn(),
  getDiagnostics: vi.fn(),
  getAssistant: vi.fn(),
  getSummary: vi.fn(),
  getQa: vi.fn(),
  liveAsk: vi.fn(),
  kbExport: vi.fn(),
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
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: null, setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
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
  expect(screen.getByText("Удалить запись и расшифровку? Это действие нельзя отменить.")).toBeInTheDocument();
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

const live = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: true, starting: false, stopping: false, folder: "C:\\rec\\r1", error: null, started_at: 1, ...o,
});

test("запись с ассистентом: в карточке живая лента, дайджест и вопросы", async () => {
  load({ has_transcript: false, source: "live" }, null);
  vi.mocked(api.liveAsk).mockResolvedValue({ answer: "Обсуждали релиз" });
  const snapshot = { status: "idle", folder: null, live: live() } as never;
  render(<RecordingCard id="r1" endpoint={ep} snapshot={snapshot} />);
  expect(await screen.findByText("Идёт запись с ассистентом")).toBeInTheDocument();
  expect(screen.queryByText("Идёт запись…")).toBeNull();
  // Поток открывается эффектом после первой отрисовки ленты — под нагрузкой
  // не обязательно к этой строке.
  const stream = await vi.waitFor(() => {
    const found = FakeEventSource.instances.find((s) => s.url.startsWith("/api/live/events"));
    if (!found) throw new Error("поток ассистента ещё не открыт");
    return found;
  });
  act(() => {
    stream.emit("state", { digest: "- релиз в пятницу", transcript: [], status: null });
    stream.emit("line", { t: 65, speaker: "Демьян", text: "давайте начнём" }, 0);
  });
  expect(screen.getByRole("log")).toHaveTextContent("давайте начнём");
  expect(screen.getByText("релиз в пятницу")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Что я пропустил?" }));
  expect(api.liveAsk).toHaveBeenCalledWith(ep, "Что я пропустил за последние минуты?");
  expect(await screen.findByText("Обсуждали релиз")).toBeInTheDocument();
});

test("ассистент пишет другую папку — у этой записи обычный статус", async () => {
  load({ has_transcript: false }, null);
  const snapshot = { status: "idle", folder: null, live: live({ folder: "C:/rec/other" }) } as never;
  render(<RecordingCard id="r1" endpoint={ep} snapshot={snapshot} />);
  expect(await screen.findByText("Запись не расшифрована")).toBeInTheDocument();
  expect(screen.queryByText("Идёт запись с ассистентом")).toBeNull();
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

test("плеер карточки играет сведённую дорожку playback — обе стороны звонка", async () => {
  load({ tracks: { sys: "s.wav", mic: "m.wav" } });
  const { container } = render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Привет всем");
  const audios = container.querySelectorAll("audio");
  expect(audios).toHaveLength(1);
  expect(audios[0]!.getAttribute("src")).toBe(api.audioUrl(ep, "r1", "playback"));
  expect(screen.getByRole("group", { name: "Проигрыватель записи" })).toBeInTheDocument();
  expect(screen.getByText("30:00")).toBeInTheDocument(); // длительность из карточки до метаданных
});

test("клик по реплике перематывает общий плеер и запускает его — и для собеседника, и для владельца микрофона",
  async () => {
    load({ tracks: { sys: "s.wav", mic: "m.wav" } });
    const { container } = render(<RecordingCard id="r1" endpoint={ep} />);
    await screen.findByText("Привет всем");
    const audio = container.querySelector("audio")!;
    await userEvent.click(screen.getAllByRole("button", { name: /▶/ })[1]!);
    expect(audio.currentTime).toBe(6);
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
    // Реплика владельца микрофона (переименованного) — тот же источник, без смены дорожки.
    await userEvent.click(screen.getAllByRole("button", { name: /▶/ })[0]!);
    expect(audio.currentTime).toBe(0);
    expect(audio.getAttribute("src")).toBe(api.audioUrl(ep, "r1", "playback"));
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2);
  });

test("без дорожек: кнопок воспроизведения нет, внизу — «Аудио недоступно»", async () => {
  load({ tracks: {} });
  const { container } = render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Привет всем");
  expect(screen.queryByRole("button", { name: /▶/ })).toBeNull();
  expect(container.querySelector("audio")).toBeNull();
  expect(screen.getByText("Аудио недоступно")).toBeInTheDocument();
});

test("дорожка не загрузилась — «Аудио недоступно», реплики не перематывают", async () => {
  load({ tracks: { sys: "s.wav", mic: "m.wav" } });
  const { container } = render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Привет всем");
  expect(screen.getAllByRole("button", { name: /▶/ }).length).toBeGreaterThan(0);
  fireEvent.error(container.querySelector("audio")!);
  expect(screen.getByText("Аудио недоступно")).toBeInTheDocument();
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

const job = (o: Partial<Job> = {}): Job => ({
  id: "j1", kind: "transcribe", folder: "C:/rec/r1", state: "running", stage: "asr", label: null,
  done: 1, total: 4, note: null, result: null, error: null, ...o,
});

test.each([
  ["queued", "В очереди на расшифровку"],
  ["running", "Распознавание 25%"],
] as const)("%s: «Отменить» снимает задачу и перечитывает", async (state, title) => {
  load({ has_transcript: false }, null);
  vi.mocked(api.cancelJob).mockResolvedValue({ ok: true });
  const onChanged = vi.fn();
  render(<RecordingCard id="r1" endpoint={ep} jobs={[job({ state })]} onChanged={onChanged} />);
  expect(await screen.findByText(title)).toBeInTheDocument();
  await new Promise((r) => setTimeout(r, 0));
  const loads = vi.mocked(api.getRecording).mock.calls.length;
  await userEvent.click(screen.getByRole("button", { name: "Отменить" }));
  await waitFor(() => expect(api.cancelJob).toHaveBeenCalledWith(ep, "j1"));
  expect(onChanged).toHaveBeenCalled();
  await waitFor(() => expect(vi.mocked(api.getRecording).mock.calls.length).toBe(loads + 1));
});

test("прерванный импорт (задачи нет): ошибка, а не вечное «Копирование…»", async () => {
  load({ has_transcript: false, source: "import", tracks: {} }, null);
  render(<RecordingCard id="r1" endpoint={ep} jobs={[]} />);
  expect(await screen.findByText("Импорт прерван")).toBeInTheDocument();
  expect(screen.queryByText("Копирование…")).toBeNull();
  expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
});

test("ошибка: «Открыть журнал» открывает <data_dir>/logs", async () => {
  load({ has_transcript: false }, null);
  vi.mocked(api.getDiagnostics).mockResolvedValue({ paths: { data_dir: "C:\\Users\\me\\meet" } });
  render(<RecordingCard id="r1" endpoint={ep} jobs={[job({ state: "failed", error: "CUDA out of memory" })]} />);
  expect(await screen.findByText("CUDA out of memory")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Открыть журнал" }));
  await waitFor(() => expect(shell.openFolder).toHaveBeenCalledWith("C:\\Users\\me\\meet\\logs"));
});

test("упавшая перерасшифровка готовой записи видна баннером с «Повторить»", async () => {
  load({ transcript_at: 1000 });
  vi.mocked(api.transcribe).mockResolvedValue({} as Job);
  render(<RecordingCard id="r1" endpoint={ep}
    jobs={[job({ state: "failed", error: "диск переполнен", finished_at: 2000 })]} />);
  expect(await screen.findByText("Привет всем")).toBeInTheDocument();
  const banner = screen.getByRole("status");
  expect(banner).toHaveTextContent("Перерасшифровка не удалась: диск переполнен");
  await userEvent.click(within(banner).getByRole("button", { name: "Повторить" }));
  expect(api.transcribe).toHaveBeenCalledWith(ep, "r1");
});

test("ошибка старее транскрипта — баннера нет", async () => {
  load({ transcript_at: 3000 });
  render(<RecordingCard id="r1" endpoint={ep}
    jobs={[job({ state: "failed", error: "старое", finished_at: 2000 })]} />);
  expect(await screen.findByText("Привет всем")).toBeInTheDocument();
  expect(screen.queryByText(/Перерасшифровка не удалась/)).toBeNull();
});

test("«Перерасшифровать» спрашивает подтверждение", async () => {
  load();
  vi.mocked(api.transcribe).mockResolvedValue({} as Job);
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Привет всем");
  await userEvent.click(screen.getByRole("button", { name: "Перерасшифровать" }));
  expect(api.transcribe).not.toHaveBeenCalled();
  expect(screen.getByText(
    "Расшифровка будет создана заново: ручные правки и имена, не сохранённые в базе голосов, будут потеряны. Продолжить?")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
  expect(api.transcribe).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Перерасшифровать" }));
  await userEvent.click(screen.getByRole("button", { name: "Перерасшифровать" }));
  await waitFor(() => expect(api.transcribe).toHaveBeenCalledWith(ep, "r1"));
});

test("названный спикер тоже кликабелен: «Кто это?» с текущим именем", async () => {
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Привет всем");
  const named = screen.getAllByRole("button", { name: /Демьян Петров/ });
  expect(named.length).toBe(2); // участник в шапке и подпись реплики
  await userEvent.click(named[1]!);
  expect(await screen.findByRole("dialog", { name: "Кто это?" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Кто это?" })).toHaveValue("Демьян Петров");
});

test("импорт без duration_s: длительность по концу последней реплики", async () => {
  load({ duration_s: null, source: "import" }, {
    version: 1, title: null,
    segments: [{ start: 0, end: 30, speaker: "Спикер 1", text: "а", uncertain: false },
               { start: 100, end: 125, speaker: "Спикер 1", text: "б", uncertain: false }],
  });
  const { container } = render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("б");
  expect(container.querySelector(".card__meta")).toHaveTextContent("2 мин");
});

test("готовая запись: вкладки «Расшифровка · Итоги · Вопросы», расшифровка по умолчанию", async () => {
  load();
  const onOpenSettings = vi.fn();
  render(<RecordingCard id="r1" endpoint={ep} onOpenSettings={onOpenSettings} />);
  await screen.findByText("Привет всем");
  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((t) => t.textContent)).toEqual(["Расшифровка", "Итоги", "Вопросы"]);
  expect(tabs[0]).toHaveAttribute("aria-selected", "true");
  await userEvent.click(tabs[1]!);
  expect(await screen.findByText("Итогов пока нет")).toBeVisible();
  expect(screen.getByRole("tab", { name: "Итоги" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("Привет всем")).not.toBeVisible();
  // Без провайдера — ссылка в настройки, раздел «Ассистент».
  await userEvent.click(await screen.findByRole("button", { name: "Открыть настройки" }));
  expect(onOpenSettings).toHaveBeenCalledWith("assistant");
  await userEvent.click(screen.getByRole("tab", { name: "Вопросы" }));
  expect(await screen.findByText("Вопросов пока не было")).toBeVisible();
  expect(api.getSummary).toHaveBeenCalledWith(ep, "r1");
  expect(api.getQa).toHaveBeenCalledWith(ep, "r1");
});

test("не расшифрованная запись — без вкладок", async () => {
  load({ has_transcript: false }, null);
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Запись не расшифрована");
  expect(screen.queryByRole("tablist")).toBeNull();
  expect(api.getSummary).not.toHaveBeenCalled();
});

test.each(["skipped_no_token", "skipped_no_access"])(
  "расшифровка без спикеров (%s) — подсказка и переход в настройки моделей", async (diarization) => {
    load({ diarization });
    const onOpenSettings = vi.fn();
    render(<RecordingCard id="r1" endpoint={ep} onOpenSettings={onOpenSettings} />);
    expect(await screen.findByText("Без разделения на спикеров — настройте Hugging Face")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Настроить" }));
    expect(onOpenSettings).toHaveBeenCalledWith("engine");
  });

test("со спикерами — подсказки про Hugging Face нет", async () => {
  load({ diarization: null });
  render(<RecordingCard id="r1" endpoint={ep} onOpenSettings={() => {}} />);
  expect(await screen.findByText("Привет всем")).toBeInTheDocument();
  expect(screen.queryByText(/настройте Hugging Face/)).toBeNull();
});


// --- база знаний ------------------------------------------------------------

const withMeetingsDir = (dir: string | null) =>
  vi.mocked(api.getSettings).mockResolvedValue({
    recording: { speaker_name: "Демьян Петров" }, export: { meetings_dir: dir },
  });

test("«В базу знаний» скрыта, пока папка для встреч не задана", async () => {
  load();
  withMeetingsDir(null);
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Привет всем");
  expect(screen.queryByRole("button", { name: "В базу знаний" })).toBeNull();
});

test("«В базу знаний» выгружает и показывает папку с кнопкой «Открыть папку»", async () => {
  load();
  withMeetingsDir("D:/kb/Встречи");
  const target = "D:/kb/Встречи/2026-09-30 - Планирование спринта";
  vi.mocked(api.kbExport).mockResolvedValue({ path: target, files: ["Транскрипт.md"], kept: [] });
  render(<RecordingCard id="r1" endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "В базу знаний" }));
  expect(api.kbExport).toHaveBeenCalledWith(ep, "r1");
  const done = await screen.findByRole("status", { name: "Выгрузка в базу знаний" });
  expect(done).toHaveTextContent(`Выгружено: ${target}`);
  await userEvent.click(within(done).getByRole("button", { name: "Открыть папку" }));
  expect(shell.openFolder).toHaveBeenCalledWith(target);
});

test("ошибка прошлой выгрузки (kb_export.error) видна в карточке", async () => {
  load({ kb_export: { path: null, at: null, error: "Папка для встреч не найдена: E:/kb" } });
  withMeetingsDir("E:/kb");
  render(<RecordingCard id="r1" endpoint={ep} />);
  expect(await screen.findByText(/Не удалось выгрузить в базу знаний: Папка для встреч не найдена/))
    .toBeInTheDocument();
});

test("файлы, изменённые вручную, перечислены после выгрузки", async () => {
  load();
  withMeetingsDir("D:/kb");
  vi.mocked(api.kbExport).mockResolvedValue({
    path: "D:/kb/2026-09-30 - Планирование спринта", files: ["Итоги.md"], kept: ["Транскрипт.md"],
  });
  render(<RecordingCard id="r1" endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "В базу знаний" }));
  const done = await screen.findByRole("status", { name: "Выгрузка в базу знаний" });
  expect(done).toHaveTextContent("Транскрипт.md изменён вручную — не перезаписан");
});
