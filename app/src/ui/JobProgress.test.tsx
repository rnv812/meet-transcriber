import { render, screen } from "@testing-library/react";
import type { Job } from "../lib/types";
import { JobProgress } from "./JobProgress";

const job = (o: Partial<Job> = {}): Job => ({
  id: "j1", kind: "transcribe", folder: "C:/rec/r1", state: "running", stage: "asr", label: "распознавание",
  done: 0.5, total: 1, note: null, result: null, error: null, ...o,
});

test("overall scale: stage number, percent and time left from the estimate", () => {
  const started = Date.now() / 1000 - 5;
  render(<JobProgress job={job({ step: 2, steps: 5, fraction: 0.25, estimate_s: 600, started_at: started })} />);
  expect(screen.getByText("Этап 2 из 5 · Распознавание")).toBeInTheDocument();
  // 600 с по оценке, сделана четверть: осталось ~450 с.
  expect(screen.getByText("25 % · осталось ~8 мин")).toBeInTheDocument();
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
});

test("a step without its own scale keeps the overall value and shows it is working", () => {
  render(<JobProgress job={job({ stage: "diarize", label: "диаризация", done: null, total: null, step: 4, steps: 6,
    fraction: 0.6 })} />);
  expect(screen.getByText("Этап 4 из 6 · Разделение на спикеров")).toBeInTheDocument();
  const bar = screen.getByRole("progressbar");
  expect(bar).toHaveAttribute("aria-valuenow", "60");
  expect(bar).toHaveClass("progressbar__track--working");
});

test("old resident: per-stage done/total, unknown total is indeterminate (not full)", () => {
  const { rerender } = render(<JobProgress job={job({ done: 1, total: 4 })} />);
  expect(screen.getByText("Распознавание")).toBeInTheDocument();
  expect(screen.getByText("25 %")).toBeInTheDocument();
  rerender(<JobProgress job={job({ stage: "align", label: "выравнивание", done: null, total: null })} />);
  const bar = screen.getByRole("progressbar");
  expect(bar).toHaveClass("progressbar__track--indeterminate");
  expect(bar).not.toHaveAttribute("aria-valuenow");
});

test("custom label and detail (model download)", () => {
  render(<JobProgress job={job({ kind: "download-model", stage: "model", done: 512, total: 1024 })}
    label="Скачивается" detail="0,5 из 1 ГБ" />);
  expect(screen.getByText("Скачивается")).toBeInTheDocument();
  expect(screen.getByText("0,5 из 1 ГБ")).toBeInTheDocument();
});

// --- 0.3.1: задачи модели -------------------------------------------------------

test("model job: window, sub-step, elapsed clock and a confident time left", () => {
  const started = Date.now() / 1000 - 72;
  render(<JobProgress job={job({ kind: "analyze", stage: "analyze", label: "анализ встречи", note: "окно 2 из 4",
    phase: "generating", fraction: 0.37, cap: 0.6, eta_s: 41, started_at: started, done: 0.3, total: 1 })} />);
  expect(screen.getByText("Анализ встречи · окно 2 из 4 · модель пишет ответ")).toBeInTheDocument();
  expect(screen.getByText("37 % · 1:12 · осталось ~40 с")).toBeInTheDocument();
});

test("model job longer than usual: says so, no time left, keeps the clock", () => {
  const started = Date.now() / 1000 - 200;
  render(<JobProgress job={job({ kind: "summary", stage: "llm", label: "итоги встречи", phase: "request", slow: true,
    fraction: 0.85, eta_s: null, started_at: started, done: 0.95, total: 1 })} />);
  expect(screen.getByText("Итоги встречи · дольше обычного…")).toBeInTheDocument();
  expect(screen.getByText("85 % · 3:20")).toBeInTheDocument();
});

test("model job without a confident estimate shows only percent and the clock", () => {
  const started = Date.now() / 1000 - 9;
  render(<JobProgress job={job({ kind: "improve", stage: "improve", label: "улучшение расшифровки", phase: "request",
    fraction: 0.05, started_at: started, done: 0.06, total: 1 })} />);
  expect(screen.getByText("5 % · 0:09")).toBeInTheDocument();
});

test("analysis and summary show the job's progress instead of a pulse when the resident reports it", async () => {
  const { AnalysisStatus } = await import("../features/card/analysis");
  const { ThinkingStage } = await import("../features/card/assistant");
  const running = job({ kind: "analyze", stage: "analyze", label: "анализ встречи", note: "окно 1 из 2", phase: "request",
    fraction: 0.2, done: 0.1, total: 1, started_at: Date.now() / 1000 - 3 });
  const { unmount } = render(<AnalysisStatus state={{ state: "running", job: running }} busy={false} />);
  expect(screen.getByRole("progressbar", { name: "Анализ встречи · окно 1 из 2 · модель думает" })).toBeInTheDocument();
  unmount();
  // Старый резидент (без подшагов) — прежний тихий «Анализ…».
  const { unmount: u2 } = render(<AnalysisStatus state={{ state: "running", job: job({ kind: "analyze", stage: "analyze" }) }} busy={false} />);
  expect(screen.getByText("Анализ…")).toBeInTheDocument();
  u2();
  render(<ThinkingStage job={job({ kind: "summary", stage: "llm", label: "итоги встречи", phase: "generating",
    fraction: 0.5, done: 0.5, total: 1 })} />);
  expect(screen.getByText("Итоги встречи · модель пишет ответ")).toBeInTheDocument();
});

test("list badge shows the same value as the bar", async () => {
  const { badgeOf } = await import("../features/recordings/RecordingItem");
  const j = job({ stage: "diarize", fraction: 0.4, step: 4, steps: 6 });
  const st = { kind: "running" as const, stage: "diarize", label: "Разделение на спикеров", job: j };
  expect(badgeOf(st)?.text).toBe("40% · Разделение на спикеров");
  expect(badgeOf(st, 0.437)?.text).toBe("43% · Разделение на спикеров");
});
