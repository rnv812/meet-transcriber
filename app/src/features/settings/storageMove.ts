/**
 * Идущий перенос движка и моделей — вне компонента. Посреди переноса резидент
 * перезапускается из новой папки, связь с ним на секунды пропадает, и раздел
 * настроек пересобирается; ход и итог переноса должны это пережить.
 */

import { useSyncExternalStore } from "react";
import { errorText } from "../../lib/format";
import {
  type EngineProgress, type StorageProgress, onEngineProgress, onStorageProgress, storageMove, storageStatus,
} from "../../lib/shell";

export type MoveState =
  | { kind: "idle" }
  /** `engine` — шаг установки движка (первая часть переноса). */
  | { kind: "running"; progress: StorageProgress | null; engine: EngineProgress | null }
  | { kind: "done"; path: string }
  | { kind: "failed"; error: string };

let state: MoveState = { kind: "idle" };
const listeners = new Set<() => void>();

function set(next: MoveState): void {
  state = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export const moveState = (): MoveState => state;
export const useMove = (): MoveState => useSyncExternalStore(subscribe, moveState);

async function listen(): Promise<() => void> {
  const offs = await Promise.all([
    onStorageProgress((progress) => { if (state.kind === "running") set({ ...state, progress }); }),
    onEngineProgress((engine) => { if (state.kind === "running") set({ ...state, engine }); }),
  ]);
  return () => offs.forEach((off) => off());
}

/** Начать перенос в `target` (уже проверенную папку `storage_check`). */
export async function startMove(target: string): Promise<void> {
  if (state.kind === "running") return;
  set({ kind: "running", progress: null, engine: null });
  const stop = await listen().catch(() => () => {});
  try {
    set({ kind: "done", path: await storageMove(target) });
  } catch (cause) {
    set({ kind: "failed", error: errorText(cause) });
  } finally {
    stop();
  }
}

/**
 * Окно открыли посреди переноса, начатого в закрытом окне: ход — из событий,
 * конец — когда оболочка скажет, что переноса больше нет.
 */
export async function attachMove(pollMs = 2000): Promise<void> {
  if (state.kind === "running") return;
  set({ kind: "running", progress: null, engine: null });
  const stop = await listen().catch(() => () => {});
  await new Promise<void>((resolve) => {
    const timer = setInterval(() => {
      storageStatus()
        .then((status) => { if (!status?.moving) { clearInterval(timer); resolve(); } })
        .catch(() => {});
    }, pollMs);
  });
  stop();
  set({ kind: "idle" });
}

/** Убрать итог прошлого переноса (новый выбор папки). */
export function clearMove(): void {
  if (state.kind !== "running") set({ kind: "idle" });
}

/**
 * Что человек уже отложил или видел в вопросах переноса (`StorageNotices`).
 * Живёт с окном, а не с компонентом: тот монтируется, только пока служба
 * записи на связи, и после любого её перезапуска спросил бы снова.
 */
export type Notices = { leftovers?: boolean; interrupted?: string; done?: string };
let notices: Notices = {};

export const noticesState = (): Notices => notices;

export function dismissNotice(change: Notices): void {
  notices = { ...notices, ...change };
  listeners.forEach((listener) => listener());
}

/** Сведения о хранении поменялись (ответ об остатках, отмена): перечитать везде. */
let storageTick = 0;

export function bumpStorage(): void {
  storageTick += 1;
  listeners.forEach((listener) => listener());
}

export const useStorageTick = (): number => useSyncExternalStore(subscribe, () => storageTick);
export const useNotices = (): Notices => useSyncExternalStore(subscribe, noticesState);

export function resetMoveForTests(): void {
  notices = {};
  set({ kind: "idle" });
}
