import { render, screen } from "@testing-library/react";
import { StepHardware } from "./StepHardware";
import type { EngineStatus } from "../../lib/shell";

const ENGINE: EngineStatus = {
  installed: false,
  version: "0.3.0",
  env_dir: "/Users/someone/Library/Application Support/meet/engine/0.3.0",
  profile: null,
  gpu: null,
  free_gb: 120,
  needs_gb: 3,
};

test("Apple Silicon: без видеокарты NVIDIA, движок — для Apple Silicon", () => {
  render(<StepHardware engine={ENGINE} profile="mac" onNext={() => {}} />);
  expect(screen.getByText("Компьютер")).toBeInTheDocument();
  expect(screen.getByText(/Apple Silicon — распознавание на процессоре/)).toBeInTheDocument();
  expect(screen.getByText("для Apple Silicon (экспериментально)")).toBeInTheDocument();
  expect(screen.queryByText(/NVIDIA/)).not.toBeInTheDocument();
  expect(screen.getByText("60 мин встречи ≈ 29 мин обработки")).toBeInTheDocument();
});

test("Windows без карты — как раньше", () => {
  render(<StepHardware engine={ENGINE} profile="cpu" onNext={() => {}} />);
  expect(screen.getByText("Видеокарта")).toBeInTheDocument();
  expect(screen.getByText(/Видеокарта NVIDIA не найдена/)).toBeInTheDocument();
  expect(screen.getByText("для процессора")).toBeInTheDocument();
});
