/**
 * Ширины боковых панелей окна: список записей, меню настроек, панель человека
 * в «Голосах», панель «Спикеры встречи». Рейка разделов (app/Nav) — постоянной
 * ширины RAIL: режим «навигация шире/уже» ушёл в 0.4.
 *
 * Пользователь тянет разделитель — ширина запоминается в этом окне
 * (localStorage, ключ на панель). Запомненная ширина — пожелание: в узком окне
 * список сжимается, карточке записи остаётся не меньше CARD_MIN. Окно снова
 * шире — список возвращается к запомненной ширине.
 */

/** Карточка записи не уже этого — боковые панели сжимаются раньше неё. */
export const CARD_MIN = 520;
/** Шаг клавиатуры у разделителя. */
export const KEY_STEP = 16;
/** Рейка разделов (app/Nav, app/rail.css): постоянная ширина. */
export const RAIL = 60;
/**
 * Прежняя навигация (до 0.4: ширина и сворачивание в полосу значков). Окно её
 * больше не использует; оставлена для примера порога в ui/Splitter.test.
 * @deprecated рейка — RAIL.
 */
export const NAV = { def: 200, min: 140, max: 320, rail: 56, snap: 100 } as const;
export const LIST = { def: 300, min: 260, max: 560 } as const;

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * Ширина списка записей в окне шириной `win` при запомненной `want`: в своих
 * пределах, но карточке — не меньше CARD_MIN; уже LIST.min список не становится
 * (в самом узком окне сжимается карточка).
 */
export function fitList(win: number, want: number): number {
  return Math.max(LIST.min, Math.min(clamp(want, LIST.min, LIST.max), win - RAIL - CARD_MIN));
}

/** Предел разделителя списка: карточка не уже CARD_MIN рядом с рейкой. */
export function listMax(win: number): number {
  return Math.max(LIST.min, Math.min(LIST.max, win - CARD_MIN - RAIL));
}

/** Панель внутри области: пределы и ширина при ширине области `room`. */
export type PaneSpec = {
  /** Размер по умолчанию; нет — его задаёт CSS (доля области), пока панель не потянули. */
  def?: number;
  min: number;
  /**
   * Верхний предел; нет — панель ограничивает только место области за вычетом
   * `reserve` (у областей ассистента: соседу остаётся его минимум, и только).
   */
  max?: number;
  /** Сколько оставить остальной области (сетке, тексту); по умолчанию 0. */
  reserve?: number | ((room: number) => number);
};

/**
 * Пределы панели в области шириной `room` (0 — ещё не измерена: только min/max).
 * Область слишком узкая — панель уже своего минимума, но никогда не шире
 * области за вычетом `reserve`: не наезжает на соседа и не выходит за край.
 */
export function paneBounds(spec: PaneSpec, room: number): { min: number; max: number } {
  // Не измерена и предела нет — пока только минимум (размер — paneWidth, как просили).
  if (!room) return { min: spec.min, max: spec.max ?? spec.min };
  const reserve = typeof spec.reserve === "function" ? spec.reserve(room) : spec.reserve ?? 0;
  const max = Math.max(0, Math.min(spec.max ?? Infinity, room - reserve));
  return { min: Math.min(spec.min, max), max };
}

/**
 * Размер панели при желаемом `want` в области `room`: в пределах `paneBounds`.
 * Область сузилась (окно) — панель ужимается, но не уже своего минимума и так,
 * чтобы соседу осталось `reserve`; запомненный `want` при этом не меняется.
 */
export function paneWidth(spec: PaneSpec, want: number, room: number): number {
  if (!room) return Math.max(spec.min, spec.max == null ? want : Math.min(want, spec.max));
  const b = paneBounds(spec, room);
  return clamp(want, b.min, b.max);
}

/**
 * Ширина после перетаскивания до `raw` px. С `snap` (сворачиваемая панель): уже `snap.below` —
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

/** Ключи прежней навигации (до 0.4): ширина и режим «полоса значков». */
const LEGACY_NAV = ["nav", "nav-mode"];

/**
 * Запомненная ширина списка записей; заодно забывает ширину и режим прежней
 * навигации — рейка их не читает.
 */
export function loadList(): number {
  try {
    for (const key of LEGACY_NAV) window.localStorage?.removeItem(PREFIX + key);
  } catch { /* хранилище недоступно */ }
  return loadWidth("list") ?? LIST.def;
}

/** Ширина по умолчанию не хранится. */
export function saveList(w: number): void {
  saveWidth("list", w === LIST.def ? null : w);
}
