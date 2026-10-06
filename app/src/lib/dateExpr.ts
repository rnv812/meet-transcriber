/**
 * Даты в строке поиска (`дата:`, `после:`, `до:`, подсказки, «Период» в «Фильтрах»):
 * текст → диапазон дней `{from, to}` (ГГГГ-ММ-ДД включительно, местные сутки) и подпись.
 *
 * Понимает: сегодня, вчера, позавчера; эта/прошлая неделя (с понедельника по
 * воскресенье), этот/прошлый месяц, этот/прошлый год; «5 окт», «5 октября [2025]»;
 * «05.10», «5.10.26», «05.10.2026», «2026-10-05»; «сентябрь [2025]», «в сентябре»;
 * «2025»; диапазоны «с 1.09 по 15.09», «1.09-15.09», «1.09..15.09» (и словами).
 *
 * Без года — последнее такое число не позже сегодняшнего (5 октября при «сегодня
 * 6 октября 2026» — 2026, 7 октября — 2025; 29.02 — последний високосный год).
 * С `{ thisYear: true }` (`до:`) — ближайшее к сегодня такое число, даже будущее: верхняя
 * граница в будущем значит «по сегодня», а 3 января `до:31.12` — конец прошлого года.
 * Несуществующая дата (31.02, 13-й месяц) — null.
 *
 * Диапазон держится за начало: начало без года — последнее такое не позже сегодня,
 * конец без года — в том же году. Конец раньше начала — переход через Новый год, только
 * если месяц конца раньше месяца начала и выходит не больше полугода («с 20.12 по 10.01»);
 * иначе (тот же месяц, «с 15.10 по 1.09», оба года указаны) — null: годовой диапазон
 * из опечатки хуже, чем «не понял» (`dateHint` объясняет почему).
 */

export type DateRange = { from: string; to: string; label: string };

const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
  "Ноябрь", "Декабрь"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
  "ноября", "декабря"];
const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
/** Формы названий месяцев, по которым узнаётся месяц (и любое их начало от трёх букв). */
const MONTH_FORMS: string[][] = [
  ["январь", "января", "январе"], ["февраль", "февраля", "феврале"], ["март", "марта", "марте"],
  ["апрель", "апреля", "апреле"], ["май", "мая", "мае"], ["июнь", "июня", "июне"], ["июль", "июля", "июле"],
  ["август", "августа", "августе"], ["сентябрь", "сентября", "сентябре"], ["октябрь", "октября", "октябре"],
  ["ноябрь", "ноября", "ноябре"], ["декабрь", "декабря", "декабре"],
];

const MIN_YEAR = 1970;
const MAX_YEAR = 2100;
/** На сколько лет назад искать 29 февраля без года. */
const LEAP_LOOKBACK = 8;
/** Диапазон через Новый год — не длиннее (дней): «с 15.10 по 1.09» — опечатка, а не 11 месяцев. */
const MAX_WRAP_DAYS = 183;

const pad = (n: number) => String(n).padStart(2, "0");
/** Местная дата → «ГГГГ-ММ-ДД». */
export const ymd = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const day = (y: number, m: number, d: number) => new Date(y, m, d);
const valid = (y: number, m: number, d: number) => {
  const x = day(y, m, d);
  return x.getFullYear() === y && x.getMonth() === m && x.getDate() === d;
};
const today = (now: Date) => day(now.getFullYear(), now.getMonth(), now.getDate());
const addDays = (d: Date, n: number) => day(d.getFullYear(), d.getMonth(), d.getDate() + n);
const lastOfMonth = (y: number, m: number) => day(y, m + 1, 0);

/** «5 октября», другой год — «5 октября 2025». */
export function dayText(d: Date, now: Date): string {
  const base = `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}`;
  return d.getFullYear() === now.getFullYear() ? base : `${base} ${d.getFullYear()}`;
}
/** Коротко для диапазона: «5 окт», другой год — «5 окт 2025». */
function dayShort(d: Date, now: Date): string {
  const base = `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}`;
  return d.getFullYear() === now.getFullYear() ? base : `${base} ${d.getFullYear()}`;
}
const monthText = (y: number, m: number, now: Date) => (y === now.getFullYear() ? MONTHS[m]! : `${MONTHS[m]} ${y}`);

/** «ГГГГ-ММ-ДД» → местная дата; не такая строка или нет такого дня — null. */
export function parseYmd(s: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]) - 1, Number(m[3])];
  return valid(y, mo, d) ? day(y, mo, d) : null;
}

/** Подпись диапазона дней: день, месяц целиком, год целиком или «1 сен – 15 сен». */
export function rangeLabel(from: string, to: string, now: Date): string {
  const a = parseYmd(from);
  const b = parseYmd(to);
  if (!a || !b) return `${from} – ${to}`;
  if (from === to) return dayText(a, now);
  const wholeMonth = a.getDate() === 1 && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth()
    && b.getDate() === lastOfMonth(b.getFullYear(), b.getMonth()).getDate();
  if (wholeMonth) return monthText(a.getFullYear(), a.getMonth(), now);
  if (a.getMonth() === 0 && a.getDate() === 1 && b.getMonth() === 11 && b.getDate() === 31
    && a.getFullYear() === b.getFullYear()) return String(a.getFullYear());
  return `${dayShort(a, now)} – ${dayShort(b, now)}`;
}

/** Месяц по слову: «окт», «октября», «сентябре», «сент»; не месяц — -1. */
export function monthOf(word: string): number {
  const w = word.replace(/\.$/, "");
  if (w.length < 3) return -1;
  const found = MONTH_FORMS.findIndex((forms) => forms.some((f) => f.startsWith(w)));
  return found;
}

/** Разобранная, но ещё не привязанная к году дата. */
type Spec =
  | { kind: "day"; d: number; m: number; y: number | null }
  | { kind: "month"; m: number; y: number | null }
  | { kind: "year"; y: number }
  | { kind: "fixed"; from: Date; to: Date; label: string };

const fullYear = (raw: string): number => (raw.length === 2 ? 2000 + Number(raw) : Number(raw));
const yearOk = (y: number) => y >= MIN_YEAR && y <= MAX_YEAR;
// Хвост года: «2025», «2025 г.», «2025 года», «2025 году».
const YEAR_TAIL = String.raw`(?:\s+(\d{4})(?:\s*г\.?|\s+года?|\s+году)?)?`;

function normalize(text: string): string {
  return text.toLowerCase().replace(/ё/g, "е").replace(/[–—]/g, "-").replace(/\s+/g, " ").trim()
    .replace(/[,;!?]+$/, "");
}

/** Одна дата (не диапазон) без привязки к году. */
function parseSpec(s: string, now: Date): Spec | null {
  const t = today(now);
  switch (s) {
    case "сегодня": return { kind: "fixed", from: t, to: t, label: "Сегодня" };
    case "вчера": { const d = addDays(t, -1); return { kind: "fixed", from: d, to: d, label: "Вчера" }; }
    case "позавчера": { const d = addDays(t, -2); return { kind: "fixed", from: d, to: d, label: "Позавчера" }; }
    default: break;
  }
  if (/^(?:на )?(?:эт(?:а|ой|у)|текущ(?:ая|ей|ую)) недел[яеию]$/.test(s)) {
    const monday = addDays(t, -((t.getDay() + 6) % 7));
    return { kind: "fixed", from: monday, to: addDays(monday, 6), label: "Эта неделя" };
  }
  if (/^(?:на )?прошл(?:ая|ой|ую) недел[яеию]$/.test(s)) {
    const monday = addDays(t, -((t.getDay() + 6) % 7) - 7);
    return { kind: "fixed", from: monday, to: addDays(monday, 6), label: "Прошлая неделя" };
  }
  if (/^(?:в )?(?:этот|этом|текущий|текущем) месяце?$/.test(s)) {
    const [y, m] = [t.getFullYear(), t.getMonth()];
    return { kind: "fixed", from: day(y, m, 1), to: lastOfMonth(y, m), label: "Этот месяц" };
  }
  if (/^(?:в )?(?:прошлый|прошлом) месяце?$/.test(s)) {
    const first = day(t.getFullYear(), t.getMonth() - 1, 1);
    return { kind: "fixed", from: first, to: lastOfMonth(first.getFullYear(), first.getMonth()), label: "Прошлый месяц" };
  }
  if (/^(?:в )?(?:этот|этом|текущий|текущем) году?$/.test(s)) {
    const y = t.getFullYear();
    return { kind: "fixed", from: day(y, 0, 1), to: day(y, 11, 31), label: "Этот год" };
  }
  if (/^(?:в )?(?:прошлый|прошлом) году?$/.test(s)) {
    const y = t.getFullYear() - 1;
    return { kind: "fixed", from: day(y, 0, 1), to: day(y, 11, 31), label: "Прошлый год" };
  }
  let m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s);
  if (m) return dayOrNull(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
  m = /^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?\.?$/.exec(s);
  if (m) return dayOrNull(Number(m[1]), Number(m[2]) - 1, m[3] ? fullYear(m[3]) : null);
  m = new RegExp(String.raw`^(\d{1,2})\s+([а-я]+\.?)${YEAR_TAIL}$`).exec(s);
  if (m) {
    const month = monthOf(m[2]!);
    return month < 0 ? null : dayOrNull(Number(m[1]), month, m[3] ? Number(m[3]) : null);
  }
  // Только «в»: «с сентября», «до сентября» — края, их разбирает вызывающий (libraryQuery).
  m = new RegExp(String.raw`^(?:в\s+)?([а-я]+\.?)${YEAR_TAIL}$`).exec(s);
  // «в 2024 году» сюда тоже подходит («в» — не месяц): тогда дальше, к году.
  if (m && monthOf(m[1]!) >= 0) {
    const y = m[2] ? Number(m[2]) : null;
    return y !== null && !yearOk(y) ? null : { kind: "month", m: monthOf(m[1]!), y };
  }
  m = /^(?:в\s+)?(\d{4})(?:\s*г\.?|\s+год[уа]?)?$/.exec(s);
  if (m) {
    const y = Number(m[1]);
    return yearOk(y) ? { kind: "year", y } : null;
  }
  return null;
}

function dayOrNull(d: number, m: number, y: number | null): Spec | null {
  if (m < 0 || m > 11 || d < 1 || d > 31) return null;
  if (y !== null) return yearOk(y) && valid(y, m, d) ? { kind: "day", d, m, y } : null;
  // 31.02, 31.04 — не бывает ни в каком году (2000 — високосный: 29.02 проходит).
  return valid(2000, m, d) ? { kind: "day", d, m, y: null } : null;
}

/**
 * Привязать к году. `notAfter` — последнее такое число/месяц не позже этого дня;
 * `notBefore` — первое не раньше (конец диапазона, у начала которого год есть).
 */
function resolve(spec: Spec, edge: { notAfter: Date } | { notBefore: Date }): { from: Date; to: Date } | null {
  switch (spec.kind) {
    case "fixed": return { from: spec.from, to: spec.to };
    case "year": return { from: day(spec.y, 0, 1), to: day(spec.y, 11, 31) };
    case "month": {
      let y = spec.y;
      if (y === null) {
        if ("notAfter" in edge) {
          const e = edge.notAfter;
          y = spec.m <= e.getMonth() ? e.getFullYear() : e.getFullYear() - 1;
        } else {
          const e = edge.notBefore;
          y = spec.m >= e.getMonth() ? e.getFullYear() : e.getFullYear() + 1;
        }
      }
      return { from: day(y, spec.m, 1), to: lastOfMonth(y, spec.m) };
    }
    case "day": {
      if (spec.y !== null) { const d = day(spec.y, spec.m, spec.d); return { from: d, to: d }; }
      const back = "notAfter" in edge;
      const e = back ? edge.notAfter : edge.notBefore;
      for (let i = 0; i <= LEAP_LOOKBACK; i++) {
        const y = e.getFullYear() + (back ? -i : i);
        if (!valid(y, spec.m, spec.d)) continue;
        const d = day(y, spec.m, spec.d);
        if (back ? d <= e : d >= e) return { from: d, to: d };
      }
      return null;
    }
  }
}

function specLabel(spec: Spec, r: { from: Date; to: Date }, now: Date): string {
  if (spec.kind === "fixed") return spec.label;
  if (spec.kind === "year") return String(spec.y);
  if (spec.kind === "month") return monthText(r.from.getFullYear(), r.from.getMonth(), now);
  return dayText(r.from, now);
}

const hasYear = (spec: Spec) => spec.kind === "fixed" || spec.kind === "year" || spec.y !== null;

/** Без года — в году `y` (29.02 в невисокосный — null). */
function resolveIn(spec: Spec, y: number): { from: Date; to: Date } | null {
  if (spec.kind === "month") return { from: day(y, spec.m, 1), to: lastOfMonth(y, spec.m) };
  if (spec.kind === "day" && spec.y === null) {
    return valid(y, spec.m, spec.d) ? { from: day(y, spec.m, spec.d), to: day(y, spec.m, spec.d) } : null;
  }
  return resolve(spec, { notAfter: day(y, 11, 31) });
}

const DAY_MS = 86_400_000;
const spanDays = (a: Date, b: Date) => Math.round((Date.UTC(b.getFullYear(), b.getMonth(), b.getDate())
  - Date.UTC(a.getFullYear(), a.getMonth(), a.getDate())) / DAY_MS);

/** Диапазон из двух дат («с X по Y», «X-Y», «X..Y»): не даты — null, даты, но конец раньше начала — "reversed". */
function parseRange(a: string, b: string, now: Date): DateRange | "reversed" | null {
  const left = parseSpec(a.trim(), now);
  const right = parseSpec(b.trim(), now);
  if (!left || !right) return null;
  const t = today(now);
  let start: { from: Date; to: Date } | null;
  let end: { from: Date; to: Date } | null;
  if (!hasYear(left) && hasYear(right)) {
    // Год только у конца — начало: последнее такое не позже конца.
    end = resolve(right, { notAfter: t });
    start = end && resolve(left, { notAfter: end.to });
  } else {
    start = resolve(left, { notAfter: t });
    if (!start) return null;
    const y = start.from.getFullYear();
    end = hasYear(right) ? resolve(right, { notAfter: t }) : resolveIn(right, y);
    if (end && end.to < start.from && !hasYear(right)) {
      // Через Новый год: месяц конца раньше месяца начала и не дольше полугода.
      const wrapped = right.kind === "day" || right.kind === "month"
        ? (right.m < start.from.getMonth() ? resolveIn(right, y + 1) : null) : null;
      end = wrapped && spanDays(start.from, wrapped.to) <= MAX_WRAP_DAYS ? wrapped : null;
      if (!end) return "reversed";
    }
  }
  if (!start || !end) return null;
  if (start.from > end.to) return "reversed";
  const from = ymd(start.from);
  const to = ymd(end.to);
  return { from, to, label: rangeLabel(from, to, now) };
}

/**
 * Число или месяц без года — ближайшее к сегодня (прошлый, этот или следующий год): `до:31.12`
 * 6 октября — этот год, 3 января — прошлый (иначе граница через год ничего бы не отсекала),
 * `до:5.01` 20 декабря — следующий.
 */
function nearest(spec: Spec, now: Date): { from: Date; to: Date } | null {
  const t = today(now);
  const away = (r: { from: Date; to: Date }) => (r.from <= t && t <= r.to ? 0
    : Math.min(Math.abs(spanDays(r.from, t)), Math.abs(spanDays(r.to, t))));
  let best: { from: Date; to: Date } | null = null;
  for (const y of [t.getFullYear() - 1, t.getFullYear(), t.getFullYear() + 1]) {
    const r = resolveIn(spec, y);
    if (r && (!best || away(r) < away(best))) best = r;
  }
  return best;
}

/** Разбор: диапазон, одна дата или null; "reversed" — диапазон с концом раньше начала. */
function parseAny(text: string, now: Date, thisYear: boolean): DateRange | "reversed" | null {
  const s = normalize(text);
  if (!s) return null;
  // Диапазон словами: «с 1.09 по 15.09», «с 5 окт до 10 окт».
  let m = /^с\s+(.+?)\s+(?:по|до)\s+(.+)$/.exec(s);
  if (m) return parseRange(m[1]!, m[2]!, now);
  m = /^(.+?)\s*\.\.\s*(.+)$/.exec(s);
  if (m) return parseRange(m[1]!, m[2]!, now);
  const single = parseSpec(s, now);
  if (single) {
    const r = thisYear && !hasYear(single) ? nearest(single, now) ?? resolve(single, { notAfter: today(now) })
      : resolve(single, { notAfter: today(now) });
    return r ? { from: ymd(r.from), to: ymd(r.to), label: specLabel(single, r, now) } : null;
  }
  // «1.09-15.09», «5 окт - 10 окт»: дефис — граница, если по обе стороны даты (у ISO дефис — внутри).
  let reversed = false;
  for (const at of [...s.matchAll(/-/g)].map((x) => x.index)) {
    const range = parseRange(s.slice(0, at), s.slice(at + 1), now);
    if (range === "reversed") reversed = true;
    else if (range) return range;
  }
  return reversed ? "reversed" : null;
}

/**
 * Текст → диапазон дней и подпись; не дата — null. `now` — «сегодня» (местное время).
 * `thisYear` — число и месяц без года — ближайшие к сегодня (может быть и будущее), а не последние
 * прошедшие (`до:`).
 */
export function parseDateExpr(text: string, now: Date, opts: { thisYear?: boolean } = {}): DateRange | null {
  const got = parseAny(text, now, opts.thisYear ?? false);
  return got === "reversed" ? null : got;
}

/** Почему текст — не дата, если понятно почему: «Конец периода раньше начала…». */
export function dateHint(text: string, now: Date): string | null {
  return parseAny(text, now, false) === "reversed"
    ? "Конец периода раньше начала — поменяйте местами или укажите год" : null;
}

// Сразу меткой — только число и месяц по две цифры («05.10») или с годом: «1.5», «3.11», «5.10»
// чаще версии и доли, чем даты — они лишь подсказка.
const NUMERIC_DAY = String.raw`(?:\d{2}\.\d{2}|\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})|\d{4}-\d{1,2}-\d{1,2})`;
const NUMERIC_RE = new RegExp(String.raw`^${NUMERIC_DAY}(?:\s*(?:-|–|—|\.\.)\s*${NUMERIC_DAY})?$`);

/**
 * Весь текст — однозначная числовая дата («05.10», «5.10.26», «2026-10-05», «01.09-15.09»): без
 * префикса она сразу становится меткой. «5.10», «3.11», год «2025» и слова («вчера», «5 окт») —
 * только подсказка.
 */
export function isNumericDate(text: string, now: Date): boolean {
  const s = text.trim();
  return NUMERIC_RE.test(s) && parseDateExpr(s, now) !== null;
}

/** Готовые варианты периода (подсказки `дата:`, «Период» в «Фильтрах»). */
export const DATE_PRESETS = ["сегодня", "вчера", "эта неделя", "прошлая неделя", "этот месяц", "прошлый месяц"];
