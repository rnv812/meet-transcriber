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
