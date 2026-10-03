/**
 * Ход задачи резидента полоской: этап («Этап 2 из 5 · Распознавание»),
 * проценты и оценка оставшегося времени. Общая доля (`fraction`, новые
 * резиденты) — одна шкала на всю задачу; без неё — шкала этапа, а этап без
 * своего хода — бегущий блик.
 *
 * Задача модели (анализ, итоги, улучшение) — «Анализ встречи · окно
 * 2 из 4 · модель пишет ответ», справа проценты, прошедшее время («1:12») и,
 * когда оценка уверенная, «осталось ~40 с»; дольше обычного — так и сказано.
 *
 * Между событиями полоска продлевается по темпу (ProgressBar `extrapolate`), а
 * проценты справа берутся из показанного значения — те же, что у полоски.
 */

import { useEffect, useState } from "react";
import { clockText, etaText, isModelProgress, jobElapsed, jobEta, jobFraction, PHASES, stageText } from "../lib/progress";
import { stageLabel } from "../lib/status";
import type { Job } from "../lib/types";
import { ProgressBar } from "./ProgressBar";

/** Как часто пересчитывать «осталось ~N мин» между событиями резидента. */
export const ETA_TICK_MS = 5000;
/** Задача модели показывает часы («1:12») — пересчёт раз в секунду. */
export const CLOCK_TICK_MS = 1000;

/** Подпись хода задачи модели: что делается, какая часть и какой подшаг. */
export function modelProgressText(job: Job): string {
  const phase = job.slow ? "дольше обычного…" : job.phase ? PHASES[job.phase] ?? null : null;
  return [stageLabel(job), job.note, phase].filter(Boolean).join(" · ");
}

/** Ключ шкалы: общая доля монотонна на всю задачу, шкала этапа — в пределах этапа. */
export const jobStageKey = (job: Job) => (typeof job.fraction === "number" ? job.id : `${job.id}:${job.stage ?? ""}`);

export function JobProgress({ job, label, detail, size = "md", ariaLabel, className }: {
  job: Job;
  /** Подпись вместо названия этапа (например, «Скачивается»). */
  label?: string;
  /** Текст справа вместо процентов и оценки; null — ничего. */
  detail?: string | null;
  size?: "sm" | "md";
  ariaLabel?: string;
  className?: string;
}) {
  const model = isModelProgress(job);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), model ? CLOCK_TICK_MS : ETA_TICK_MS);
    return () => window.clearInterval(t);
  }, [model]);
  const fraction = jobFraction(job);
  const overall = typeof job.fraction === "number";
  const running = job.state === "running";
  const text = label ?? (model ? modelProgressText(job) : stageText(stageLabel(job), job.step, job.steps));
  const elapsed = jobElapsed(job, now);
  const eta = etaText(jobEta(job, fraction, elapsed));
  const clock = model && running && elapsed !== null ? clockText(elapsed) : null;
  const right = detail !== undefined ? detail
    : (shown: number | null) => [shown !== null ? `${Math.floor(shown * 100)} %` : null, clock, eta]
      .filter(Boolean).join(" · ") || null;
  const working = overall && running && (job.done === null || job.done === undefined);
  return (
    <ProgressBar value={fraction} stageKey={jobStageKey(job)} label={text} detail={right} size={size}
      ariaLabel={ariaLabel ?? text} className={className} working={working}
      extrapolate={running && fraction !== null} cap={job.cap ?? null} />
  );
}
