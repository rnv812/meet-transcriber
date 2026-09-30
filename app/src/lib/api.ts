/**
 * Клиент control API резидента.
 *
 * * в приложении — Rust отдаёт порт с токеном командой `endpoint`;
 * * в браузере (dev) — Vite проксирует `/api` и сам подставляет токен.
 *
 * IMPORTANT: токен для SSE/аудио/аватаров уходит в query — EventSource и <audio>
 * не умеют ставить заголовки. Сервер это поддерживает намеренно (см. control.py).
 */

import { inTauri, invoke } from "./shell";
import type {
  BusEvent, CommandResult, Job, Person, PersonCard, Recording, Sample, Snapshot, Transcript,
} from "./types";

export type Endpoint = {
  /** Без слэша на конце: "http://127.0.0.1:53127" или "/api". */
  base: string;
  /** null в dev: токен подставляет прокси. */
  token: string | null;
};

export class NoResidentError extends Error {}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Не найден — NoResidentError: штатное состояние, а не сбой. */
export async function resolveEndpoint(): Promise<Endpoint> {
  if (!inTauri()) return { base: "/api", token: null };
  const data = await invoke<{ port: number; token: string } | null>("endpoint");
  if (!data?.port) throw new NoResidentError("резидент не запущен");
  return { base: `http://127.0.0.1:${data.port}`, token: data.token };
}

function auth(ep: Endpoint): Record<string, string> {
  return ep.token ? { Authorization: `Bearer ${ep.token}` } : {};
}

async function request(ep: Endpoint, path: string, init: RequestInit, contentType: string | null): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${ep.base}${path}`, {
      ...init,
      headers: { ...auth(ep), ...(contentType ? { "Content-Type": contentType } : {}) },
    });
  } catch (cause) {
    throw new NoResidentError(`резидент не отвечает: ${String(cause)}`);
  }
  if (response.status === 401) throw new ApiError(401, "неверный токен резидента");
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    let message = text;
    try {
      const parsed = JSON.parse(text) as { error?: unknown };
      if (typeof parsed.error === "string") message = parsed.error;
    } catch {
      /* не JSON — оставляем текст как есть */
    }
    throw new ApiError(response.status, message || `резидент ответил ${response.status}`);
  }
  return response;
}

async function json<T>(ep: Endpoint, path: string, init: RequestInit = {}): Promise<T> {
  return (await (await request(ep, path, init, "application/json")).json()) as T;
}

const body = (method: string, data: unknown): RequestInit => ({ method, body: JSON.stringify(data) });
const enc = encodeURIComponent;

export const getState = (ep: Endpoint) => json<Snapshot>(ep, "/state");

// --- записи ----------------------------------------------------------------

export function getRecordings(ep: Endpoint, q?: string) {
  const qs = q ? `?q=${enc(q)}` : "";
  return json<{ root: string; items: Recording[] }>(ep, `/recordings${qs}`);
}
export const getRecording = (ep: Endpoint, id: string) =>
  json<Recording & { transcript: Transcript | null }>(ep, `/recordings/${enc(id)}`);
export const patchRecording = (ep: Endpoint, id: string, patch: { title: string | null }) =>
  json<Recording>(ep, `/recordings/${enc(id)}`, body("PATCH", patch));
export const deleteRecording = (ep: Endpoint, id: string) =>
  json<{ ok: boolean }>(ep, `/recordings/${enc(id)}`, { method: "DELETE" });
export const importFile = (ep: Endpoint, path: string) =>
  json<{ recording: string; job: Job }>(ep, "/recordings/import", body("POST", { path }));
export const transcribe = (ep: Endpoint, id: string) =>
  json<Job>(ep, `/recordings/${enc(id)}/transcribe`, body("POST", {}));
export const saveTranscript = (ep: Endpoint, id: string, transcript: Transcript) =>
  json<{ ok: boolean; path?: string; error?: string }>(
    ep, `/recordings/${enc(id)}/transcript`, body("PUT", transcript));
export const nameSpeakers = (ep: Endpoint, id: string, mapping: Record<string, string>) =>
  json<{ ok: boolean; renamed: number; enrolled: string[]; voices_error: string | null }>(
    ep, `/recordings/${enc(id)}/speakers`, body("POST", mapping));
export const exportRecording = (ep: Endpoint, id: string, format: string) =>
  json<{ filename: string; content: string }>(ep, `/recordings/${enc(id)}/export?format=${enc(format)}`);

export const getJobs = (ep: Endpoint) => json<{ items: Job[] }>(ep, "/jobs");
export const cancelJob = (ep: Endpoint, id: string) =>
  json<{ ok: boolean }>(ep, `/jobs/${enc(id)}`, { method: "DELETE" });

export const recordingCommand = (ep: Endpoint, command: "start" | "stop" | "cancel") =>
  json<CommandResult>(ep, `/recording/${command}`, { method: "POST" });
export const setAutoRecord = (ep: Endpoint, enabled: boolean) =>
  json<Snapshot>(ep, "/auto-record", body("POST", { enabled }));

// --- люди ------------------------------------------------------------------

export const getPeople = (ep: Endpoint) => json<{ items: Person[] }>(ep, "/voices");
export const getPerson = (ep: Endpoint, name: string) => json<PersonCard>(ep, `/voices/${enc(name)}`);
export const getSample = (ep: Endpoint, name: string) => json<Sample>(ep, `/voices/${enc(name)}/sample`);
export async function putAvatar(ep: Endpoint, name: string, blob: Blob): Promise<void> {
  await request(ep, `/voices/${enc(name)}/avatar`, { method: "PUT", body: blob },
    blob.type || "application/octet-stream");
}
export const deleteAvatar = (ep: Endpoint, name: string) =>
  json<{ ok: boolean }>(ep, `/voices/${enc(name)}/avatar`, { method: "DELETE" });
export const renamePerson = (ep: Endpoint, name: string, to: string) =>
  json<unknown>(ep, `/voices/${enc(name)}/rename`, body("POST", { to }));
export const mergePerson = (ep: Endpoint, name: string, into: string) =>
  json<unknown>(ep, `/voices/${enc(name)}/merge`, body("POST", { into }));
export const deletePerson = (ep: Endpoint, name: string) =>
  json<unknown>(ep, `/voices/${enc(name)}`, { method: "DELETE" });

// --- настройки и сервис -----------------------------------------------------

export type Processes = {
  available: boolean;
  running?: string[];
  selected?: string[];
  known?: string[];
  error?: string;
};
export type Devices = {
  available: boolean;
  system?: { name: string; rate: number };
  mic?: { name: string; rate: number };
  pinning: boolean;
  error?: string;
};
export type EngineState = {
  installed: boolean;
  missing: string[];
  components: { module: string; title: string; installed: boolean }[];
  gpu: { available: boolean; name: string | null };
  flavor: "cuda" | "cpu";
  download_gb: number;
  python: string;
  target: string;
  ffmpeg: boolean;
};
export type Model = {
  id: string;
  kind: "asr" | "diarization" | "align";
  title: string;
  note: string;
  size_gb: number;
  gated?: boolean;
  recommended?: boolean;
  downloaded: boolean;
  size_on_disk: number;
  selected: boolean;
  blocked: boolean;
};
export type ModelsState = {
  items: Model[];
  cache: string;
  token: boolean;
  selected: string | null;
  can_download: boolean;
};

export const getSettings = (ep: Endpoint) => json<Record<string, unknown>>(ep, "/settings");
export const patchSettings = (ep: Endpoint, updates: Record<string, unknown>) =>
  json<{ settings: Record<string, unknown>; restart_required: string[] }>(ep, "/settings", body("PATCH", updates));
export type Hotwords = { text: string; budget: number; used: number };
export const getHotwords = (ep: Endpoint) => json<Hotwords>(ep, "/hotwords");
export const putHotwords = (ep: Endpoint, text: string) =>
  json<Hotwords>(ep, "/hotwords", body("PUT", { text }));
export const getEngine = (ep: Endpoint) => json<EngineState>(ep, "/engine");
export const installEngine = (ep: Endpoint, flavor?: "cuda" | "cpu") =>
  json<Job>(ep, "/engine/install", body("POST", flavor ? { flavor } : {}));
export const getModels = (ep: Endpoint) => json<ModelsState>(ep, "/models");
export const downloadModel = (ep: Endpoint, id: string) =>
  json<Job>(ep, "/models/download", body("POST", { id }));
export const getDiagnostics = (ep: Endpoint, lines = 200) =>
  json<Record<string, unknown>>(ep, `/diagnostics?lines=${lines}`);
export const getDevices = (ep: Endpoint) => json<Devices>(ep, "/devices");
export const getProcesses = (ep: Endpoint) => json<Processes>(ep, "/processes");

// --- URL для <audio>/<img> ---------------------------------------------------

export function audioUrl(ep: Endpoint, id: string, track: "sys" | "mic" | "source"): string {
  const q = new URLSearchParams({ track });
  if (ep.token) q.set("token", ep.token);
  return `${ep.base}/recordings/${enc(id)}/audio?${q}`;
}

/** `version` сбрасывает кэш картинки после смены аватара. */
export function avatarUrl(ep: Endpoint, name: string, version: number): string {
  const parts: string[] = [];
  if (ep.token) parts.push(`token=${enc(ep.token)}`);
  parts.push(`v=${version}`);
  return `${ep.base}/voices/${enc(name)}/avatar?${parts.join("&")}`;
}

// --- события -----------------------------------------------------------------

const EVENT_KINDS = [
  "job.queued", "job.started", "job.progress", "job.done", "job.failed",
  "record.started", "record.stopped", "record.discarded", "record.device",
  "record.waiting", "record.silence", "record.level", "progress", "log", "error",
];

/** Первым сообщением сервер присылает `state` со снимком. Возвращает закрытие. */
export function openEvents(
  ep: Endpoint,
  handlers: {
    onSnapshot?: (s: Snapshot) => void;
    onEvent?: (e: BusEvent) => void;
    onError?: () => void;
  },
): () => void {
  const query = ep.token ? `?token=${enc(ep.token)}` : "";
  const source = new EventSource(`${ep.base}/events${query}`);
  const parse = (raw: string): unknown => {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  };
  source.addEventListener("state", (m) => {
    const data = parse((m as MessageEvent<string>).data);
    if (data) handlers.onSnapshot?.(data as Snapshot);
  });
  source.onmessage = (m) => {
    const data = parse(m.data as string);
    if (data) handlers.onEvent?.(data as BusEvent);
  };
  // Подписываемся широко: новый вид события в Python не требует правки фронта.
  for (const kind of EVENT_KINDS) {
    source.addEventListener(kind, (m) => {
      const data = parse((m as MessageEvent<string>).data);
      if (data) handlers.onEvent?.(data as BusEvent);
    });
  }
  source.onerror = () => handlers.onError?.();
  return () => source.close();
}
