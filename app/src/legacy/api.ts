/**
 * Клиент control API резидента.
 *
 * Два способа найти резидента, один и тот же код поверх:
 *
 * * **в приложении** — Rust читает `%LOCALAPPDATA%/meet/daemon.json` и отдаёт
 *   порт с токеном командой `endpoint`; запросы идут прямо на 127.0.0.1;
 * * **в браузере (dev)** — dev-сервер Vite проксирует `/api` и сам подставляет
 *   заголовок с токеном: из страницы файл прочитать нельзя.
 *
 * IMPORTANT: токен для SSE уходит в query — браузерный EventSource не умеет
 * ставить заголовки. Сервер это поддерживает намеренно (см. control.py).
 */

import type { BusEvent, CommandResult, Job, Recording, Snapshot } from "./types";

export type Endpoint = {
  /** Базовый адрес без слэша на конце: "http://127.0.0.1:53127" или "/api". */
  base: string;
  /** null в dev-режиме: токен подставляет прокси. */
  token: string | null;
};

type TauriWindow = Window & {
  __TAURI_INTERNALS__?: unknown;
  __TAURI__?: { core?: { invoke: (cmd: string, args?: object) => Promise<unknown> } };
};

export const inTauri = (): boolean =>
  typeof window !== "undefined" &&
  (window as TauriWindow).__TAURI_INTERNALS__ !== undefined;

async function invoke<T>(cmd: string, args?: object): Promise<T> {
  const { invoke: call } = await import("@tauri-apps/api/core");
  return call<T>(cmd, args as Record<string, unknown> | undefined);
}

export class NoResidentError extends Error {}

/** Найти резидента. Не найден — NoResidentError: это штатное состояние
 *  («дежурный не запущен»), а не сбой, и панель показывает его как экран. */
export async function resolveEndpoint(): Promise<Endpoint> {
  if (!inTauri()) return { base: "/api", token: null };
  const data = await invoke<{ port: number; token: string } | null>("endpoint");
  if (!data?.port) throw new NoResidentError("резидент не запущен");
  return { base: `http://127.0.0.1:${data.port}`, token: data.token };
}

/** Поднять резидента (`meet-tray --watch`), если его нет. */
export async function startResident(): Promise<string> {
  return invoke<string>("start_resident");
}

/** Открыть окно настроек (создаётся оболочкой при первом вызове). */
export async function openSettings(): Promise<void> {
  if (!inTauri()) {
    // В браузере окон нет — открываем ту же страницу вкладкой.
    window.open("/index.html", "_blank");
    return;
  }
  await invoke<void>("open_settings");
}

/** Спрятать панель: вернуть её можно из трея. Крестик нужен потому, что у окна
 *  нет рамки — закрыть его больше нечем. */
export async function hidePanel(): Promise<void> {
  if (!inTauri()) return;
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  await getCurrentWindow().hide();
}

/** Ширина панели и поле вокруг неё под тень (см. panel.css). */
export const PANEL_WIDTH = 340;
export const PANEL_MARGIN = 14;

let lastHeight = 0;

/**
 * Подогнать окно под высоту панели.
 *
 * Окно прозрачное, поэтому его лишняя высота видна дважды: тень панели
 * обрезается краями окна в жёсткий прямоугольник, а невидимая часть окна
 * перехватывает клики по тому, что под ней. Меряем `scrollHeight`, а не
 * фактическую высоту, иначе ограничение по окну и подгонка зациклятся.
 */
export async function fitWindowTo(panel: HTMLElement): Promise<void> {
  if (!inTauri()) return;
  const wanted = Math.ceil(panel.scrollHeight) + PANEL_MARGIN * 2;
  const capped = Math.min(wanted, Math.round(window.screen.availHeight * 0.7));
  if (Math.abs(capped - lastHeight) < 2) return;
  lastHeight = capped;
  const { getCurrentWindow, LogicalSize } = await import("@tauri-apps/api/window");
  await getCurrentWindow().setSize(
    new LogicalSize(PANEL_WIDTH + PANEL_MARGIN * 2, capped),
  );
}

/**
 * Начать перетаскивание окна.
 *
 * Явный вызов, а не только `data-tauri-drag-region`: атрибут работает лишь
 * когда элемент под курсором — ровно тот, на котором он висит, и любая правка
 * вёрстки шапки может это молча сломать. Здесь окно тащится, пока шапка
 * получает mousedown, и это не зависит от разметки.
 */
export async function startDragging(): Promise<void> {
  if (!inTauri()) return;
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  await getCurrentWindow().startDragging();
}

function headers(endpoint: Endpoint): HeadersInit {
  return endpoint.token
    ? { Authorization: `Bearer ${endpoint.token}`, "Content-Type": "application/json" }
    : { "Content-Type": "application/json" };
}

async function json<T>(endpoint: Endpoint, path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${endpoint.base}${path}`, {
      ...init,
      headers: headers(endpoint),
    });
  } catch (cause) {
    // Резидент мог упасть или ещё не поднялся: файл endpoint остаётся,
    // а порт уже не отвечает — та же болезнь, что у lock-файлов.
    throw new NoResidentError(`резидент не отвечает: ${String(cause)}`);
  }
  if (response.status === 401) throw new Error("неверный токен резидента");
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`резидент ответил ${response.status}: ${detail}`);
  }
  return (await response.json()) as T;
}

export const getState = (endpoint: Endpoint): Promise<Snapshot> =>
  json<Snapshot>(endpoint, "/state");

export const getSettings = (endpoint: Endpoint): Promise<Record<string, unknown>> =>
  json(endpoint, "/settings");

export const getDiagnostics = (
  endpoint: Endpoint,
  lines = 200,
): Promise<Record<string, unknown>> => json(endpoint, `/diagnostics?lines=${lines}`);

export type Processes = {
  available: boolean;
  running?: string[];
  selected?: string[];
  known?: string[];
  error?: string;
};

/** Запущенные процессы — чтобы выбирать клиент конференции из списка. */
export const getProcesses = (endpoint: Endpoint): Promise<Processes> =>
  json<Processes>(endpoint, "/processes");

export type Devices = {
  available: boolean;
  system?: { name: string; rate: number };
  mic?: { name: string; rate: number };
  pinning: boolean;
  error?: string;
};

/** Что увидит запись. Только для показа: закрепить устройство нельзя — запись
 *  следит за дефолтными endpoint'ами и переживает их смену. */
export const getDevices = (endpoint: Endpoint): Promise<Devices> =>
  json<Devices>(endpoint, "/devices");

/** Библиотека записей: источник истины — папки на диске, не индекс. */
export const getRecordings = (
  endpoint: Endpoint,
): Promise<{ root: string; items: Recording[] }> => json(endpoint, "/recordings");

/** Поставить расшифровку в очередь резидента. */
export const transcribeRecording = (
  endpoint: Endpoint,
  id: string,
  options: Record<string, unknown> = {},
): Promise<Job> =>
  json<Job>(endpoint, `/recordings/${encodeURIComponent(id)}/transcribe`, {
    method: "POST",
    body: JSON.stringify(options),
  });

export const getJobs = (endpoint: Endpoint): Promise<{ items: Job[] }> =>
  json(endpoint, "/jobs");

// --- редактор -------------------------------------------------------------

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

export const getRecording = (
  endpoint: Endpoint,
  id: string,
): Promise<Recording & { transcript: Transcript | null }> =>
  json(endpoint, `/recordings/${encodeURIComponent(id)}`);

export const getTranscript = (endpoint: Endpoint, id: string): Promise<Transcript> =>
  json(endpoint, `/recordings/${encodeURIComponent(id)}/transcript`);

/** Сохранить правки редактора (текст, разбивка). */
export const saveTranscript = (
  endpoint: Endpoint,
  id: string,
  transcript: Transcript,
): Promise<{ ok: boolean; path?: string; error?: string }> =>
  json(endpoint, `/recordings/${encodeURIComponent(id)}/transcript`, {
    method: "PUT",
    body: JSON.stringify(transcript),
  });

/** Переименовать спикеров во всём транскрипте И записать их голоса в базу. */
export const nameSpeakers = (
  endpoint: Endpoint,
  id: string,
  mapping: Record<string, string>,
): Promise<{ ok: boolean; renamed: number; enrolled: string[]; voices_error: string | null }> =>
  json(endpoint, `/recordings/${encodeURIComponent(id)}/speakers`, {
    method: "POST",
    body: JSON.stringify(mapping),
  });

/** URL дорожки для плеера. Токен в query: <audio> не умеет ставить заголовки,
 *  а сервер это поддерживает (тот же путь, что у SSE). В dev прокси добавит его сам. */
export function audioUrl(endpoint: Endpoint, id: string, track: "sys" | "mic"): string {
  const q = new URLSearchParams({ track });
  if (endpoint.token) q.set("token", endpoint.token);
  return `${endpoint.base}/recordings/${encodeURIComponent(id)}/audio?${q}`;
}

/** Открыть папку записи в проводнике. */
export async function openFolder(path: string): Promise<void> {
  if (!inTauri()) return;
  await invoke<void>("open_folder", { path });
}

/** Открыть окно редактора; recording — id записи, чтобы сразу её открыть. */
export async function openEditor(recording?: string): Promise<void> {
  if (!inTauri()) {
    window.open("/editor.html", "_blank");
    return;
  }
  await invoke<void>("open_editor", { recording: recording ?? null });
}

export type EngineComponent = {
  module: string;
  title: string;
  installed: boolean;
};

export type EngineState = {
  installed: boolean;
  missing: string[];
  components: EngineComponent[];
  gpu: { available: boolean; name: string | null };
  flavor: "cuda" | "cpu";
  download_gb: number;
  python: string;
  target: string;
  ffmpeg: boolean;
};

/** Что установлено для расшифровки. Запись работает и без движка. */
export const getEngine = (endpoint: Endpoint): Promise<EngineState> =>
  json<EngineState>(endpoint, "/engine");

/** Поставить движок задачей резидента: это гигабайты и минуты. */
export const installEngine = (
  endpoint: Endpoint,
  flavor?: "cuda" | "cpu",
): Promise<Job> =>
  json<Job>(endpoint, "/engine/install", {
    method: "POST",
    body: JSON.stringify(flavor ? { flavor } : {}),
  });

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

/** Каталог моделей: что умеет пайплайн и что уже лежит в кэше Hugging Face. */
export const getModels = (endpoint: Endpoint): Promise<ModelsState> =>
  json<ModelsState>(endpoint, "/models");

/** Скачать модель задачей: это гигабайты. */
export const downloadModel = (endpoint: Endpoint, id: string): Promise<Job> =>
  json<Job>(endpoint, "/models/download", {
    method: "POST",
    body: JSON.stringify({ id }),
  });

export type RecordingCommand = "start" | "stop" | "cancel" | "adopt";

export const sendCommand = (
  endpoint: Endpoint,
  command: RecordingCommand,
): Promise<CommandResult> =>
  json<CommandResult>(endpoint, `/recording/${command}`, { method: "POST" });

export const patchSettings = (
  endpoint: Endpoint,
  updates: Record<string, unknown>,
): Promise<{ settings: Record<string, unknown>; restart_required: string[] }> =>
  json(endpoint, "/settings", { method: "PATCH", body: JSON.stringify(updates) });

/**
 * Подписаться на поток событий. Возвращает функцию закрытия.
 *
 * Первым сообщением сервер присылает `state` со снимком — панель рисуется, не
 * дожидаясь, пока что-нибудь произойдёт.
 */
export function openEvents(
  endpoint: Endpoint,
  handlers: {
    onSnapshot?: (snapshot: Snapshot) => void;
    onEvent?: (event: BusEvent) => void;
    onError?: () => void;
  },
): () => void {
  const query = endpoint.token ? `?token=${encodeURIComponent(endpoint.token)}` : "";
  const source = new EventSource(`${endpoint.base}/events${query}`);
  const parse = (raw: string): unknown => {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  };
  source.addEventListener("state", (message) => {
    const data = parse((message as MessageEvent<string>).data);
    if (data) handlers.onSnapshot?.(data as Snapshot);
  });
  // Именованные события шины: подписываемся широко, а не на каждый вид, чтобы
  // новый вид события в Python не требовал правки фронта.
  source.onmessage = (message) => {
    const data = parse(message.data as string);
    if (data) handlers.onEvent?.(data as BusEvent);
  };
  for (const kind of [
    "job.queued",
    "job.started",
    "job.progress",
    "job.done",
    "job.failed",
    "record.started",
    "record.stopped",
    "record.discarded",
    "record.device",
    "record.waiting",
    "record.silence",
    "record.level",
    "progress",
    "log",
    "error",
  ]) {
    source.addEventListener(kind, (message) => {
      const data = parse((message as MessageEvent<string>).data);
      if (data) handlers.onEvent?.(data as BusEvent);
    });
  }
  source.onerror = () => handlers.onError?.();
  return () => source.close();
}
