/** Обёртки над командами оболочки Tauri; в браузере (dev) — мягкие запасные пути. */

export const inTauri = (): boolean =>
  typeof window !== "undefined" &&
  (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ !== undefined;

export async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke: call } = await import("@tauri-apps/api/core");
  return call<T>(cmd, args);
}

export async function openFolder(path: string): Promise<void> {
  if (!inTauri()) return;
  await invoke<void>("open_folder", { path });
}

/** Сохранить текст файлом. Возвращает путь, null — отмена. */
export async function saveText(name: string, content: string): Promise<string | null> {
  if (inTauri()) return invoke<string | null>("save_text", { defaultName: name, content });
  const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.style.display = "none";
  // Firefox качает только по ссылке из документа; URL отзываем после того,
  // как браузер начал загрузку, — синхронный revoke её обрывает.
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  return name;
}

export async function pickMedia(): Promise<string | null> {
  if (!inTauri()) return null;
  return invoke<string | null>("pick_media");
}

/**
 * Диалог выбора папки (`start` — откуда начать). null — отказ. В браузере (dev)
 * диалога нет — путь вводится руками.
 */
export async function pickFolder(start?: string | null): Promise<string | null> {
  if (!inTauri()) return window.prompt("Путь к папке", start ?? "")?.trim() || null;
  return invoke<string | null>("pick_folder", { start: start ?? null });
}

/** Оболочка просит открыть запись (клик по уведомлению). */
export async function onOpenRecording(cb: (id: string) => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<string>("open-recording", (e) => cb(e.payload));
}

export function initialRecording(): string | null {
  return new URLSearchParams(window.location.search).get("recording");
}

/** Оболочка просит открыть раздел настроек (`assistant` — «Ассистент»). */
export async function onOpenSection(cb: (section: string) => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<string>("open-section", (e) => cb(e.payload));
}

/** Раздел настроек из адреса окна (`?section=assistant`). */
export function initialSection(): string | null {
  return new URLSearchParams(window.location.search).get("section");
}

// --- мастер первого запуска: движок, ссылки, автозапуск ---------------------

/** `engine_status` оболочки: движок расшифровки и место под него. */
export type EngineStatus = {
  installed: boolean;
  version: string;
  env_dir: string;
  profile: "cuda" | "cpu" | null;
  gpu: string | null;
  /** Свободно на диске с данными, ГБ; null — узнать нельзя (не блокируем). */
  free_gb: number | null;
  needs_gb: number;
};
/** Событие `engine-progress`: первая строка шага — его название. */
export type EngineProgress = { step: number; of: number; line: string };
/** Событие `engine-failed`: хвост лога упавшего шага (до 30 строк). */
export type EngineFailed = { step: number; tail: string };

/** Состояние движка (до 5 с: nvidia-smi). Вне приложения — null: оболочки нет. */
export async function engineStatus(): Promise<EngineStatus | null> {
  if (!inTauri()) return null;
  return invoke<EngineStatus>("engine_status");
}

/** Поставить движок (`fresh` — удалить окружение и поставить заново). Минуты. */
export async function installEngine(profile: "cuda" | "cpu", fresh: boolean): Promise<void> {
  await invoke<void>(fresh ? "reinstall_engine" : "install_engine", { profile });
}

async function listenShell<T>(event: string, cb: (payload: T) => void): Promise<() => void> {
  if (!inTauri()) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen<T>(event, (e) => cb(e.payload));
}

export const onEngineProgress = (cb: (p: EngineProgress) => void) => listenShell("engine-progress", cb);
export const onEngineFailed = (cb: (f: EngineFailed) => void) => listenShell("engine-failed", cb);

/** Что оболочка знает о резиденте ("running", "engine-missing", …); вне приложения — null. */
export async function residentStatus(): Promise<string | null> {
  if (!inTauri()) return null;
  return invoke<string>("resident_status");
}

/**
 * Открыть страницу в браузере. Оболочка открывает только свои адреса
 * (huggingface.co, claude.ai, github.com/openai/codex) — см. `open_url`.
 * Не открылась — только в журнал консоли: это ссылка, а не действие.
 */
export async function openUrl(url: string): Promise<void> {
  if (!inTauri()) {
    window.open(url, "_blank", "noopener,noreferrer");
    return;
  }
  await invoke<void>("open_url", { url }).catch((cause) => console.warn("open_url:", cause));
}

/** Отметка «мастер пройден» для оболочки: при старте она не откроет окно с мастером. */
export async function markWizardDone(): Promise<void> {
  if (!inTauri()) return;
  await invoke<void>("mark_wizard_done");
}

/**
 * Есть ли команда в оболочке: вызов без аргументов. Нет команды — Tauri
 * отвечает «Command … not found»; есть — ругается на аргументы (или
 * выполняется). Так окно работает и со старой оболочкой.
 */
export async function probeCommand(call: () => Promise<unknown>): Promise<boolean> {
  try {
    await call();
    return true;
  } catch (cause) {
    return !/^Command \S+ not found$/.test(String(cause));
  }
}

/**
 * Умеет ли оболочка автозапуск. IMPORTANT — контракт с оболочкой:
 * * `set_autostart` обязан принимать `enabled: bool` как ОБЯЗАТЕЛЬНЫЙ аргумент:
 *   проба зовёт команду без аргументов, и с необязательным `enabled` она бы
 *   выполнилась (переключила автозапуск) вместо отказа;
 * * проба опирается на то, что у команд приложения нет манифеста прав (ACL в
 *   build.rs): с ним неразрешённая команда отвечала бы «… not allowed», и
 *   проба сочла бы её существующей.
 */
export async function autostartAvailable(): Promise<boolean> {
  if (!inTauri()) return false;
  return probeCommand(() => invoke("set_autostart"));
}

/** Запускать ли приложение вместе с Windows. */
export async function setAutostart(enabled: boolean): Promise<void> {
  await invoke<void>("set_autostart", { enabled });
}
