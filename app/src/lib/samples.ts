/**
 * Образец голоса — одна фраза записи (панель «Спикеры», «Разделить спикера»,
 * «Переразделить», «Голоса»): ▶ играет её от начала до конца, не дольше
 * SAMPLE_MAX_S — страховка от слишком длинного сегмента.
 */
import { clock } from "./format";

/** Страховка: дольше образец не играет. */
export const SAMPLE_MAX_S = 10;

/** Подпись вместо образца, когда подходящей фразы нет (▶ не показываем). */
export const NO_PHRASE = "нет подходящей фразы";

/** Где образец остановится. */
export const sampleUntil = (s: { start: number; end: number }) => Math.min(s.end, s.start + SAMPLE_MAX_S);

/** «00:12 · 4 с» — начало образца и сколько он звучит. */
export function sampleCaption(s: { start: number; end: number }): string {
  const seconds = Math.max(1, Math.round(sampleUntil(s) - s.start));
  return `${clock(s.start)} · ${seconds} с`;
}
