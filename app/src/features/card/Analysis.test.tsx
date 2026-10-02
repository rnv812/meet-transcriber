import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingCard } from "./RecordingCard";
import { AnalysisStatus, analysisJobOf, reanalyzeBlocked } from "./analysis";
import * as api from "../../lib/api";
import type { AnalysisState, Job, Recording, Transcript } from "../../lib/types";

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
  await userEvent.click(screen.getByRole("button", { name: "Переанализировать" }));
  rerender(<AnalysisStatus state={{ state: "failed", error: "таймаут вызова модели" }} busy={false} onRun={onRun} />);
  expect(screen.getByRole("status")).toHaveTextContent("Анализ не удался: таймаут вызова модели");
  await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(onRun).toHaveBeenCalledTimes(2);
  rerender(<AnalysisStatus state={{ state: "ready" }} busy={false} onRun={onRun} />);
  expect(screen.queryByRole("status")).toBeNull();
  // без модели — без кнопки
  rerender(<AnalysisStatus state={{ state: "failed", error: "нет" }} busy={false} />);
  expect(screen.queryByRole("button")).toBeNull();
});

test("reanalyzeBlocked и задача анализа записи", () => {
  const running: AnalysisState = { state: "running" };
  expect(reanalyzeBlocked(running, false)).toBe("Анализ уже идёт");
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
  const item = within(menu).getByRole("menuitem", { name: "Переанализировать" });
  expect(item).toBeDisabled();
  expect(item).toHaveAttribute("title", "Анализ уже идёт");
});

test("карточка: без модели «Переанализировать» недоступно", async () => {
  provider(null);
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Начнём с беты");
  const menu = await openMore();
  await waitFor(() => expect(within(menu).getByRole("menuitem", { name: "Переанализировать" })).toBeDisabled());
});

test("карточка: устаревший анализ — «Переанализировать» ставит задачу", async () => {
  vi.mocked(api.getAnalysis).mockResolvedValue({ state: "stale" });
  vi.mocked(api.runAnalysis).mockResolvedValue(job("queued"));
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  expect(await screen.findByText(/Анализ устарел/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Переанализировать" }));
  expect(api.runAnalysis).toHaveBeenCalledWith(ep, "r1");
});

test("карточка: анализ не удался — тихая строка и «Повторить» из меню", async () => {
  vi.mocked(api.getAnalysis).mockResolvedValue({ state: "failed", error: "rate_limit" });
  vi.mocked(api.runAnalysis).mockResolvedValue(job("queued"));
  load();
  render(<RecordingCard id="r1" endpoint={ep} />);
  expect(await screen.findByText("Анализ не удался: rate_limit")).toBeInTheDocument();
  await userEvent.click(within(await openMore()).getByRole("menuitem", { name: "Переанализировать" }));
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
  expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Запуск беты");
  expect(screen.getByLabelText(/Название предложено ИИ/)).toBeInTheDocument();
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

  vi.mocked(api.suggestTitle).mockRejectedValueOnce(new api.ApiError(409, "Подключите Claude Code или Codex в настройках"));
  await userEvent.click(within(await openMore()).getByRole("menuitem", { name: "Предложить название" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось предложить название");
});

test("бейдж «ИИ» у названия от модели; нажатие — переименовать, своё название — без бейджа", async () => {
  vi.mocked(api.patchRecording).mockResolvedValue({ ...base, title: "Моё", title_source: "user" });
  load({ title: "Запуск беты", title_source: "ai" });
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Начнём с беты");
  await userEvent.click(screen.getByLabelText(/Название предложено ИИ/));
  const input = screen.getByRole("textbox", { name: "Название записи" });
  await userEvent.clear(input);
  await userEvent.type(input, "Моё{Enter}");
  expect(api.patchRecording).toHaveBeenCalledWith(ep, "r1", { title: "Моё" });
  await waitFor(() => expect(screen.queryByLabelText(/Название предложено ИИ/)).toBeNull());
});
