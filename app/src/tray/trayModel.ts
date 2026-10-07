/**
 * Чистая логика панели записи (строка меню macOS): что показывать по снимку
 * резидента. Компонент (`TrayPanel`) только рисует то, что решено здесь.
 */

import { notSaved } from "../lib/recordingStop";
import type { Recording, Snapshot } from "../lib/types";

/** Сколько после остановки панель предлагает «Открыть запись» крупно. */
export const JUST_STOPPED_MS = 10 * 60_000;

/** Ассистент в идущей записи: включён, запускается, выключается — или его нет. */
export type AssistantMark = "on" | "starting" | "stopping" | null;

export type Phase =
  /** Резидента нет или он не отвечает. */
  | { kind: "offline" }
  /** Ещё не знаем: адрес ищем, первый снимок не пришёл. */
  | { kind: "connecting" }
  /**
   * Идёт запись: обычная (`liveOnly: false`, остановка — `/recording/stop`)
   * или с ассистентом с самого начала (`liveOnly: true`, остановка —
   * `/live/stop`), как у кнопки записи в окне. `temporary` — временная
   * встреча (не сохранится).
   */
  | { kind: "recording"; liveOnly: boolean; assistant: AssistantMark; auto: boolean; temporary: boolean }
  /** Запись с ассистентом запускается (грузится модель). */
  | { kind: "live-starting" }
  /** Запись с ассистентом останавливается: ассистент дописывает запись. */
  | { kind: "saving" }
  | { kind: "idle" };

export function phaseOf(snapshot: Snapshot | null, online: boolean | null): Phase {
  if (online === false) return { kind: "offline" };
  if (!snapshot) return { kind: online === null ? "connecting" : "offline" };
  const live = snapshot.live;
  // Звук уже идёт, а модель ещё грузится (`ready: false`; старый резидент поля не
  // присылает — тогда `active` и значит «слушает»).
  const warming = !!live?.active && live.ready === false;
  if (snapshot.status === "recording") {
    const attached = !!live?.attached;
    const assistant: AssistantMark = !attached ? null
      : live?.stopping ? "stopping" : live?.starting || warming ? "starting" : live?.active ? "on" : null;
    return {
      kind: "recording", liveOnly: false, assistant, auto: snapshot.source === "auto",
      temporary: !!snapshot.temporary,
    };
  }
  if (live?.stopping) return { kind: "saving" };
  if (warming || live?.starting) return { kind: "live-starting" };
  if (live?.active) return { kind: "recording", liveOnly: true, assistant: "on", auto: false, temporary: false };
  return { kind: "idle" };
}

/**
 * Секунды идущей записи на момент `nowMs`. Обычная — `elapsed_s` из снимка
 * плюс время с его прихода (`snapshotAt`); с ассистентом — от `started_at`
 * (стенное время резидента). Неизвестно — null.
 */
export function elapsedOf(snapshot: Snapshot, snapshotAt: number, nowMs: number): number | null {
  if (snapshot.status === "recording") {
    return snapshot.elapsed_s + Math.max(0, nowMs - snapshotAt) / 1000;
  }
  const started = snapshot.live?.started_at;
  return started == null ? null : Math.max(0, nowMs / 1000 - started);
}

/** Id записи — имя её папки (резидент пишет пути Windows или POSIX). */
export function folderId(path: string | null | undefined): string | null {
  if (!path) return null;
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? null;
}

/** Папка записи, которая идёт сейчас (своя или ассистента), — её нет среди «последних». */
export function runningFolder(snapshot: Snapshot | null): string | null {
  if (!snapshot) return null;
  if (snapshot.status === "recording") return folderId(snapshot.folder);
  const live = snapshot.live;
  if (live && (live.active || live.starting || live.stopping)) return folderId(live.folder);
  return null;
}

/**
 * Запись, закончившаяся между двумя снимками: обычная или с ассистентом с
 * самого начала (у включённого посреди записи ассистента запись — обычная).
 */
export function stoppedBetween(prev: Snapshot | null, next: Snapshot): string | null {
  // Временная встреча не сохраняется: «Открыть запись» после неё не к чему. А
  // сохранённая как обычная получает имя в библиотеке только при переносе
  // (может быть «…_2») — его скажет резидент (`last_stop`, `freshStop`).
  if (!prev || (prev.status === "recording" && (prev.temporary || inTemporaryRoot(prev.folder)))) return null;
  if (prev.status === "recording" && next.status !== "recording") return folderId(prev.folder);
  const was = prev.live;
  const now = next.live;
  const wasRunning = !!was && !was.attached && (was.active || was.stopping);
  const running = !!now && (now.active || now.starting || now.stopping);
  if (wasRunning && !running && prev.status !== "recording") return folderId(was?.folder);
  return null;
}

/** Папка внутри корня временных встреч (`…/tmp-meetings/<сеанс>/…`). */
export const inTemporaryRoot = (path: string | null | undefined) => !!path && /[\\/]tmp-meetings[\\/]/.test(path);

/** Остановка, о которой сказал сам резидент (`last_stop`): свежая и сохранённая. */
export function freshStop(snapshot: Snapshot | null, nowMs: number): string | null {
  const stop = snapshot?.last_stop;
  if (!stop || notSaved(stop.reason)) return null;
  if (nowMs - stop.at * 1000 > JUST_STOPPED_MS) return null;
  return folderId(stop.folder);
}

/** Последняя сохранённая запись: самая свежая, кроме той, что пишется сейчас. */
export function latestSaved(items: Recording[], snapshot: Snapshot | null): Recording | null {
  const running = runningFolder(snapshot);
  return items.find((item) => item.id !== running) ?? null;
}

/**
 * «Только что остановлена»: эту запись остановили не дольше `JUST_STOPPED_MS`
 * назад — по словам резидента или по тому, что панель видела сама
 * (`observed` — id и когда).
 */
export function isJustStopped(
  recent: Recording | null,
  snapshot: Snapshot | null,
  observed: { id: string; at: number } | null,
  nowMs: number,
): boolean {
  if (!recent) return false;
  if (freshStop(snapshot, nowMs) === recent.id) return true;
  return !!observed && observed.id === recent.id && nowMs - observed.at <= JUST_STOPPED_MS;
}
