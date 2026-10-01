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
  /** Запись с ассистентом; `status` выше при ней остаётся "idle". Нет у старых резидентов. */
  live?: LiveStatus;
  /** Папка для встреч в базе знаний (`export.meetings_dir`); не задана — null. */
  meetings_dir?: string | null;
  /** Последний сбой автоматической выгрузки в базу знаний. */
  kb_export_failed?: { folder: string; error: string; at: number } | null;
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
  /** Секунды эпохи; старые резиденты их не присылали. */
  created_at?: number;
  started_at?: number | null;
  finished_at?: number | null;
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
  /** Когда записан транскрипт (секунды эпохи), нет — null. */
  transcript_at?: number | null;
  /** Спикеры не разделены: "skipped_no_token" — нет токена HF, "skipped_no_access" — HF отказал. */
  diarization?: string | null;
  /** Выгрузка в базу знаний: куда и когда; `error` — последняя не удалась. Не выгружалась — null. */
  kb_export?: KbExportRecord | null;
};

export type KbExportRecord = {
  path?: string | null;
  at?: number | null;
  /** Наши файлы: имя → SHA-256 записанного (null — без хеша, например аудио). */
  files?: Record<string, string | null> | string[];
  /** Не перезаписаны: изменены вручную или лежали в папке до выгрузки. */
  kept?: string[];
  error?: string | null;
};

/** `POST /recordings/{id}/kb-export`: папка встречи, записанные файлы и не перезаписанные (правленые вручную). */
export type KbExport = { path: string; files: string[]; kept: string[] };

/** `GET /export/preview`: как назовётся папка (относительно папки для встреч) и что в ней будет. */
export type ExportPreview = { folder: string | null; files: string[]; error: string | null };

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

// --- ассистент ---------------------------------------------------------------

/** `GET /recordings/{id}/summary`; итогов нет — 404. */
export type Summary = { markdown: string; created_at: number | null };

/** Пара из `qa.jsonl`; `at` — секунды эпохи. */
export type QaItem = { q: string; a: string; at: number; provider: string | null };

/** Что видит резидент: CLI найдены, локальная модель отвечает. */
export type ProviderAvailability = { found: boolean; path?: string | null; base_url?: string };

/**
 * `GET /assistant`. `provider` — кто ответит сейчас; null при `checking` значит
 * «ещё считается», а не «никого нет».
 */
export type AssistantInfo = {
  provider: string | null;
  setting: string;
  available: Record<string, ProviderAvailability>;
  knowledge_dir: string | null;
  checking: boolean;
  /** Нет у резидента прежней версии. */
  proxy?: ProxyInfo;
};

/**
 * Прокси, который получат Claude Code и Codex (`llm.proxy`): режим сохранённой
 * настройки, действующий адрес (логин и пароль скрыты) и откуда он; `system` —
 * что дал бы вариант «как в системе» (переменные среды или прокси Windows).
 */
export type ProxyInfo = {
  mode: "system" | "none" | "custom";
  effective: string | null;
  source: "env" | "system" | "setting" | null;
  system: string | null;
};

/** `POST /assistant/check`. */
export type ProviderCheck = { ok: boolean; error?: string | null; provider?: string | null };

/** Состояние живого режима, общая часть ответов `/live/start` и `/live/stop`. */
export type LiveStatus = {
  active: boolean;
  starting: boolean;
  stopping: boolean;
  folder: string | null;
  error: string | null;
  started_at: number | null;
};

/** `event: state` потока `/live/events`: дайджест, хвост ленты, статус дайджестера. */
export type LiveState = { digest: string; transcript: string[]; status: string | null };

/** `event: line`: новая строка ленты; `t` — секунды от начала записи. */
export type LiveLine = { t: number; speaker: string | null; text: string };
