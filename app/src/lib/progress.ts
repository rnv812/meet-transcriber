/**
 * Ход долгих задач для полоски прогресса: доля, подпись этапа и оценка
 * оставшегося времени.
 *
 * Резидент присылает по задаче `stage`/`label`, `done`/`total` внутри этапа и
 * (новые версии) `step`/`steps` — номер этапа и их число, `fraction` — общую
 * долю 0…1 с весами этапов, `estimate_s` — ожидаемую длительность всей работы
 * по `SPEED_FACTOR`. Старый резидент без них — доля из `done/total`, а без
 * `total` — «неизвестно» (бегущая полоска, а не полная шкала).
 */

import type { Job } from "./types";

/** Доля 0…1 или null — неизвестно. */
export function jobFraction(job: Pick<Job, "done" | "total"> & { fraction?: number | null }): number | null {
  if (typeof job.fraction === "number" && Number.isFinite(job.fraction)) return clamp01(job.fraction);
  if (job.total && job.total > 0 && typeof job.done === "number") return clamp01(job.done / job.total);
  return null;
}

export const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

/** «Этап 2 из 4 · Выравнивание»; без номера этапа — просто название. */
export function stageText(label: string, step?: number | null, steps?: number | null): string {
  const name = label ? label[0]!.toUpperCase() + label.slice(1) : "";
  if (step && steps && steps > 1) return name ? `Этап ${step} из ${steps} · ${name}` : `Этап ${step} из ${steps}`;
  return name;
}

/**
 * Сколько осталось, секунд, или null — оценивать рано.
 *
 * В начале опираемся на ожидание (`estimateS` — по SPEED_FACTOR и
 * длительности записи), дальше всё больше на замер: прошло `elapsedS` за долю
 * `fraction` — остаток в той же пропорции. К трети работы замер весит целиком.
 */
export function etaSeconds(fraction: number | null, elapsedS: number | null, estimateS?: number | null): number | null {
  if (fraction === null || fraction >= 1) return null;
  const byEstimate = estimateS && estimateS > 0 ? estimateS * (1 - fraction) : null;
  const byPace = elapsedS !== null && elapsedS >= 10 && fraction >= 0.03 ? (elapsedS * (1 - fraction)) / fraction : null;
  if (byPace === null) return byEstimate;
  if (byEstimate === null) return fraction >= 0.1 ? byPace : null;
  const w = Math.min(1, fraction / 0.33);
  return w * byPace + (1 - w) * byEstimate;
}

/** «осталось ~4 мин», «осталось меньше минуты», «осталось ~1 ч 10 мин». */
export function etaText(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return "осталось меньше минуты";
  const min = Math.round(seconds / 60);
  if (min < 60) return `осталось ~${min} мин`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `осталось ~${h} ч ${m} мин` : `осталось ~${h} ч`;
}

/** «прошло 3 мин» — для работ без оценки (установка движка). */
export function elapsedText(seconds: number): string {
  if (seconds < 60) return "прошло меньше минуты";
  return `прошло ${Math.floor(seconds / 60)} мин`;
}

/**
 * Шаг сглаживания: показанное значение догоняет цель по экспоненте с
 * постоянной `tauMs`. Близко к цели — встаёт ровно на неё.
 */
export function easeToward(current: number, target: number, dtMs: number, tauMs = 450): number {
  if (dtMs <= 0) return current;
  const next = current + (target - current) * (1 - Math.exp(-dtMs / tauMs));
  return Math.abs(target - next) < 0.002 ? target : next;
}

/** Секунды задачи с начала (по часам этой машины: резидент локальный). */
export function jobElapsed(job: Pick<Job, "started_at">, nowMs = Date.now()): number | null {
  return job.started_at ? Math.max(0, nowMs / 1000 - job.started_at) : null;
}

/** «1,2 из 3,1 ГБ» / «340 из 900 МБ» — объём скачанного. */
export function bytesText(done: number, total: number): string {
  const gb = total >= 1024 ** 3;
  const unit = gb ? 1024 ** 3 : 1024 ** 2;
  const fmt = (x: number) => (x / unit).toLocaleString("ru-RU", { maximumFractionDigits: gb ? 1 : 0 });
  return `${fmt(done)} из ${fmt(total)} ${gb ? "ГБ" : "МБ"}`;
}

/** Подпись хода загрузки по задаче: байты и проценты, если резидент их сообщает. */
export function downloadDetail(job: Pick<Job, "done" | "total">): string | null {
  if (!job.total || job.total <= 0 || typeof job.done !== "number") return null;
  const pct = Math.floor((Math.min(job.done, job.total) / job.total) * 100);
  return job.total > 1024 * 1024 ? `${bytesText(job.done, job.total)} · ${pct} %` : `${pct} %`;
}
