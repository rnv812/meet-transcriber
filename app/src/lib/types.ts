/**
 * Формы, которые отдаёт control API резидента (`meet.control`).
 * Виды событий совпадают со строками из `meet/events.py` — они уходят в SSE как есть.
 */

export type Signal = boolean | null;
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
  disk_free_gb: number | null;
  last_stop: { folder: string; reason: "saved" | "discarded" | "short"; at: number } | null;
};

export type CommandResult = Snapshot & { ok: boolean; action: string };

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
  tracks: Record<string, string>;
  has_transcript: boolean;
  has_voices: boolean;
  title: string | null;
  source: string;
};

export type Segment = {
  start: number;
  end: number;
  speaker: string | null;
  text: string;
  uncertain: boolean;
};

export type Transcript = {
  version: number;
  title: string | null;
  segments: Segment[];
  names?: Record<string, string>;
};

export type Person = {
  name: string;
  samples: number;
  meetings: number;
  seconds: number;
  has_avatar: boolean;
  color: string;
};

export type PersonMeeting = {
  recording: string;
  title: string | null;
  started_at: string | null;
  seconds: number;
};

export type PersonCard = {
  name: string;
  color: string;
  has_avatar: boolean;
  samples: number;
  meetings: PersonMeeting[];
};

export type Sample = { recording: string; start: number; end: number; track: string };

export type BusEvent = { kind: string; at: number; [key: string]: unknown };
export type ProgressEvent = BusEvent & {
  stage: string;
  label: string;
  done: number | null;
  total: number | null;
  note: string | null;
};
export type LevelEvent = BusEvent & { levels: Record<string, number> };
export type JobEvent = BusEvent & { job: Job };

export const isJob = (e: BusEvent): e is JobEvent => e.kind.startsWith("job.");
export const isLevel = (e: BusEvent): e is LevelEvent => e.kind === "record.level";
