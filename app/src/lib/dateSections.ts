/**
 * Разделы списка записей по дате начала: «Сегодня», «Вчера», дни недели
 * (2–6 дней назад), «Ранее в октябре», месяцы за 12 назад, «Ранее в 2025»,
 * годы, «Без даты».
 *
 * `started_at` — местное время без зоны («2026-10-06T09:30:00»): разбирается
 * вручную, а не `new Date(…)` — та прочла бы голую дату как UTC.
 */

export type SectionKind = "today" | "yesterday" | "day" | "rest" | "month" | "rest-year" | "year" | "none";

export type DateSection = {
  /** Устойчивый ключ: today, yesterday, day:YYYY-MM-DD, rest:YYYY-MM, m:YYYY-MM, rest-y:YYYY, y:YYYY, none. */
  key: string;
  label: string;
  kind: SectionKind;
  /** Место среди разделов: меньше — выше (новее). */
  rank: number;
};

const WEEKDAYS = ["Воскресенье", "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"];
const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
  "Ноябрь", "Декабрь"];
/** «4 октября». */
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
  "ноября", "декабря"];
/** «Ранее в октябре». */
const MONTHS_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре",
  "ноябре", "декабре"];

/** Сколько месяцев назад ещё показываются отдельными разделами. */
const MONTHS_BACK = 12;

const pad = (n: number) => String(n).padStart(2, "0");

/**
 * «2026-10-06T09:30:00», «2026-10-06 09:30», «2026-10-06» (и доли секунды) —
 * местное время; со сдвигом зоны («Z», «+03:00»), с хвостом или с 10:75 — null.
 */
export function parseLocal(s: string | null | undefined): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?$/.exec(s ?? "");
  if (!m) return null;
  const [y, mo, d, h, mi, sec] = [1, 2, 3, 4, 5, 6].map((i) => Number(m[i] ?? 0)) as
    [number, number, number, number, number, number];
  // Время проверяется числами, а не обратным чтением из Date: 02:30 в ночь перевода
  // часов Date сдвинет на 03:30 — это всё ещё та же дата, а не «не дата».
  if (h > 23 || mi > 59 || sec > 59) return null;
  const date = new Date(y, mo - 1, d, h, mi, sec);
  // 31.02 и 13-й месяц Date молча переносит — такие строки не дата.
  if (date.getFullYear() !== y || date.getMonth() !== mo - 1 || date.getDate() !== d) return null;
  return date;
}

/** На сколько календарных дней `d` раньше `now` (по местным суткам, без сдвигов перехода на летнее время). */
export function daysBefore(d: Date, now: Date): number {
  const day = (x: Date) => Date.UTC(x.getFullYear(), x.getMonth(), x.getDate());
  return Math.round((day(now) - day(d)) / 86_400_000);
}

export function sectionOf(startedAt: string | null | undefined, now: Date): DateSection {
  const d = parseLocal(startedAt);
  if (!d) return { key: "none", label: "Без даты", kind: "none", rank: Number.MAX_SAFE_INTEGER };
  const days = daysBefore(d, now);
  if (days <= 0) return { key: "today", label: "Сегодня", kind: "today", rank: 0 };
  if (days === 1) return { key: "yesterday", label: "Вчера", kind: "yesterday", rank: 1 };
  const y = d.getFullYear();
  const mo = d.getMonth();
  if (days <= 6) {
    return {
      key: `day:${y}-${pad(mo + 1)}-${pad(d.getDate())}`, kind: "day", rank: days,
      label: `${WEEKDAYS[d.getDay()]}, ${d.getDate()} ${MONTHS_GEN[mo]}`,
    };
  }
  const back = (now.getFullYear() - y) * 12 + (now.getMonth() - mo);
  if (back <= 0) return { key: `rest:${y}-${pad(mo + 1)}`, label: `Ранее в ${MONTHS_PREP[mo]}`, kind: "rest", rank: 7 };
  if (back <= MONTHS_BACK) {
    return {
      key: `m:${y}-${pad(mo + 1)}`, kind: "month", rank: 7 + back,
      label: y === now.getFullYear() ? MONTHS[mo]! : `${MONTHS[mo]} ${y}`,
    };
  }
  // Год самого старого месяца-раздела: его остаток — «Ранее в …», что старше — по годам.
  const edge = new Date(now.getFullYear(), now.getMonth() - MONTHS_BACK, 1).getFullYear();
  const base = 8 + MONTHS_BACK;
  if (y === edge) return { key: `rest-y:${y}`, label: `Ранее в ${y}`, kind: "rest-year", rank: base };
  return { key: `y:${y}`, label: String(y), kind: "year", rank: base + 1 + (edge - y) };
}

/** Годовые разделы по умолчанию свёрнуты: старое не мешает листать недавнее. */
export function defaultOpen(section: Pick<DateSection, "kind">): boolean {
  return section.kind !== "year" && section.kind !== "rest-year";
}

export type SectionGroup<T> = { section: DateSection; items: T[] };

/** Записи по разделам; разделы — от новых к старым, внутри — в пришедшем порядке. Пустых нет. */
export function groupBySection<T extends { started_at: string | null }>(items: T[], now: Date): SectionGroup<T>[] {
  const byKey = new Map<string, SectionGroup<T>>();
  for (const item of items) {
    const section = sectionOf(item.started_at, now);
    let group = byKey.get(section.key);
    if (!group) byKey.set(section.key, group = { section, items: [] });
    group.items.push(item);
  }
  return [...byKey.values()].sort((a, b) => a.section.rank - b.section.rank);
}

// --- запоминание свёрнутых и развёрнутых ---------------------------------------

export const SECTIONS_KEY = "meet.sections.v1";
export const SECTIONS_MAX = 100;

/** Отклонения от умолчания: ключ раздела → развёрнут ли. */
export type SectionPrefs = Record<string, boolean>;

export function loadSectionPrefs(): SectionPrefs {
  try {
    const raw = window.localStorage.getItem(SECTIONS_KEY);
    const got: unknown = raw ? JSON.parse(raw) : {};
    if (!got || typeof got !== "object" || Array.isArray(got)) return {};
    return Object.fromEntries(Object.entries(got).filter(([, v]) => typeof v === "boolean")) as SectionPrefs;
  } catch {
    return {};
  }
}

export function saveSectionPrefs(prefs: SectionPrefs): void {
  try {
    if (Object.keys(prefs).length) window.localStorage.setItem(SECTIONS_KEY, JSON.stringify(prefs));
    else window.localStorage.removeItem(SECTIONS_KEY);
  } catch {
    /* состояние разделов просто не запомнится */
  }
}

/**
 * Новое состояние раздела: совпало с умолчанием — ключ убирается, иначе
 * встаёт последним (свежим). Сверх SECTIONS_MAX вытесняются самые давние.
 */
export function withPref(prefs: SectionPrefs, key: string, open: boolean, byDefault: boolean): SectionPrefs {
  const { [key]: _, ...rest } = prefs;
  if (open === byDefault) return rest;
  const next = Object.entries({ ...rest, [key]: open });
  return Object.fromEntries(next.slice(Math.max(0, next.length - SECTIONS_MAX)));
}

// --- «Только этот период» ---------------------------------------------------------

const ymdOf = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/**
 * Дни раздела (ГГГГ-ММ-ДД включительно) — для метки `дата:` из меню заголовка «Только этот
 * период»: день, «Ранее в октябре» (с 1-го по день до недельных разделов), месяц (без дней,
 * что в начале нового месяца ещё в разделах по дням), «Ранее в
 * 2025» (с 1 января по месяц до месячных разделов), год. «Без даты» — null.
 */
export function sectionRange(section: Pick<DateSection, "key">, now: Date): { from: string; to: string } | null {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const shift = (days: number) => ymdOf(new Date(today.getFullYear(), today.getMonth(), today.getDate() + days));
  const [kind, value = ""] = section.key.split(/:(.*)/);
  if (kind === "today") return { from: shift(0), to: shift(0) };
  if (kind === "yesterday") return { from: shift(-1), to: shift(-1) };
  if (kind === "day") return { from: value, to: value };
  const month = /^(\d{4})-(\d{2})$/.exec(value);
  if (kind === "m" && month) {
    // Конец месяца, попавший в разделы по дням (1–6 числа: «Суббота, 28 сентября»), — не этот раздел.
    const [y, m] = [Number(month[1]), Number(month[2]) - 1];
    const end = ymdOf(new Date(y, m + 1, 0));
    const week = shift(-7);
    return { from: `${value}-01`, to: end < week ? end : week };
  }
  // Остаток месяца: дни старше недельных разделов (7 дней назад и раньше).
  if (kind === "rest" && month) return { from: `${value}-01`, to: shift(-7) };
  if (kind === "y" && /^\d{4}$/.test(value)) return { from: `${value}-01-01`, to: `${value}-12-31` };
  if (kind === "rest-y" && /^\d{4}$/.test(value)) {
    // До первого дня самого старого месяца-раздела.
    const edge = new Date(now.getFullYear(), now.getMonth() - MONTHS_BACK, 1);
    const last = new Date(edge.getFullYear(), edge.getMonth(), 0);
    return { from: `${value}-01-01`, to: ymdOf(last) };
  }
  return null;
}
