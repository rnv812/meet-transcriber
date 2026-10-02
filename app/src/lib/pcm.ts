/**
 * Раздел «Модель PCM» профиля: подписи типов и «этажи». Модель упоминается
 * только описательно: Process Communication Model — модель Тайби Кейлера,
 * PCM — товарный знак Kahler Communications (см. NOTICE).
 */

import type { Pcm, PcmType } from "./types";

export const PCM_TYPES: PcmType[] = ["thinker", "persister", "harmonizer", "imaginer", "rebel", "promoter"];

/** Нейтральные русские названия типов и оригинал. */
export const PCM_LABELS: Record<PcmType, { ru: string; en: string }> = {
  thinker: { ru: "Логик", en: "Thinker" },
  persister: { ru: "Упорный", en: "Persister" },
  harmonizer: { ru: "Гармонизатор", en: "Harmonizer" },
  imaginer: { ru: "Мечтатель", en: "Imaginer" },
  rebel: { ru: "Бунтарь", en: "Rebel" },
  promoter: { ru: "Деятель", en: "Promoter" },
};

export const PCM_HYPOTHESIS = "Гипотеза по репликам во встречах, не сертифицированная оценка";
export const PCM_FLOOR_MAX = 5;

/** «Логик (Thinker)». */
export const pcmLabel = (t: PcmType) => `${PCM_LABELS[t].ru} (${PCM_LABELS[t].en})`;

export type Floor = { type: PcmType; value: number; base: boolean; phase: boolean };

/**
 * «Этажи» снизу вверх: база — первый (нижний) этаж, дальше — по убыванию
 * выраженности (при равенстве — в порядке модели).
 */
export function pcmFloors(pcm: Pcm): Floor[] {
  const value = (t: PcmType) => Math.max(0, Math.min(PCM_FLOOR_MAX, Math.round(pcm.floors?.[t] ?? 0)));
  const rest = PCM_TYPES.filter((t) => t !== pcm.base.type)
    .sort((a, b) => value(b) - value(a) || PCM_TYPES.indexOf(a) - PCM_TYPES.indexOf(b));
  return [pcm.base.type, ...rest].map((type) => ({
    type, value: value(type), base: type === pcm.base.type, phase: type === pcm.phase?.type,
  }));
}

/** Текстовая замена диаграммы для экранного диктора. */
export function floorsText(pcm: Pcm): string {
  const parts = pcmFloors(pcm).map((f, n) => {
    const marks = [f.base ? "база" : "", f.phase ? "фаза" : ""].filter(Boolean).join(", ");
    return `${n + 1}: ${pcmLabel(f.type)} — ${f.value} из ${PCM_FLOOR_MAX}${marks ? ` (${marks})` : ""}`;
  });
  return `Этажи модели PCM снизу вверх. ${parts.join("; ")}.`;
}

export const percent = (x: number) => `${Math.round(Math.max(0, Math.min(1, x)) * 100)} %`;
