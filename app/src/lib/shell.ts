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
  a.click();
  URL.revokeObjectURL(url);
  return name;
}

export async function pickMedia(): Promise<string | null> {
  if (!inTauri()) return null;
  return invoke<string | null>("pick_media");
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
