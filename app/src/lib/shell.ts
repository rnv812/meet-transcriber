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
