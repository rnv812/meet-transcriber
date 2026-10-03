/**
 * Ширины боковых панелей окна: навигация, список записей, меню настроек,
 * панель человека в «Голосах», панель «Спикеры встречи».
 *
 * Пользователь тянет разделитель — ширина запоминается в этом окне
 * (localStorage, ключ на панель). Запомненная ширина — пожелание: в узком окне
 * панели сжимаются (сначала пропорционально, потом навигация — в полосу
 * значков), карточке записи остаётся не меньше CARD_MIN. Окно снова шире —
 * панели возвращаются к запомненной ширине.
 */

/** Карточка записи не уже этого — боковые панели сжимаются раньше неё. */
export const CARD_MIN = 520;
/** Окно до этой ширины — навигация по умолчанию полосой значков (как и раньше, до 1000 px). */
export const NARROW = 1000;
/** Шаг клавиатуры у разделителя. */
export const KEY_STEP = 16;

export const NAV = { def: 200, min: 140, max: 320, rail: 56, snap: 100 } as const;
export const LIST = { def: 320, min: 260, max: 560 } as const;

/**
 * Навигация: «auto» — полоса значков в узком окне (до NARROW), иначе полная;
 * «rail» — пользователь свернул её в полосу; «open» — развернул в узком окне
 * (полная, пока помещается вместе со списком и карточкой).
 */
export type NavMode = "auto" | "rail" | "open";
export type ShellPrefs = { nav: number; navMode: NavMode; list: number };
export type ShellFit = { nav: number; rail: boolean; list: number };

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * Ширины навигации и списка для окна шириной `win`. Не хватает места для
 * карточки — панели сжимаются пропорционально (не уже своих минимумов), потом
 * навигация сворачивается в полосу значков; список уже LIST.min не становится.
 */
export function fitShell(win: number, p: ShellPrefs): ShellFit {
  const avail = win - CARD_MIN;
  const list = clamp(p.list, LIST.min, LIST.max);
  const rail = p.navMode === "rail" || (p.navMode === "auto" && win <= NARROW);
  if (!rail) {
    const nav = clamp(p.nav, NAV.min, NAV.max);
    if (nav + list <= avail) return { nav, rail: false, list };
    const k = avail / (nav + list);
    const navFit = Math.max(NAV.min, Math.round(nav * k));
    const listFit = Math.max(LIST.min, avail - navFit);
    if (navFit + listFit <= avail) return { nav: navFit, rail: false, list: listFit };
  }
  return { nav: NAV.rail, rail: true, list: Math.max(LIST.min, Math.min(list, avail - NAV.rail)) };
}

/** Предел разделителя навигации: карточка не уже CARD_MIN при нынешнем списке. */
export function navMax(win: number, fit: ShellFit): number {
  return Math.max(NAV.min, Math.min(NAV.max, win - CARD_MIN - fit.list));
}

/** Предел разделителя списка: карточка не уже CARD_MIN при нынешней навигации. */
export function listMax(win: number, fit: ShellFit): number {
  return Math.max(LIST.min, Math.min(LIST.max, win - CARD_MIN - fit.nav));
}

/** Навигацию отпустили на ширине `w`: полоса значков или полная (и в узком окне — тоже). */
export function navCommit(w: number, win: number, prev: ShellPrefs): ShellPrefs {
  if (w <= NAV.rail) return { ...prev, navMode: "rail" };
  return { ...prev, nav: w, navMode: win <= NARROW ? "open" : "auto" };
}

/** Панель внутри области: пределы и ширина при ширине области `room`. */
export type PaneSpec = {
  def: number;
  min: number;
  max: number;
  /** Сколько оставить остальной области (сетке, тексту); по умолчанию 0. */
  reserve?: number | ((room: number) => number);
};

/**
 * Пределы панели в области шириной `room` (0 — ещё не измерена: только min/max).
 * Область слишком узкая — панель уже своего минимума, но никогда не шире
 * области за вычетом `reserve`: не наезжает на соседа и не выходит за край.
 */
export function paneBounds(spec: PaneSpec, room: number): { min: number; max: number } {
  if (!room) return { min: spec.min, max: spec.max };
  const reserve = typeof spec.reserve === "function" ? spec.reserve(room) : spec.reserve ?? 0;
  const max = Math.max(0, Math.min(spec.max, room - reserve));
  return { min: Math.min(spec.min, max), max };
}

export function paneWidth(spec: PaneSpec, want: number, room: number): number {
  const b = paneBounds(spec, room);
  return clamp(want, b.min, b.max);
}

/**
 * Ширина после перетаскивания до `raw` px. С `snap` (навигация): уже `snap.below` —
 * сворачивается в `snap.to`, между ним и `min` — встаёт на `min`.
 */
export function dragWidth(raw: number, min: number, max: number, snap?: { below: number; to: number }): number {
  if (snap) {
    if (raw < snap.below) return snap.to;
    if (raw < min) return min;
  }
  return Math.round(clamp(raw, min, max));
}

/**
 * Ширина после клавиши: `dir` +1 — шире, −1 — уже. С `snap` шаг уже минимума
 * сворачивает в `snap.to`, шаг шире из `snap.to` — разворачивает до минимума.
 */
export function stepWidth(cur: number, dir: 1 | -1, min: number, max: number, snap?: { below: number; to: number }): number {
  if (snap && cur <= snap.to) return dir > 0 ? min : snap.to;
  const next = cur + dir * KEY_STEP;
  if (snap && next < min) return snap.to;
  return clamp(next, min, max);
}

const PREFIX = "meet.pane.";

/** Запомненная ширина панели; хранилища нет или там мусор — `null`. */
export function loadWidth(name: string): number | null {
  try {
    const raw = window.localStorage?.getItem(PREFIX + name);
    const n = raw == null ? NaN : Number(raw);
    return Number.isFinite(n) && n > 0 ? Math.round(n) : null;
  } catch {
    return null;
  }
}

export function saveWidth(name: string, w: number | null): void {
  try {
    if (w == null) window.localStorage?.removeItem(PREFIX + name);
    else window.localStorage?.setItem(PREFIX + name, String(Math.round(w)));
  } catch { /* хранилище недоступно: ширина живёт до перезапуска */ }
}

export function loadShell(): ShellPrefs {
  let navMode: NavMode = "auto";
  try {
    const m = window.localStorage?.getItem(PREFIX + "nav-mode");
    if (m === "rail" || m === "open") navMode = m;
  } catch { /* хранилище недоступно */ }
  return { nav: loadWidth("nav") ?? NAV.def, navMode, list: loadWidth("list") ?? LIST.def };
}

export function saveShell(p: ShellPrefs): void {
  saveWidth("nav", p.nav === NAV.def ? null : p.nav);
  saveWidth("list", p.list === LIST.def ? null : p.list);
  try {
    if (p.navMode === "auto") window.localStorage?.removeItem(PREFIX + "nav-mode");
    else window.localStorage?.setItem(PREFIX + "nav-mode", p.navMode);
  } catch { /* хранилище недоступно */ }
}
