/**
 * Ход долгих задач для полоски прогресса: доля, подпись этапа и оценка
 * оставшегося времени.
 *
 * Резидент присылает по задаче `stage`/`label`, `done`/`total` внутри этапа и
 * (новые версии) `step`/`steps` — номер этапа и их число, `fraction` — общую
 * долю 0…1 с весами этапов, `estimate_s` — ожидаемую длительность всей работы
 * по `SPEED_FACTOR`. Старый резидент без них — доля из `done/total`, а без
 * `total` — «неизвестно» (бегущая полоска, а не полная шкала).
 *
 * С 0.3.1 резидент сообщает ещё `cap` — долю в конце текущего шага: между
 * событиями полоска продлевается по скорости хода (`extrapolate`), но не дальше
 * неё. У задач модели — `part`/`parts` («окно 2 из 4»), `phase` (подшаг),
 * `slow` («дольше обычного») и `eta_s` (сколько осталось, когда оценка уверенная).
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

/** «осталось ~4 мин», «осталось ~40 с», «осталось ~1 ч 10 мин». */
export function etaText(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 8) return "осталось несколько секунд";
  if (seconds < 55) return `осталось ~${Math.round(seconds / 5) * 5} с`;
  if (seconds < 90) return "осталось ~1 мин";
  const min = Math.round(seconds / 60);
  if (min < 60) return `осталось ~${min} мин`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `осталось ~${h} ч ${m} мин` : `осталось ~${h} ч`;
}

/** Прошедшее время часами: «0:07», «1:12», «1:02:03». */
export function clockText(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** Подшаг вызова модели — для подписи хода задачи модели. */
export const PHASES: Record<string, string> = {
  request: "модель думает",
  generating: "модель пишет ответ",
  validating: "проверка ответа",
  repair: "исправление ответа",
};

/** Задача модели (анализ, итоги, улучшение): ход по подшагам. */
export const isModelProgress = (job: { phase?: string | null }) => typeof job.phase === "string";

/**
 * Сколько осталось, секунд, или null — не уверены. Задача модели: «дольше
 * обычного» — ничего, своя оценка резидента (`eta_s`) — она; без неё молчим.
 * Расшифровка и прочее — по оценке и замеру (`etaSeconds`).
 */
export function jobEta(job: { phase?: string | null; slow?: boolean | null; eta_s?: number | null; estimate_s?: number | null },
  fraction: number | null, elapsedS: number | null): number | null {
  if (job.slow) return null;
  if (typeof job.eta_s === "number" && Number.isFinite(job.eta_s)) return Math.max(0, job.eta_s);
  if (isModelProgress(job)) return null;
  return etaSeconds(fraction, elapsedS, job.estimate_s);
}

// --- продление полоски между событиями ---------------------------------------

/** Значение резидента и когда оно пришло (мс, `performance.now()`). */
export type Sample = { v: number; t: number };

/** Окно замера скорости, мс: старше — не в счёт (ход мог смениться). */
export const VELOCITY_WINDOW_MS = 30_000;
/** Скорость не больше этой (доля в мс): рывок в одном событии — не темп. */
export const MAX_RATE = 0.2 / 1000;
/** Без `cap` (старый резидент) — не дальше этого от последнего значения. */
export const MAX_LEAD = 0.05;
/** «Готово» — только от резидента: продление не доходит до полной полоски. */
export const EXTRAPOLATE_TOP = 0.995;

/** Скорость хода по недавним значениям, доля в мс (≥ 0). */
export function velocity(samples: Sample[], windowMs = VELOCITY_WINDOW_MS): number {
  const last = samples.at(-1);
  if (!last) return 0;
  const recent = samples.filter((s) => last.t - s.t <= windowMs);
  const first = recent[0]!;
  if (recent.length < 2 || last.t <= first.t || last.v <= first.v) return 0;
  return Math.min(MAX_RATE, (last.v - first.v) / (last.t - first.t));
}

/**
 * Сколько показывать в момент `now` между событиями: последнее значение плюс
 * темп последних событий × прошедшее время. Не дальше `cap` (следующая
 * известная отметка — конец шага) и не дольше `maxAheadMs` после последнего
 * события: резидент замолчал — полоска встаёт, а не уезжает к концу.
 */
export function extrapolate(samples: Sample[], now: number, cap?: number | null): number | null {
  const last = samples.at(-1);
  if (!last) return null;
  if (last.v >= 1) return 1;
  const ceiling = Math.min(EXTRAPOLATE_TOP, typeof cap === "number" && cap > last.v ? cap : last.v + MAX_LEAD);
  const v = velocity(samples);
  if (v <= 0 || ceiling <= last.v) return last.v;
  const dt = Math.min(Math.max(0, now - last.t), maxAheadMs(samples));
  return Math.min(ceiling, last.v + v * dt);
}

/** Сколько продлевать после последнего события: 2,5 обычных промежутка (1,5…15 с). */
export function maxAheadMs(samples: Sample[]): number {
  const recent = samples.slice(-6);
  if (recent.length < 2) return 1500;
  const gap = (recent.at(-1)!.t - recent[0]!.t) / (recent.length - 1);
  return Math.min(15_000, Math.max(1500, gap * 2.5));
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
