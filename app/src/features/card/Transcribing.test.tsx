import { render, screen, within } from "@testing-library/react";

import type { Job } from "../../lib/types";
import { Transcribing } from "./Transcribing";

/** Страница «Расшифровывается» в карточке записи. */

const job = (o: Partial<Job> = {}): Job => ({
  id: "j1", kind: "transcribe", folder: "C:/rec/r1", state: "running", stage: "asr", label: null,
  done: 0.64, total: 1, note: null, result: null, error: null, ...o,
});
const stages = () => within(screen.getByRole("list", { name: "Этапы расшифровки" })).getAllByRole("listitem");

test("идёт распознавание: бейдж, этапы (запись готова · распознавание % · спикеры · анализ)", () => {
  render(<Transcribing job={job()} durationS={767} tracks={2} analysis="Claude Code, после расшифровки" />);
  expect(screen.getByText("Расшифровывается")).toBeInTheDocument();
  const [rec, asr, spk, an] = stages();
  expect(rec).toHaveTextContent("Запись");
  expect(rec).toHaveTextContent("готово");
  expect(rec).toHaveTextContent("12:47 · 2 дорожки");
  expect(asr).toHaveTextContent("Распознавание");
  expect(asr).toHaveTextContent("64 %");
  expect(within(asr!).getByRole("progressbar", { name: "Распознавание" })).toHaveAttribute("aria-valuenow", "64");
  expect(spk).toHaveTextContent("подписи появятся следом");
  expect(an).toHaveTextContent("Claude Code, после расшифровки");
  expect(screen.getByText("Окно можно закрыть: Meet доделает расшифровку в фоне и покажет уведомление."))
    .toBeInTheDocument();
  // Текст ещё не готов — строки скелетом.
  expect(screen.getByRole("status", { name: "Текст появится после распознавания" })).toHaveAttribute("aria-busy", "true");
});

test("разделение на спикеров: распознавание готово, у спикеров этап без своей шкалы — бегущая полоса", () => {
  render(<Transcribing job={job({ stage: "diarize", done: null, total: null, step: 4, steps: 5, fraction: 0.7 })}
    durationS={60} tracks={1} analysis="после расшифровки" />);
  const [, asr, spk] = stages();
  expect(asr).toHaveTextContent("готово");
  const bar = within(spk!).getByRole("progressbar", { name: "Разделение на спикеров" });
  expect(bar).not.toHaveAttribute("aria-valuenow");
  // Общий ход и этап — строкой под карточкой.
  expect(screen.getByText(/Этап 4 из 5 · 70 %/)).toBeInTheDocument();
});

test("распознавание одной из дорожек — подпись этапа под шкалой", () => {
  render(<Transcribing job={job({ note: "sys", done: 1, total: 4 })} durationS={60} tracks={2} analysis="" />);
  const [, asr] = stages();
  expect(asr).toHaveTextContent("Распознавание собеседников");
  expect(asr).toHaveTextContent("25 %");
});

test("в очереди: свой бейдж, распознавание ждёт", () => {
  render(<Transcribing job={job({ state: "queued", stage: null, done: null, total: null })} queued durationS={60}
    tracks={1} analysis="" />);
  expect(screen.getByText("В очереди на расшифровку")).toBeInTheDocument();
  expect(screen.queryByText("Расшифровывается")).toBeNull();
  expect(screen.queryByRole("progressbar")).toBeNull();
});

test("обработка без задачи (обрезка ожидания) — этап записи идёт", () => {
  render(<Transcribing job={null} stage="trim" label="Обработка" durationS={null} tracks={2} analysis="" />);
  const [rec] = stages();
  expect(within(rec!).getByRole("progressbar", { name: "Обработка" })).toBeInTheDocument();
});

test("предупреждение задачи и действия — на странице", () => {
  const warning = "Распознаётся на процессоре: видеокарта NVIDIA не найдена";
  render(<Transcribing job={job({ warning })} durationS={60} tracks={1} analysis=""
    actions={<button type="button">Отменить расшифровку…</button>} />);
  expect(screen.getByRole("note")).toHaveTextContent(warning);
  expect(screen.getByRole("button", { name: "Отменить расшифровку…" })).toBeInTheDocument();
});
