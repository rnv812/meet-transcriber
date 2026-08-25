/**
 * Формы, которые отдаёт control API резидента (`meet.control`).
 *
 * Держим ровно то, что панель показывает: снимок состояния и события шины.
 * Виды событий совпадают со строками из `meet/events.py` — они уходят в SSE
 * как есть.
 */

/** Сигнал детектора звонка. null значит «ответить нечем» (ключа в реестре нет,
 *  pycaw не встал) — это не то же самое, что «нет». */
export type Signal = boolean | null;

/** Состояние машины детектора: про звонок, а не про нашу запись. */
export type WatcherState = "idle" | "recording" | "grace" | "suppressed" | null;

export type AutoRecord = {
  enabled: boolean;
  processes: string[];
  grace_seconds: number;
  state: WatcherState;
  mic: Signal;
  render: Signal;
};

export type Snapshot = {
  status: "idle" | "recording";
  source: "auto" | "manual" | null;
  folder: string | null;
  elapsed_s: number;
  levels: Record<string, number>;
  auto_record: AutoRecord;
  recordings_dir: string;
  gpu_busy: boolean;
};

export type CommandResult = Snapshot & {
  ok: boolean;
  action: string;
};

/** Событие шины. `kind` — вид, остальное зависит от вида (см. events.py). */
export type Job = {
  id: string;
  kind: string;
  folder: string;
  state: "queued" | "running" | "done" | "failed" | "cancelled";
  stage: string | null;
  label: string | null;
  done: number | null;
  total: number | null;
  note: string | null;
  result: string | null;
  error: string | null;
};

export type Recording = {
  id: string;
  path: string;
  started_at: string | null;
  duration_s: number | null;
  has_transcript: boolean;
  has_voices: boolean;
  title: string | null;
};

export type BusEvent = {
  kind: string;
  at: number;
  [key: string]: unknown;
};

export type ProgressEvent = BusEvent & {
  stage: string;
  label: string;
  done: number | null;
  total: number | null;
  note: string | null;
};

export type LevelEvent = BusEvent & {
  levels: Record<string, number>;
};

export type LogEvent = BusEvent & {
  text: string;
  source?: string;
};

export type JobEvent = BusEvent & { job: Job };

export const isProgress = (e: BusEvent): e is ProgressEvent =>
  e.kind === "progress";
export const isJob = (e: BusEvent): e is JobEvent => e.kind.startsWith("job.");
export const isLevel = (e: BusEvent): e is LevelEvent =>
  e.kind === "record.level";
export const isLog = (e: BusEvent): e is LogEvent => e.kind === "log";
