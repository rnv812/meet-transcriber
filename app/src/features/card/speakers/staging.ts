/**
 * Отложенные правки панели «Спикеры»: что человек наметил, но ещё не применил.
 *
 * На строку — одна правка: имя (`rename`), «Неизвестный» (`reset`) или
 * «объединить с другим спикером» (`merge`). «Применить» шлёт их резиденту одним
 * набором — одним шагом истории, который можно отменить целиком.
 */

import { plural } from "../../../lib/format";
import { isUnnamed } from "../../../lib/speakers";
import type { SpeakerOp, SpeakerOpInput, SpeakerRow, SpeakerStep } from "../../../lib/types";

export type Change = { kind: "rename"; to: string } | { kind: "reset" } | { kind: "merge"; into: string };
export type Staged = Record<string, Change>;

/** Подпись строки после правок; null — станет «Спикер N» (номер выберет резидент). */
export function finalOf(label: string, staged: Staged, seen: Set<string> = new Set()): string | null {
  const c = staged[label];
  if (!c) return label;
  if (c.kind === "rename") return c.to;
  if (c.kind === "reset") return isUnnamed(label) ? label : null;
  if (seen.has(label)) return label;
  seen.add(label);
  return finalOf(c.into, staged, seen);
}

/** «Запомнить голос» по умолчанию: безымянный спикер получает имя (не «Это я»), и голос у него есть. */
export function rememberDefault(row: SpeakerRow, staged: Staged, owner: string): boolean {
  if (!row.has_voice || !isUnnamed(row.label) || !staged[row.label]) return false;
  const final = finalOf(row.label, staged);
  return final !== null && !isUnnamed(final) && final !== owner;
}

/** Правки в порядке строк, как их ждёт резидент. */
export function toOps(order: string[], staged: Staged): SpeakerOpInput[] {
  const ops: SpeakerOpInput[] = [];
  for (const label of order) {
    const c = staged[label];
    if (!c) continue;
    if (c.kind === "rename") ops.push({ type: "rename", label, to: c.to });
    else if (c.kind === "reset") ops.push({ type: "reset", label });
    else ops.push({ type: "merge", label, to: c.into });
  }
  return ops;
}

/** Что сделает правка, словами: «Спикер 2 → Анна». */
export function changeText(label: string, staged: Staged): string {
  const c = staged[label];
  if (!c) return "";
  if (c.kind === "rename") return `${label} → ${c.to}`;
  if (c.kind === "reset") return `${label} → без имени`;
  return `${label} → объединён со спикером «${finalOf(c.into, staged) ?? c.into}»`;
}

/** Строка предпросмотра под списком: «Будет изменено: …». */
export function preview(order: string[], staged: Staged): string {
  const parts = order.filter((l) => staged[l]).map((l) => changeText(l, staged));
  return parts.length ? `Будет изменено: ${parts.join(", ")}` : "";
}

/** Отбросить правки строк, которых больше нет (после отмены, повтора, перерасшифровки). */
export function prune(staged: Staged, labels: string[]): Staged {
  const have = new Set(labels);
  const out: Staged = {};
  for (const [label, c] of Object.entries(staged)) {
    if (!have.has(label)) continue;
    if (c.kind === "merge" && !have.has(c.into)) continue;
    out[label] = c;
  }
  return out;
}

function opText(op: SpeakerOp): string {
  if (op.type === "split") return `${op.label} разделён: ${op.into.join(", ")}`;
  if (op.type === "threshold") return `Порог узнавания ${Math.round(op.value * 100)}%`;
  if (op.type === "relabel") {
    const n = op.turns;
    return `${n} ${plural(n, "реплика", "реплики", "реплик")} (${op.from.join(", ")}) → ${op.to}`;
  }
  if (op.type === "merge") return `${op.from} объединён со спикером «${op.to}»`;
  if (op.type === "reset") return `${op.from} → без имени (${op.to})`;
  return `${op.from} → ${op.to}`;
}

/** Шаг истории словами: правки и чей голос запомнен. */
export function describeStep(step: SpeakerStep): string {
  const text = step.ops.map(opText).join(", ");
  const voices = [...new Set(step.enrolled.map((e) => e.person))];
  return voices.length ? `${text} · голос запомнен: ${voices.join(", ")}` : text;
}

/** Только время для шагов сегодняшнего дня, иначе дата и время. */
export function stepTime(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay ? time : `${d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}, ${time}`;
}
