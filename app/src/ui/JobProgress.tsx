/**
 * Ход задачи резидента полоской: этап («Этап 2 из 5 · Распознавание»),
 * проценты и оценка оставшегося времени. Общая доля (`fraction`, новые
 * резиденты) — одна шкала на всю задачу; без неё — шкала этапа, а этап без
 * своего хода — бегущий блик.
 */

import { useEffect, useState } from "react";
import { etaSeconds, etaText, jobElapsed, jobFraction, stageText } from "../lib/progress";
import { stageLabel } from "../lib/status";
import type { Job } from "../lib/types";
import { ProgressBar } from "./ProgressBar";

/** Как часто пересчитывать «осталось ~N мин» между событиями резидента. */
export const ETA_TICK_MS = 5000;

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
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), ETA_TICK_MS);
    return () => window.clearInterval(t);
  }, []);
  const fraction = jobFraction(job);
  const overall = typeof job.fraction === "number";
  // Общая шкала монотонна на всю задачу; шкала этапа — в пределах этапа.
  const stageKey = overall ? job.id : `${job.id}:${job.stage ?? ""}`;
  const text = label ?? stageText(stageLabel(job), job.step, job.steps);
  const pct = fraction !== null ? `${Math.floor(fraction * 100)} %` : null;
  const eta = etaText(etaSeconds(fraction, jobElapsed(job, now), job.estimate_s));
  const right = detail !== undefined ? detail : [pct, eta].filter(Boolean).join(" · ") || null;
  const working = overall && job.state === "running" && (job.done === null || job.done === undefined);
  return (
    <ProgressBar value={fraction} stageKey={stageKey} label={text} detail={right} size={size}
      ariaLabel={ariaLabel ?? text} className={className} working={working} />
  );
}
