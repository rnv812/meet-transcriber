/**
 * Что из разметки встречи показывать: раздел настроек «Расшифровка: подсветка
 * и разметка» (`transcript_view`) вместе с «Анализом встречи» (`analysis`):
 * часть, которую не размечают, не показывается, даже если включена здесь.
 */

import type { ViewParts } from "./analysisView";

export type CurveMode = "always" | "hover" | "off";
export const CURVE_MODES: CurveMode[] = ["always", "hover", "off"];

export type MarkupPrefs = {
  /** Какие части анализа брать (размечены и не выключены в «Анализе встречи»). */
  parts: ViewParts;
  /** Значки типов у реплик и фильтры по типам. */
  typeIcons: boolean;
  /** Полоса слева у важных реплик. */
  keyBorder: boolean;
  /** Заголовки глав в ленте. */
  chapterHeads: boolean;
  /** Блок «Наблюдения». */
  insights: boolean;
  /** Кривая важности над полосой плеера. */
  curve: CurveMode;
  /** Подписи глав под полосой плеера. */
  barLabels: boolean;
};

export const DEFAULT_PREFS: MarkupPrefs = {
  parts: { types: true, importance: true, chapters: true, insights: true },
  typeIcons: true, keyBorder: true, chapterHeads: true, insights: true, curve: "hover", barLabels: true,
};

type Raw = Record<string, unknown> | undefined;
const flag = (section: Raw, key: string) => section?.[key] !== false;

/** Настройки (`GET /settings`) → что показывать. Нет раздела — всё включено (по умолчанию). */
export function markupPrefs(settings: Record<string, unknown> | null | undefined): MarkupPrefs {
  const view = settings?.transcript_view as Raw;
  const analysis = settings?.analysis as Raw;
  const curve = view?.curve;
  return {
    parts: {
      types: flag(analysis, "types"),
      importance: flag(analysis, "importance"),
      chapters: flag(analysis, "chapters"),
      insights: flag(analysis, "insights"),
    },
    typeIcons: flag(view, "types"),
    keyBorder: flag(view, "importance"),
    chapterHeads: flag(view, "chapters"),
    insights: flag(view, "insights"),
    curve: CURVE_MODES.includes(curve as CurveMode) ? (curve as CurveMode) : DEFAULT_PREFS.curve,
    barLabels: flag(view, "bar_labels"),
  };
}
