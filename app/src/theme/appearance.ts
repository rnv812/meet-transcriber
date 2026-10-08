/**
 * Оформление окна (0.4, Atlas Aurora): тема, палитра сияния, вид сияния и
 * живое сияние. Источник истины — `ui.*` настроек резидента; окно ставит
 * атрибуты на <html> (data-theme, data-aurora, data-aurora-style,
 * data-motion), а копию держит в localStorage, чтобы следующее открытие
 * сразу было в нужной теме (public/appearance-boot.js).
 */

export type ThemePref = "system" | "dark" | "light";
export type Palette = "violet" | "green" | "blue" | "red" | "amber";
export type AuroraStyle = "glow" | "waves";
export type Appearance = { theme: ThemePref; aurora: Palette; auroraStyle: AuroraStyle; motion: boolean };

export const THEMES: readonly ThemePref[] = ["system", "dark", "light"];
export const PALETTES: readonly Palette[] = ["violet", "green", "blue", "red", "amber"];
export const STYLES: readonly AuroraStyle[] = ["glow", "waves"];
export const DEFAULT_APPEARANCE: Appearance = { theme: "system", aurora: "violet", auroraStyle: "glow", motion: true };
export const CACHE_KEY = "meet.appearance";

const pick = <T extends string>(value: unknown, allowed: readonly T[], fallback: T): T =>
  typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;

function fromFields(v: Record<string, unknown>, styleKey: "aurora_style" | "auroraStyle"): Appearance {
  return {
    theme: pick(v.theme, THEMES, DEFAULT_APPEARANCE.theme),
    aurora: pick(v.aurora, PALETTES, DEFAULT_APPEARANCE.aurora),
    auroraStyle: pick(v[styleKey], STYLES, DEFAULT_APPEARANCE.auroraStyle),
    motion: typeof v.motion === "boolean" ? v.motion : DEFAULT_APPEARANCE.motion,
  };
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

export function appearanceFromSettings(raw: unknown): Appearance {
  const ui = isRecord(raw) && isRecord(raw.ui) ? raw.ui : {};
  return fromFields(ui, "aurora_style");
}

export function appearanceToSettings(a: Appearance): { ui: Record<string, unknown> } {
  return { ui: { theme: a.theme, aurora: a.aurora, aurora_style: a.auroraStyle, motion: a.motion } };
}

export function resolveTheme(pref: ThemePref, systemDark: boolean): "dark" | "light" {
  return pref === "system" ? (systemDark ? "dark" : "light") : pref;
}

export function systemPrefersDark(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : true;
}

export function applyAppearance(root: HTMLElement, a: Appearance, systemDark: boolean): void {
  root.dataset.theme = resolveTheme(a.theme, systemDark);
  root.dataset.aurora = a.aurora;
  root.dataset.auroraStyle = a.auroraStyle;
  if (a.motion) delete root.dataset.motion;
  else root.dataset.motion = "paused";
}

export function readCached(storage: Storage | undefined = globalThis.localStorage): Appearance {
  try {
    const text = storage?.getItem(CACHE_KEY);
    if (!text) return DEFAULT_APPEARANCE;
    const v: unknown = JSON.parse(text);
    if (!isRecord(v) || !THEMES.includes(v.theme as ThemePref) || !PALETTES.includes(v.aurora as Palette)) {
      return DEFAULT_APPEARANCE;
    }
    return fromFields(v, "auroraStyle");
  } catch {
    return DEFAULT_APPEARANCE;
  }
}

export function writeCached(a: Appearance, storage: Storage | undefined = globalThis.localStorage): void {
  try {
    storage?.setItem(CACHE_KEY, JSON.stringify(a));
  } catch {
    // Хранилище недоступно — следующий запуск начнётся с умолчаний, это не ошибка.
  }
}
