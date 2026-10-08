import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingCard } from "./RecordingCard";
import { AnalysisStatus, analysisJobOf, analysisNotes, missingNote, missingParts, reanalyzeBlocked } from "./analysis";
import * as api from "../../lib/api";
import type { Analysis, AnalysisState, Job, Recording, Transcript } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(),
  getSettings: vi.fn(async () => ({})),
  patchRecording: vi.fn(),
  getAssistant: vi.fn(),
  getSummary: vi.fn(),
  getQa: vi.fn(async () => ({ items: [] })),
  getAnalysis: vi.fn(),
  runAnalysis: vi.fn(),
  suggestTitle: vi.fn(),
  getRediarized: vi.fn(async () => { throw new Error("нового разделения нет"); }),
}));
vi.mock("../../lib/shell", () => ({
  inTauri: () => true,
  saveText: vi.fn(async () => "x"),
  openFolder: vi.fn(async () => {}),
  agentKillRecording: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
const transcript: Transcript = {
  version: 1, title: null,
  segments: [{ start: 0, end: 5, speaker: "Ольга", text: "Начнём с беты", uncertain: false }],
};
const base: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-10-01T10:00:00", duration_s: 1800,
  tracks: { sys: "s.wav" }, has_transcript: true, has_voices: false, title: "Встреча", source: "record",
  title_source: "user",
};
const job = (state: Job["state"]): Job => ({
  id: "a1", kind: "analyze", folder: "C:\\rec\\r1", state, stage: null, label: null, done: null, total: null,
  note: null, result: null, error: null,
});

function load(extra: Partial<Recording> = {}) {
  vi.mocked(api.getRecording).mockResolvedValue({ ...base, ...extra, transcript });
}
function provider(name: string | null) {
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: name, setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
}
async function openMore() {
  await userEvent.click(screen.getByRole("button", { name: "Ещё действия" }));
  return screen.getByRole("menu", { name: "Ещё действия с записью" });
}

beforeEach(() => {
  vi.clearAllMocks();
  provider("claude-code");
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.getAnalysis).mockResolvedValue({ state: "none" });
  HTMLMediaElement.prototype.load = vi.fn();
});

// --- строки состояния ----------------------------------------------------------------

test("AnalysisStatus: идёт, устарел, не удался", async () => {
  const onRun = vi.fn();
  const { rerender } = render(<AnalysisStatus state={{ state: "running" }} busy={false} onRun={onRun} />);
  expect(screen.getByRole("status")).toHaveTextContent("Анализ…");
  rerender(<AnalysisStatus state={{ state: "queued" }} busy={false} onRun={onRun} />);
  expect(screen.getByRole("status")).toHaveTextContent("Анализ в очереди…");
  rerender(<AnalysisStatus state={{ state: "stale" }} busy={false} onRun={onRun} />);
  expect(screen.getByRole("status")).toHaveTextContent("Анализ устарел");
  await userEvent.click(screen.getByRole("button", { name: "Переанализировать…" }));
  await userEvent.click(within(screen.getByRole("alertdialog", { name: "Разметить встречу заново?" }))
    .getByRole("button", { name: "Переанализировать" }));
  rerender(<AnalysisStatus state={{ state: "failed", error: "таймаут вызова модели" }} busy={false} onRun={onRun} />);
  expect(screen.getByRole("status")).toHaveTextContent("Анализ не удался");
  expect(screen.getByText("Анализ не удался")).toHaveAccessibleDescription("таймаут вызова модели");
  await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(onRun).toHaveBeenCalledTimes(2);
  rerender(<AnalysisStatus state={{ state: "ready" }} busy={false} onRun={onRun} />);
  expect(screen.queryByRole("status")).toBeNull();
  // без модели — без кнопки
  rerender(<AnalysisStatus state={{ state: "failed", error: "нет" }} busy={false} />);
  expect(screen.queryByRole("button")).toBeNull();
});

const doc = (extra: Partial<Analysis> = {}): Analysis => ({
  version: 1, model: "openai-compatible:qwen3:8b", llm: { provider: "openai-compatible", model: "qwen3:8b" },
  created_at: 1759300000, fingerprint: "f", segments: 1,
  features: ["types", "importance", "chapters", "insights", "category", "title"],
  phrase_types: {}, importance: { "0": 0.9 }, chapters: [{ start_i: 0, end_i: 0, title: "Бета", short: "Бета" }],
  insights: [], category: null, title: null, ...extra,
});

test("анализ без глав — честная строка, а не молча пустая полоса", () => {
  const warnings = ["часть 1: chapters пустой: нужны главы по смене темы разговора"];
  render(<AnalysisStatus state={{ state: "ready", analysis: doc({ chapters: [], missing: ["chapters"], warnings }) }}
    busy={false} />);
  // Локальная модель — совет и про окно контекста (частая причина).
  const note = screen.getByText("Модель вернула анализ без глав — увеличьте контекст модели (16K+) или попробуйте другую модель");
  expect(note).toHaveAccessibleDescription(warnings[0]);
  // Какая модель разметила — по-прежнему видно.
  expect(screen.getByText(/Анализ: Локальная модель \(qwen3:8b\)/)).toBeInTheDocument();
});

test("строка о недостающих частях: несколько частей, устаревший анализ", () => {
  expect(missingNote(["chapters", "importance"])).toBe(
    "Модель вернула анализ без глав и оценок важности — попробуйте другую модель");
  expect(missingNote(["types", "chapters", "insights"])).toBe(
    "Модель вернула анализ без глав, типов реплик и наблюдений — попробуйте другую модель");
  expect(missingNote([])).toBeNull();
  render(<AnalysisStatus state={{ state: "stale", analysis: doc({ chapters: [], missing: ["chapters"] }) }}
    busy={false} />);
  expect(screen.getByText(/Модель вернула анализ без глав/)).toBeInTheDocument();
});

test("анализ прежних версий без `missing`: пустые главы длинной встречи и пустая важность", () => {
  expect(missingParts(doc({ chapters: [] }), 1800)).toEqual(["chapters"]);
  // Короткой встрече главы не положены.
  expect(missingParts(doc({ chapters: [] }), 120)).toEqual([]);
  expect(missingParts(doc({ importance: {}, segments: 40 }), 1800)).toEqual(["importance"]);
  // Главы выключены в настройках — их и не просили.
  expect(missingParts(doc({ chapters: [], features: ["importance"] }), 1800)).toEqual([]);
  // Резидент сам сказал, чего нет, — верим ему.
  expect(missingParts(doc({ chapters: [], missing: [] }), 1800)).toEqual([]);
});

test("карточка: анализ без глав и важности — строка в карточке", async () => {
  vi.mocked(api.getAnalysis).mockResolvedValue({
    state: "ready", analysis: doc({ chapters: [], importance: {}, missing: ["importance", "chapters"] }),
  });
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  expect(await screen.findByText(
    "Модель вернула анализ без глав и оценок важности — увеличьте контекст модели (16K+) или попробуйте другую модель"))
    .toBeInTheDocument();
});

test("reanalyzeBlocked и задача анализа записи", () => {
  const running: AnalysisState = { state: "running" };
  expect(reanalyzeBlocked(running, false)).toBe("Анализ уже идёт");
  expect(reanalyzeBlocked({ state: "queued" }, false)).toBe("Анализ уже в очереди");
  expect(reanalyzeBlocked({ state: "ready" }, true)).toMatch(/Подключите/);
  expect(reanalyzeBlocked({ state: "stale" }, false)).toBeNull();
  expect(analysisJobOf("C:/rec/r1", [job("running")])?.id).toBe("a1");
  expect(analysisJobOf("C:/rec/r1", [job("done")])).toBeNull();
  expect(analysisJobOf("C:/rec/r2", [job("queued")])).toBeNull();
});

// --- карточка ----------------------------------------------------------------------

test("карточка: идёт анализ — «Анализ…», «Переанализировать» недоступно с подсказкой", async () => {
  load();
  render(<RecordingCard id="r1" endpoint={ep} jobs={[job("running")]} />);
  await screen.findByText("Начнём с беты");
  expect(screen.getByText("Анализ…")).toBeInTheDocument();
  const menu = await openMore();
  const item = within(menu).getByRole("menuitem", { name: "Переанализировать…" });
  expect(item).toBeDisabled();
  expect(item).toHaveAccessibleDescription("Анализ уже идёт");
});

test("карточка: без модели «Переанализировать» недоступно", async () => {
  provider(null);
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Начнём с беты");
  const menu = await openMore();
  // анализа ещё не было — «Анализировать»
  await waitFor(() => expect(within(menu).getByRole("menuitem", { name: "Анализировать" })).toBeDisabled());
});

test("карточка: устаревший анализ — «Переанализировать» ставит задачу", async () => {
  vi.mocked(api.getAnalysis).mockResolvedValue({ state: "stale" });
  vi.mocked(api.runAnalysis).mockResolvedValue(job("queued"));
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  expect(await screen.findByText(/Анализ устарел/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Переанализировать…" }));
  expect(api.runAnalysis).not.toHaveBeenCalled();
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Переанализировать" }));
  expect(api.runAnalysis).toHaveBeenCalledWith(ep, "r1");
});

test("карточка: анализ не удался — тихая строка и «Повторить» из меню", async () => {
  vi.mocked(api.getAnalysis).mockResolvedValue({ state: "failed", error: "rate_limit" });
  vi.mocked(api.runAnalysis).mockResolvedValue(job("queued"));
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  expect(await screen.findByText("Анализ не удался")).toHaveAccessibleDescription("rate_limit");
  await userEvent.click(within(await openMore()).getByRole("menuitem", { name: "Переанализировать…" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Переанализировать" }));
  expect(api.runAnalysis).toHaveBeenCalledTimes(1);
});

test("«Предложить название»: окно «Применить / Отмена», применённое — от ИИ", async () => {
  vi.mocked(api.suggestTitle).mockResolvedValue({ title: "Запуск беты", from: "analysis" });
  vi.mocked(api.patchRecording).mockResolvedValue({ ...base, title: "Запуск беты", title_source: "ai" });
  const onChanged = vi.fn();
  load();
  render(<RecordingCard id="r1" endpoint={ep} onChanged={onChanged} />);
  await screen.findByText("Начнём с беты");
  await userEvent.click(within(await openMore()).getByRole("menuitem", { name: "Предложить название" }));
  const box = await screen.findByRole("dialog", { name: "Предложенное название" });
  expect(await within(box).findByText("Запуск беты")).toBeInTheDocument();
  expect(within(box).getByText("Название из анализа встречи")).toBeInTheDocument();
  await userEvent.click(within(box).getByRole("button", { name: "Применить" }));
  expect(api.patchRecording).toHaveBeenCalledWith(ep, "r1", { title: "Запуск беты", title_source: "ai" });
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Предложенное название" })).toBeNull());
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Запуск беты");
  expect(screen.getByTitle(/Название предложено ИИ/)).toBeInTheDocument();
  expect(onChanged).toHaveBeenCalled();
});

test("«Предложить название»: «Отмена» ничего не меняет; ошибка — текстом", async () => {
  vi.mocked(api.suggestTitle).mockResolvedValueOnce({ title: "Запуск беты", from: "model" });
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Начнём с беты");
  await userEvent.click(within(await openMore()).getByRole("menuitem", { name: "Предложить название" }));
  const box = await screen.findByRole("dialog", { name: "Предложенное название" });
  await within(box).findByText("Название по началу встречи");
  await userEvent.click(within(box).getByRole("button", { name: "Отмена" }));
  expect(screen.queryByRole("dialog", { name: "Предложенное название" })).toBeNull();
  expect(api.patchRecording).not.toHaveBeenCalled();

  vi.mocked(api.suggestTitle).mockRejectedValueOnce(new api.ApiError(409, "Подключите Claude Code, Codex или OpenCode в настройках"));
  await userEvent.click(within(await openMore()).getByRole("menuitem", { name: "Предложить название" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось предложить название");
});

test("бейдж «ИИ» у названия от модели; нажатие — переименовать, своё название — без бейджа", async () => {
  vi.mocked(api.patchRecording).mockResolvedValue({ ...base, title: "Моё", title_source: "user" });
  load({ title: "Запуск беты", title_source: "ai" });
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Начнём с беты");
  await userEvent.click(screen.getByTitle(/Название предложено ИИ/));
  const input = screen.getByRole("textbox", { name: "Название записи" });
  await userEvent.clear(input);
  await userEvent.type(input, "Моё{Enter}");
  expect(api.patchRecording).toHaveBeenCalledWith(ep, "r1", { title: "Моё" });
  await waitFor(() => expect(screen.queryByTitle(/Название предложено ИИ/)).toBeNull());
});


const cloud = (extra: Partial<Analysis> = {}) =>
  doc({ model: "claude-code:sonnet", llm: { provider: "claude-code", model: "sonnet" }, ...extra });

test("облачной модели — только «попробуйте другую модель»", () => {
  expect(analysisNotes(cloud({ chapters: [], missing: ["chapters"] }))).toEqual([
    "Модель вернула анализ без глав — попробуйте другую модель"]);
});

test("не разобранные куски встречи — с причиной; части только для части встречи; обрезанный промпт", () => {
  const notes = analysisNotes(doc({
    unparsed: { parts: 2, of: 5, reason: "текст не помещается в контекст модели: maximum context length is 4096" },
    partial: { chapters: "1/3", importance: "2/3" },
    context_cut: { seen: 4096, need: 18000 },
  }));
  expect(notes).toEqual([
    "Часть встречи не разобрана (2 из 5 кусков): текст не помещается в контекст модели: maximum context length is 4096",
    "Главы — только для части встречи (1 из 3 кусков)",
    "Оценки важности — только для части встречи (2 из 3 кусков)",
    "Модель видела только часть текста (~4096 из ~18000 токенов) — увеличьте контекст модели до 16K+",
  ]);
  expect(analysisNotes(cloud())).toEqual([]);
  // Ollama: окно ставит Meet — совет другой.
  expect(analysisNotes(doc({ context_cut: { seen: 32000, need: 90000, ollama: true } }))).toEqual([
    "Модель видела только часть текста (~32000 из ~90000 токенов) — окно этой модели меньше нужного, "
      + "возьмите модель с бо́льшим окном контекста"]);  // Модель умеет больше, а Meet просит у Ollama не больше 32K — так и сказано.
  expect(analysisNotes(doc({ context_cut: { seen: 32000, need: 90000, ollama: true, capped: true } }))).toEqual([
    "Модель видела только часть текста (~32000 из ~90000 токенов) — встреча длиннее окна, которое Meet "
      + "запрашивает у Ollama (32K)"]);
});

test("карточка: часть встречи не разобрана — строкой, причина целиком в подсказке", async () => {
  const reason = "таймаут вызова модели " + "очень длинная причина ".repeat(10);
  render(<AnalysisStatus state={{ state: "ready", analysis: cloud({ unparsed: { parts: 1, of: 3, reason } }) }}
    busy={false} />);
  const note = screen.getByText(/^Часть встречи не разобрана \(1 из 3 кусков\): таймаут/);
  expect(note.textContent!.length).toBeLessThan(200);
  expect(note).toHaveAccessibleDescription(expect.stringContaining(reason.trim()));
});
