/**
 * Язык строки поиска по записям: префиксы → метки → фильтр библиотеки (`LibraryFilter`).
 *
 * Резиденту уходят структурные параметры (meet.library_filter) и текст `q`: слова,
 * «фразы» и `спикер:` (их правила — lib/search.ts). Префиксы:
 *
 * | префикс | метка | параметр |
 * |---|---|---|
 * | `название:бюджет` | «В названии: бюджет» | `title=` (только эти слова — в названии; прочие — как обычно) |
 * | `спикер:Анна` | — (остаётся в тексте) | `q` |
 * | `участник:Анна`, `участник:"Анна П"` | «Анна» | `people=` (нужны все) |
 * | `группа:Альфа`, `группа:без` | «Альфа» | `groups=` (любая из; `_none` — «Без группы») |
 * | `категория:дейлик` | «Дейлик» | `categories=` (любая из; `_none`) |
 * | `дата:вчера`, `после:5.10`, `до:сентябрь` | «Вчера», «с 5 октября», «по 30 сентября» | `from`/`to` (lib/dateExpr) |
 * | `есть:итоги`, `нет:анализ` | «Есть итоги», «Нет анализа» | `has`/`lacks` |
 * | `дольше:30м`, `короче:1ч` | «Дольше 30 мин» | `min_s`/`max_s` |
 *
 * Значение со пробелом — в кавычках (`участник:"Анна Петрова"`); у дат кавычки не нужны
 * (`дата:5 окт`, `дата:с 1.09 по 15.09` — берётся самый длинный разбираемый кусок).
 * Группа и категория узнаются по имени (без регистра и «ё»), его однозначному началу или id.
 *
 * Строка поиска — поле с текстом и строка меток над списком. Пока префикс в тексте, он
 * уже действует (`parseText`); Enter или выбор подсказки переносит его в метку (`commit`).
 * Вся строка — однозначная числовая дата («05.10») — тоже метка (подсказка предложит
 * «Искать «05.10» как текст»); словесная дата без префикса — только подсказка.
 */

import { DATE_PRESETS, dateHint, isNumericDate, parseDateExpr, parseYmd, rangeLabel } from "./dateExpr";
import { NO_CATEGORY, NO_CATEGORY_NAME } from "./categories";
import { NO_GROUP, NO_GROUP_NAME } from "./groups";
import { matchTokens, normWord, nfc, parseQuery, searchable, tokenize } from "./search";
import type { Category, LibraryFilter, LibraryHas } from "./types";

export type ChipKind = "title" | "person" | "group" | "category" | "date" | "has" | "lacks" | "longer" | "shorter";

/**
 * Метка условия. `value`: title — слова для названий; person — имя; group/category — id или
 * `_none`; date — «ГГГГ-ММ-ДД..ГГГГ-ММ-ДД» (край может быть пустым: «после», «до»); has/lacks —
 * LibraryHas; longer/shorter — секунды. `label` — подпись даты (какой её написал человек).
 * `expr` — у относительной даты («дата:сегодня», «после:вчера»): после полуночи метка
 * пересчитывается (`refreshDates`), подпись «Сегодня» не врёт.
 */
export type Chip = { kind: ChipKind; value: string; label?: string; expr?: string };

/** «Без группы» в фильтре (как NO_CATEGORY у категорий): один источник — lib/groups. */
export { NO_GROUP, NO_GROUP_NAME };
/** Группа, которой нет и не будет (id групп — «g-» и 8 знаков): пересечение области и метки пусто. */
export const NO_GROUP_MATCH = "0-no-match";

export type GroupRef = { id: string; name: string; color?: string };
/** Что нужно разбору: категории и группы — узнать по имени, `now` — «сегодня» для дат. */
export type QueryContext = { categories: Category[]; groups: GroupRef[]; now: Date };

type PrefixKind = ChipKind | "speaker" | "after" | "before";
export type Prefix = { word: string; kind: PrefixKind; hint: string };

/** Префиксы — в порядке подсказок. */
export const PREFIXES: Prefix[] = [
  { word: "участник", kind: "person", hint: "встречи с этим человеком" },
  { word: "спикер", kind: "speaker", hint: "что сказал этот человек" },
  { word: "группа", kind: "group", hint: "встречи из группы" },
  { word: "категория", kind: "category", hint: "встречи этой категории" },
  { word: "дата", kind: "date", hint: "вчера, 5 окт, сентябрь, с 1.09 по 15.09" },
  { word: "после", kind: "after", hint: "с этого дня" },
  { word: "до", kind: "before", hint: "по этот день" },
  { word: "есть", kind: "has", hint: "итоги, анализ, ассистент, расшифровка" },
  { word: "нет", kind: "lacks", hint: "итоги, анализ, ассистент, расшифровка" },
  { word: "дольше", kind: "longer", hint: "30м, 1ч" },
  { word: "короче", kind: "shorter", hint: "15м, 1ч" },
  { word: "название", kind: "title", hint: "искать только в названиях" },
];
const PREFIX_OF = new Map(PREFIXES.map((p) => [p.word, p]));

/** Что бывает у встречи: слово запроса, подписи «есть …» / «нет …». */
export const HAS_WORDS: { value: LibraryHas; word: string; has: string; lacks: string }[] = [
  { value: "summary", word: "итоги", has: "Есть итоги", lacks: "Нет итогов" },
  { value: "analysis", word: "анализ", has: "Есть анализ", lacks: "Нет анализа" },
  { value: "assistant", word: "ассистент", has: "Был ассистент", lacks: "Без ассистента" },
  { value: "transcript", word: "расшифровка", has: "Есть расшифровка", lacks: "Нет расшифровки" },
];

const QUOTES: Record<string, string> = { "\"": "\"", "«": "»", "“": "”" };
const norm = (s: string) => normWord(nfc(s)).replace(/\s+/g, " ").trim();
const chipKey = (c: Chip) => `${c.kind}\u0000${c.value}`;
export const sameChip = (a: Chip, b: Chip) => chipKey(a) === chipKey(b);

// --- разбор текста ------------------------------------------------------------------

/** Кусок строки: `[префикс:]значение`, значение — слово или «фраза в кавычках». */
export type Tok = {
  start: number; end: number; raw: string;
  /** Префикс языка (нижний регистр) или null — обычное слово. */
  prefix: string | null;
  value: string;
  quoted: boolean;
};

/** Строка → куски по пробелам; кавычки держат пробелы внутри (и незакрытая — до конца). */
export function scan(text: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < text.length) {
    if (/\s/.test(text[i]!)) { i++; continue; }
    const start = i;
    let prefix: string | null = null;
    const m = /^([\p{L}]+):/u.exec(text.slice(i));
    if (m && PREFIX_OF.has(m[1]!.toLowerCase())) {
      prefix = m[1]!.toLowerCase();
      i += m[0].length;
    }
    const close = QUOTES[text[i] ?? ""];
    let value: string;
    let quoted = false;
    if (close) {
      quoted = true;
      const j = text.indexOf(close, i + 1);
      value = text.slice(i + 1, j < 0 ? text.length : j);
      i = j < 0 ? text.length : j + 1;
    } else {
      let j = i;
      while (j < text.length && !/\s/.test(text[j]!)) j++;
      value = text.slice(i, j);
      i = j;
    }
    out.push({ start, end: i, raw: text.slice(start, i), prefix, value, quoted });
  }
  return out;
}

/** Длительность: «30», «30м», «30 мин», «1ч», «1.5ч», «1ч30м», «90с» (без единиц — минуты); не она — null. */
export function parseDuration(text: string): number | null {
  const s = norm(text).replace(/,/g, ".").replace(/\s+/g, "");
  if (!s) return null;
  if (/^\d+(?:\.\d+)?$/.test(s)) return Math.round(Number(s) * 60);
  // Длинные единицы — первыми: иначе «мин» прочлась бы как «м» и «ин».
  const re = /(\d+(?:\.\d+)?)(часов|часа|час|ч|h|минуты|минута|минут|мин|min|м|m|секунд|сек|с|s)/gy;
  let total = 0;
  let at = 0;
  for (const m of s.matchAll(re)) {
    if (m.index !== at) return null;
    at = m.index + m[0].length;
    const n = Number(m[1]);
    const unit = m[2]!;
    total += /^(ч|час|h)/.test(unit) ? n * 3600 : /^(с|s)/.test(unit) ? n : n * 60;
  }
  return at === s.length && at > 0 ? Math.round(total) : null;
}

/** «30 мин», «1 ч», «1 ч 30 мин», «45 с». */
export function durationText(seconds: number): string {
  if (seconds < 60) return `${seconds} с`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return [h ? `${h} ч` : "", m ? `${m} мин` : ""].filter(Boolean).join(" ");
}

/** Что бывает у встречи по слову («итог», «анализа», «ассистент»); не узнать — null. */
export function hasOf(word: string): LibraryHas | null {
  const w = norm(word);
  if (w.length < 2) return null;
  const found = HAS_WORDS.filter((h) => h.word.startsWith(w) || w.startsWith(h.word.slice(0, 4)));
  return found.length === 1 ? found[0]!.value : null;
}

/** Варианты группы или категории для узнавания по имени: с «Без …» первым. */
export function namedOptions(kind: "group" | "category", ctx: QueryContext): { id: string; name: string; color?: string | null }[] {
  return kind === "group"
    ? [{ id: NO_GROUP, name: NO_GROUP_NAME, color: null }, ...ctx.groups]
    : [{ id: NO_CATEGORY, name: NO_CATEGORY_NAME, color: null }, ...ctx.categories];
}

/** Подходит ли имя к началу набранного: всё имя или любое слово в нём начинается с него. */
export function nameMatches(name: string, typed: string): boolean {
  const t = norm(typed);
  if (!t) return true;
  const n = norm(name);
  return n.startsWith(t) || n.split(" ").some((w) => w.startsWith(t));
}

/** id группы или категории по набранному: имя целиком, id, единственное подходящее начало. */
export function resolveNamed(kind: "group" | "category", typed: string, ctx: QueryContext): string | null {
  const t = norm(typed);
  if (!t) return null;
  const options = namedOptions(kind, ctx);
  const exact = options.find((o) => norm(o.name) === t) ?? options.find((o) => o.id === typed.trim());
  if (exact) return exact.id;
  const found = options.filter((o) => nameMatches(o.name, t));
  return found.length === 1 ? found[0]!.id : null;
}

/** Относительная дата: её диапазон меняется со днём («сегодня», «эта неделя», «прошлый месяц»). */
const RELATIVE = /^(?:(?:с|после|до|по|на|в)\s+)?(?:сегодня|вчера|позавчера|(?:эт|текущ|прошл)\S*\s+(?:недел|месяц|год)\S*)$/;

/**
 * Метка даты: `дата:` — весь диапазон (а «с сентября», «до 5 окт» — край), `после:` — с его
 * начала, `до:` — по его конец (число без года — этого года, даже будущее).
 */
function dateChip(kind: "date" | "after" | "before", text: string, now: Date): Chip | null {
  const word = kind === "date" ? "дата" : kind === "after" ? "после" : "до";
  const expr = RELATIVE.test(norm(text)) ? { expr: `${word}:${norm(text)}` } : {};
  if (kind === "date") {
    const r = parseDateExpr(text, now);
    if (r) return { kind: "date", value: `${r.from}..${r.to}`, label: r.label, ...expr };
    // «с сентября», «после 5 окт» — нижний край; «до сентября», «по 5 окт» — верхний.
    const edge = /^\s*(с|после|от|до|по)\s+(.+)$/i.exec(text);
    if (!edge) return null;
    const chip = dateChip(/^(с|после|от)$/i.test(edge[1]!) ? "after" : "before", edge[2]!, now);
    return chip && { ...chip, ...expr };
  }
  const r = parseDateExpr(text, now, { thisYear: kind === "before" });
  if (!r) return null;
  // Край диапазона — днём: «с 1 сентября», а не «с Сентябрь».
  if (kind === "after") return { kind: "date", value: `${r.from}..`, label: `с ${rangeLabel(r.from, r.from, now)}`, ...expr };
  return { kind: "date", value: `..${r.to}`, label: `по ${rangeLabel(r.to, r.to, now)}`, ...expr };
}

/** Подпись дат по краям: «1 сен – 15 сен», «с 1 сентября», «по 15 сентября». */
function edgesLabel(from: string | undefined, to: string | undefined, now: Date): string {
  if (from && to) {
    return from <= to ? rangeLabel(from, to, now)
      : `${rangeLabel(from, from, now)} – ${rangeLabel(to, to, now)} (нет общих дней)`;
  }
  return from ? `с ${rangeLabel(from, from, now)}` : to ? `по ${rangeLabel(to, to, now)}` : "";
}

/**
 * Пересечение дат в одну метку: `после:1.09 до:15.09` — «1 сен – 15 сен». Фильтр не
 * расширяется: что действовало до Enter (пересечение), то и остаётся.
 */
export function intersectDates(dates: Chip[], now: Date): Chip | null {
  if (!dates.length) return null;
  if (dates.length === 1) return dates[0]!;
  let from: string | undefined;
  let to: string | undefined;
  for (const c of dates) {
    const e = dateEdges(c);
    if (e.from && (!from || e.from > from)) from = e.from;
    if (e.to && (!to || e.to < to)) to = e.to;
  }
  const value = `${from ?? ""}..${to ?? ""}`;
  const same = dates.find((c) => c.value === value);
  return same ?? { kind: "date", value, label: edgesLabel(from, to, now) };
}

/** После полуночи: относительные даты («сегодня», «эта неделя») — заново, с подписью. */
export function refreshDates(chips: Chip[], now: Date): Chip[] {
  let changed = false;
  const next = chips.map((c) => {
    if (c.kind !== "date" || !c.expr) return c;
    const at = c.expr.indexOf(":");
    const prefix = c.expr.slice(0, at);
    const fresh = dateChip(prefix === "после" ? "after" : prefix === "до" ? "before" : "date", c.expr.slice(at + 1), now);
    if (!fresh || (fresh.value === c.value && fresh.label === c.label)) return c;
    changed = true;
    return { ...fresh, label: c.label && !RELATIVE_LABEL.test(c.label) ? c.label : fresh.label };
  });
  return changed ? next : chips;
}
/** Подписи, которые сами относительные (их и пересчитываем); прочие — подпись человека/раздела. */
const RELATIVE_LABEL = /^(Сегодня|Вчера|Позавчера|Эта неделя|Прошлая неделя|Этот месяц|Прошлый месяц|Этот год|Прошлый год|с |по )/;

/** Значение префикса → метка; не годится (неизвестная группа, не дата) — null. */
export function chipOf(prefix: string, value: string, ctx: QueryContext): Chip | null {
  const p = PREFIX_OF.get(prefix);
  if (!p) return null;
  const v = value.trim();
  switch (p.kind) {
    case "title": return v ? { kind: "title", value: v } : null;
    case "person": return v ? { kind: "person", value: v } : null;
    case "group":
    case "category": {
      const id = resolveNamed(p.kind, v, ctx);
      return id ? { kind: p.kind, value: id } : null;
    }
    case "date":
    case "after":
    case "before": return dateChip(p.kind, v, ctx.now);
    case "has":
    case "lacks": {
      const what = hasOf(v);
      return what ? { kind: p.kind, value: what } : null;
    }
    case "longer":
    case "shorter": {
      const s = parseDuration(v);
      return s !== null && s > 0 ? { kind: p.kind, value: String(s) } : null;
    }
    default: return null;
  }
}

const DATE_PREFIXES = new Set(["дата", "после", "до"]);
/** Сколько слов после `дата:` пробовать в одну дату («с 1 сентября 2025 по 15 сентября 2025» — 7). */
const DATE_WORDS = 8;

/** Префикс в тексте, который ещё не стал меткой. */
export type PrefixTok = {
  tok: Tok;
  /** Номер куска префикса среди `scan(text)`. */
  first: number;
  /** Последний кусок, вошедший в значение (у дат — несколько слов). */
  last: number;
  prefix: string;
  value: string;
  /** Что из него вышло; null — значение пока не годится (дописывается или опечатка). */
  chip: Chip | null;
};

export type ParsedText = {
  /** Метки из префиксов текста (и числовой даты без префикса). */
  chips: Chip[];
  /** Текст без префиксов: слова, «фразы», `спикер:` — в поиск. */
  rest: string;
  /** Все префиксы текста по порядку — годные и нет. */
  prefixes: PrefixTok[];
  /** Вся строка — числовая дата без префикса: она и стала меткой. */
  numericDate: boolean;
};

/** Разобрать текст строки поиска; `спикер:` остаётся текстом. */
export function parseText(text: string, ctx: QueryContext): ParsedText {
  if (isNumericDate(text, ctx.now)) {
    const chip = dateChip("date", text, ctx.now)!;
    return { chips: [chip], rest: "", prefixes: [], numericDate: true };
  }
  const toks = scan(text);
  const chips: Chip[] = [];
  const prefixes: PrefixTok[] = [];
  const rest: string[] = [];
  for (let i = 0; i < toks.length; i++) {
    const tok = toks[i]!;
    if (!tok.prefix || tok.prefix === "спикер") { rest.push(tok.raw); continue; }
    let last = i;
    let value = tok.value;
    let chip = chipOf(tok.prefix, value, ctx);
    if (DATE_PREFIXES.has(tok.prefix) && !tok.quoted) {
      // Самый длинный кусок из следующих слов, который читается как дата: «дата:5 окт 2025».
      let joined = tok.value;
      for (let j = i + 1; j < toks.length && j <= i + DATE_WORDS; j++) {
        const next = toks[j]!;
        if (next.prefix || next.quoted) break;
        joined = `${joined} ${next.raw}`;
        const longer = chipOf(tok.prefix, joined, ctx);
        if (longer) { chip = longer; last = j; value = joined; continue; }
        // «дата:с 15.09 по 1.09» — диапазон задом наперёд: негоден целиком, а не «с 15.09» и текст «по 1.09».
        if (dateHint(joined, ctx.now)) { chip = null; last = j; value = joined; break; }
      }
    }
    prefixes.push({ tok, first: i, last, prefix: tok.prefix, value, chip });
    if (chip) chips.push(chip);
    i = last;
  }
  return { chips, rest: rest.join(" "), prefixes, numericDate: false };
}

/** Только текстовая часть строки (слова, «фразы», `спикер:`): её — в поиск по карточке. */
export function textOf(text: string, now: Date = new Date()): string {
  return parseText(text, { categories: [], groups: [], now }).rest;
}

/**
 * Enter в строке поиска: годные префиксы текста (и числовая дата) — в метки, в поле остаётся
 * текст и негодные префиксы (их видно — можно исправить). Категории — отдельно: они запоминаются.
 */
export function commit(text: string, chips: Chip[], categories: string[], ctx: QueryContext):
  { text: string; chips: Chip[]; categories: string[] } {
  const parsed = parseText(text, ctx);
  if (!parsed.chips.length) return { text, chips, categories };
  const keep: string[] = [];
  if (!parsed.numericDate) {
    const toks = scan(text);
    const used = new Set<number>();
    for (const p of parsed.prefixes) {
      if (!p.chip) continue;
      for (let k = p.first; k <= p.last; k++) used.add(k);
    }
    toks.forEach((t, k) => { if (!used.has(k)) keep.push(t.raw); });
  }
  let nextChips = chips;
  let nextCats = categories;
  for (const c of parsed.chips) {
    if (c.kind === "category") nextCats = nextCats.includes(c.value) ? nextCats : [...nextCats, c.value];
    else if (c.kind !== "date" && c.kind !== "longer" && c.kind !== "shorter") nextChips = addChip(nextChips, c);
  }
  // Даты и длительность — как действовали до Enter: пересечение и строжайшая граница, не шире.
  if (parsed.chips.some((c) => c.kind === "date" || c.kind === "longer" || c.kind === "shorter")) {
    const all = [...chips, ...parsed.chips];
    const merged = intersectDates(all.filter((c) => c.kind === "date"), ctx.now);
    const strict = (kind: "longer" | "shorter"): Chip[] => {
      const list = all.filter((c) => c.kind === kind);
      if (!list.length) return [];
      const pick = (kind === "longer" ? Math.max : Math.min)(...list.map((c) => Number(c.value)));
      return [list.find((c) => Number(c.value) === pick)!];
    };
    nextChips = [...nextChips.filter((c) => c.kind !== "date" && c.kind !== "longer" && c.kind !== "shorter"),
      ...(merged ? [merged] : []), ...strict("longer"), ...strict("shorter")];
  }
  return { text: keep.length ? `${keep.join(" ")} ` : "", chips: nextChips, categories: nextCats };
}

// --- метки → фильтр -----------------------------------------------------------------

/**
 * Добавить метку: дата одна (новая заменяет; но «после» к «до» — и наоборот — дополняет: один
 * диапазон), длительность — одна в каждую сторону, повторы не нужны.
 */
export function addChip(chips: Chip[], added: Chip, now: Date = new Date()): Chip[] {
  let chip = added;
  const old = chip.kind === "date" ? chips.find((c) => c.kind === "date") : undefined;
  if (old) {
    const a = dateEdges(old);
    const b = dateEdges(chip);
    const oneSided = (e: { from?: string; to?: string }) => !e.from !== !e.to;
    if (oneSided(a) && oneSided(b) && !a.from !== !b.from) chip = intersectDates([old, chip], now)!;
  }
  const single = chip.kind === "date" || chip.kind === "longer" || chip.kind === "shorter";
  const rest = chips.filter((c) => !sameChip(c, chip) && !(single && c.kind === chip.kind)
    // «Есть итоги» и «Нет итогов» вместе не бывают: новое заменяет.
    && !((chip.kind === "has" || chip.kind === "lacks") && (c.kind === "has" || c.kind === "lacks") && c.value === chip.value));
  return [...rest, chip];
}
export const removeChip = (chips: Chip[], chip: Chip) => chips.filter((c) => !sameChip(c, chip));
export const hasChip = (chips: Chip[], chip: Chip) => chips.some((c) => sameChip(c, chip));
export const toggleChip = (chips: Chip[], chip: Chip) => (hasChip(chips, chip) ? removeChip(chips, chip) : addChip(chips, chip));

/** Края даты метки: «ГГГГ-ММ-ДД..ГГГГ-ММ-ДД», край может быть пуст. */
export function dateEdges(chip: Chip): { from?: string; to?: string } {
  const [from = "", to = ""] = chip.value.split("..");
  return { ...(parseYmd(from) ? { from } : {}), ...(parseYmd(to) ? { to } : {}) };
}

/** Метки → фильтр резидента: категории и группы — любая из, участники — все, даты — пересечение. */
export function toFilter(chips: Chip[]): LibraryFilter {
  const f: LibraryFilter = {};
  const push = <K extends "categories" | "groups" | "people" | "has" | "lacks">(key: K, value: string) => {
    const list = (f[key] ?? []) as string[];
    if (!list.includes(value)) (f as Record<string, string[]>)[key] = [...list, value];
  };
  for (const c of chips) {
    switch (c.kind) {
      case "category": push("categories", c.value); break;
      case "group": push("groups", c.value); break;
      case "person": push("people", c.value); break;
      case "has": push("has", c.value); break;
      case "lacks": push("lacks", c.value); break;
      case "title": f.title = f.title ? `${f.title} ${c.value}` : c.value; break;
      case "longer": f.min_s = Math.max(f.min_s ?? 0, Number(c.value)); break;
      case "shorter": f.max_s = Math.min(f.max_s ?? Infinity, Number(c.value)); break;
      case "date": {
        const { from, to } = dateEdges(c);
        if (from && (!f.from || from > f.from)) f.from = from;
        if (to && (!f.to || to < f.to)) f.to = to;
        break;
      }
    }
  }
  return f;
}

/**
 * Область группы из левой панели (запоминается отдельно) и метки `группа:` не спорят: метка
 * сужает внутри области. Область — одна группа (или «Без группы»): метки других групп дают
 * пустое пересечение — встреч нет (у встречи одна группа). Нет области — фильтр как есть.
 */
export function withGroupScope(filter: LibraryFilter, scope: string | null | undefined): LibraryFilter {
  if (!scope) return filter;
  const groups = filter.groups?.length ? filter.groups.filter((g) => g === scope) : [scope];
  return { ...filter, groups: groups.length ? groups : [NO_GROUP_MATCH] };
}

export type EffectiveQuery = {
  /** Текст для резидента: слова, фразы, `спикер:` (слова `название:` — в `filter.title`). */
  q: string;
  filter: LibraryFilter;
  /** Только текстовая часть — в поиск по карточке. */
  find: string;
  /** Все метки по порядку: запомненные категории, метки сеанса, префиксы из текста. */
  chips: Chip[];
  parsed: ParsedText;
  /**
   * Идёт поиск или действуют условия сеанса (не только запомненные категории): разделы по датам
   * развёрнуты, над списком «Найдено…».
   */
  active: boolean;
};

/** Что ищется сейчас: метки сеанса, запомненные категории и ещё не ставший метками текст. */
export function effectiveQuery(text: string, chips: Chip[], categories: string[], ctx: QueryContext): EffectiveQuery {
  const parsed = parseText(text, ctx);
  const all: Chip[] = [];
  for (const c of [...categories.map((value): Chip => ({ kind: "category", value })), ...chips, ...parsed.chips]) {
    if (!hasChip(all, c)) all.push(c);
  }
  // Слова `название:` — отдельным условием (`title=`), прочие ищутся как обычно.
  const q = parsed.rest.trim();
  const active = searchable(q) || chips.length > 0 || parsed.chips.length > 0;
  return { q, filter: toFilter(all), find: parsed.rest.trim(), chips: all, parsed, active };
}

// --- подписи --------------------------------------------------------------------------

/** Подпись метки для строки меток и диктора. */
export function chipText(chip: Chip, ctx: Pick<QueryContext, "categories" | "groups">): string {
  switch (chip.kind) {
    case "category":
      return chip.value === NO_CATEGORY ? NO_CATEGORY_NAME
        : ctx.categories.find((c) => c.id === chip.value)?.name ?? chip.value;
    case "group":
      return chip.value === NO_GROUP ? NO_GROUP_NAME
        : ctx.groups.find((g) => g.id === chip.value)?.name ?? "Группа без названия";
    case "person": return chip.value;
    case "title": return `В названии: ${chip.value}`;
    case "date": return chip.label ?? chip.value;
    case "has": return HAS_WORDS.find((h) => h.value === chip.value)?.has ?? chip.value;
    case "lacks": return HAS_WORDS.find((h) => h.value === chip.value)?.lacks ?? chip.value;
    case "longer": return `Дольше ${durationText(Number(chip.value))}`;
    case "shorter": return `Короче ${durationText(Number(chip.value))}`;
  }
}

/** Цвет точки у метки группы или категории (нет — серая точка «Без …»). */
export function chipColor(chip: Chip, ctx: Pick<QueryContext, "categories" | "groups">): string | null | undefined {
  if (chip.kind === "category") return ctx.categories.find((c) => c.id === chip.value)?.color ?? null;
  if (chip.kind === "group") return ctx.groups.find((g) => g.id === chip.value)?.color ?? null;
  return undefined;
}

/** Метка как текст строки поиска: `группа:"Проект Альфа"` (подсказки, «Искать как текст»). */
export function quoteValue(value: string): string {
  return /[\s"«»“”]/.test(value) || value === "" ? `"${value.replace(/"/g, "")}"` : value;
}

/**
 * Что подсветить в названии по словам `название:` (`filter.title`), когда резидент подсветку не
 * прислал (список без текста поиска — `/recordings`): по тем же правилам, по названию в NFC.
 */
export function titleRanges(title: string, terms: string): [number, number][] {
  return matchTokens(tokenize(nfc(title)), { ...parseQuery(terms), speakers: [] }) ?? [];
}

/** Слова имён для подсветки участника в фрагменте: из меток `участник:` и `спикер:` в тексте. */
export function whoWords(q: string, filter: LibraryFilter): string[][] {
  const people = (filter.people ?? []).map((p) => tokenize(nfc(p)).map((t) => t.norm)).filter((w) => w.length);
  return [...people, ...parseQuery(q).speakers];
}

/** Подсветить в имени спикера слова, с которых начинаются слова участника. */
export function whoRanges(speaker: string, who: string[][]): [number, number][] {
  if (!who.length) return [];
  const toks = tokenize(nfc(speaker));
  const out: [number, number][] = [];
  for (const words of who) {
    const hit = toks.filter((t) => words.some((w) => t.norm.startsWith(w)));
    // Совпало всё имя участника («Анна П» — оба слова), иначе это не он.
    if (words.every((w) => toks.some((t) => t.norm.startsWith(w)))) for (const t of hit) out.push([t.start, t.end]);
  }
  return out.sort((a, b) => a[0] - b[0]).filter((r, i, all) => i === 0 || r[0] >= all[i - 1]![1]);
}

// --- подсказки ------------------------------------------------------------------------

export type Suggestion = {
  id: string;
  /** Что видно в списке и что прочтёт диктор. */
  label: string;
  /** Пояснение справа (мелко). */
  detail?: string;
  /** Цвет точки (группа, категория). */
  color?: string | null;
  kind: "search" | "prefix" | "person" | "group" | "category" | "date" | "has" | "duration" | "literal" | "hint";
  /** Пояснение, а не выбор («Не понял дату «абв»»): видно и диктору, но не выбирается. */
  disabled?: boolean;
  /** «commit» — то же, что Enter: искать текст, готовые префиксы — в метки. */
  action: { type: "commit" } | { type: "replace"; text: string; chip?: Chip };
};

/** Участник для подсказок (`GET /participants`). */
export type PersonOption = { name: string; meetings: number };

const DURATION_PRESETS = [15 * 60, 30 * 60, 60 * 60, 2 * 3600];
/** Сколько слов в конце текста пробовать как дату без префикса («бюджет 5 окт» → «5 окт»). */
const TAIL_DATE_WORDS = 4;

/** Разбирается ли кусок как дата — от «сегодня» не зависит. */
const NOW_ANY = new Date(2026, 0, 15);

/**
 * Конец текста, к которому относятся подсказки: последнее слово (или префикс со значением) и
 * текст до него. У `дата:` значение — все слова после префикса («дата:5 окт»), у незакрытой
 * кавычки — всё до конца (`участник:"Анна П`). После пробела слова нет — null.
 */
export function tail(text: string): { before: string; tok: Tok | null } {
  const toks = scan(text);
  const last = toks[toks.length - 1];
  if (!last) return { before: text, tok: null };
  let k = toks.length - 1;
  while (k >= 0 && toks[k]!.prefix === null) k--;
  const head = toks[k];
  if (head && DATE_PREFIXES.has(head.prefix!) && !head.quoted && toks.length - 1 - k <= DATE_WORDS
    && toks.slice(k + 1).every((t) => !t.prefix && !t.quoted)) {
    const value = text.slice(head.start + head.prefix!.length + 1);
    // Дата ещё набирается («дата:5 о») или уже вся («дата:5 окт»); «дата:вчера бюджет» — нет:
    // дата кончилась на «вчера», дальше — обычное слово.
    const shorter = toks.length - 1 > k ? text.slice(head.start + head.prefix!.length + 1, last.start) : "";
    const building = !shorter.trim() || parseDateExpr(value, NOW_ANY) !== null || parseDateExpr(shorter, NOW_ANY) === null;
    if (building) {
      return { before: text.slice(0, head.start), tok: { ...head, end: text.length, raw: text.slice(head.start), value } };
    }
  }
  const open = last.quoted && !/["»”]$/.test(last.raw.slice(last.prefix ? last.prefix.length + 2 : 1));
  if (/\s$/.test(text) && !open) return { before: text, tok: null };
  return { before: text.slice(0, last.start), tok: last };
}

const replaceTail = (before: string, insert: string) => `${before}${insert}`;

/**
 * Варианты для строки поиска (combobox): первый — «Искать «…» в тексте» (то же, что Enter;
 * у числовой даты — «Искать «05.10» как текст»), дальше — дописать префикс, участники, группы,
 * категории, толкования даты. `people` — ответ `/participants` на нынешнее слово.
 */
export function suggestions(text: string, ctx: QueryContext, people: PersonOption[] = []): Suggestion[] {
  const out: Suggestion[] = [];
  const parsed = parseText(text, ctx);
  const shown = text.trim();
  if (parsed.numericDate) {
    out.push({ id: "date-numeric", kind: "date", label: `Дата: ${parsed.chips[0]!.label}`, detail: "Enter",
      action: { type: "commit" } });
    out.push({ id: "literal", kind: "literal", label: `Искать «${shown}» как текст`,
      action: { type: "replace", text: `"${shown}"` } });
    return out;
  }
  if (shown) {
    const words = parsed.rest.trim();
    out.push({ id: "search", kind: "search", detail: "Enter",
      label: words ? `Искать «${words}» в тексте` : "Применить условия", action: { type: "commit" } });
  }
  const { before, tok } = tail(text);
  // Негодные префиксы раньше в строке — пояснением: почему условие не действует.
  for (const p of parsed.prefixes) {
    if (p.chip || (tok && p.tok.start === tok.start)) continue;
    const hint = prefixHint(p.prefix, p.value, ctx);
    if (hint) out.push({ id: `hint:${p.tok.start}`, kind: "hint", label: hint, disabled: true, action: { type: "commit" } });
  }
  if (!tok) return out;
  const set = (insert: string, chip?: Chip): Suggestion["action"] =>
    ({ type: "replace", text: replaceTail(before, insert), ...(chip ? { chip } : {}) });

  if (tok.prefix && tok.prefix !== "спикер") {
    const offered = out.length;
    const result = prefixSuggestions(tok, set, ctx, people, out);
    // Значений нет, а что-то набрано — почему: «Не понял дату «абв»», «Нет группы «x»».
    const hint = out.length === offered ? prefixHint(tok.prefix, tok.value, ctx) : null;
    if (hint) out.push({ id: "hint:tail", kind: "hint", label: hint, disabled: true, action: { type: "commit" } });
    return result;
  }
  return wordSuggestions(text, tok, before, set, ctx, people, out);
}

/** Почему значение префикса не стало меткой; пусто (ещё набирается) — null. */
export function prefixHint(prefix: string, value: string, ctx: QueryContext): string | null {
  const kind = PREFIX_OF.get(prefix)?.kind;
  const v = value.trim();
  if (!v) return null;
  switch (kind) {
    case "date":
    case "after":
    case "before":
      return dateHint(v, ctx.now) ?? `Не понял дату «${v}»`;
    case "group":
    case "category": {
      const n = namedOptions(kind, ctx).filter((o) => nameMatches(o.name, v)).length;
      if (n > 1) return `«${v}» — подходит несколько ${kind === "group" ? "групп" : "категорий"}, уточните`;
      return `Нет ${kind === "group" ? "группы" : "категории"} «${v}»`;
    }
    case "has":
    case "lacks":
      return `Не понял «${v}»: итоги, анализ, ассистент или расшифровка`;
    case "longer":
    case "shorter":
      return `Не понял длительность «${v}»: например 30м или 1ч`;
    default:
      return null;
  }
}

type SetAction = (insert: string, chip?: Chip) => Suggestion["action"];

/** Значения префикса под курсором (`группа:бе` — группы на «бе»). */
function prefixSuggestions(tok: Tok, set: SetAction, ctx: QueryContext, people: PersonOption[], out: Suggestion[]): Suggestion[] {
  {
    const prefix = PREFIX_OF.get(tok.prefix!)!;
    const v = tok.value;
    switch (prefix.kind) {
      case "person":
        for (const p of people) {
          out.push({ id: `person:${p.name}`, kind: "person", label: p.name, detail: meetingsText(p.meetings),
            action: set("", { kind: "person", value: p.name }) });
        }
        break;
      case "group":
      case "category":
        for (const o of namedOptions(prefix.kind, ctx).filter((x) => nameMatches(x.name, v))) {
          out.push({ id: `${prefix.kind}:${o.id}`, kind: prefix.kind, label: o.name, color: o.color ?? null,
            action: set("", { kind: prefix.kind, value: o.id }) });
        }
        break;
      case "date":
      case "after":
      case "before": {
        const seen = new Set<string>();
        const add = (expr: string) => {
          const chip = dateChip(prefix.kind as "date" | "after" | "before", expr, ctx.now);
          if (!chip || seen.has(chip.value)) return;
          seen.add(chip.value);
          out.push({ id: `date:${chip.value}`, kind: "date", label: chip.label!, detail: rangeHint(chip, ctx.now),
            action: set("", chip) });
        };
        const whole = v.trim();
        if (whole) add(whole);
        for (const p of DATE_PRESETS) if (!whole || p.startsWith(norm(whole))) add(p);
        break;
      }
      case "has":
      case "lacks":
        for (const h of HAS_WORDS.filter((x) => x.word.startsWith(norm(v)) || hasOf(v) === x.value)) {
          const chip: Chip = { kind: prefix.kind, value: h.value };
          out.push({ id: `${prefix.kind}:${h.value}`, kind: "has", label: chipText(chip, ctx), action: set("", chip) });
        }
        break;
      case "longer":
      case "shorter": {
        const typed = parseDuration(v);
        // Набрано, но не длительность — готовых вариантов нет: будет пояснение.
        const list = typed ? [typed] : v.trim() ? [] : DURATION_PRESETS;
        for (const s of list) {
          const chip: Chip = { kind: prefix.kind, value: String(s) };
          out.push({ id: `${prefix.kind}:${s}`, kind: "duration", label: chipText(chip, ctx), action: set("", chip) });
        }
        break;
      }
      default: break;
    }
    return out;
  }
}

/** Слово без префикса: дописать префикс, участник, группа, категория, дата. */
function wordSuggestions(text: string, tok: Tok, before: string, set: SetAction, ctx: QueryContext,
  people: PersonOption[], out: Suggestion[]): Suggestion[] {
  const w = norm(tok.value);
  if (!tok.prefix && !tok.quoted && w && !w.includes(":")) {
    for (const p of PREFIXES.filter((x) => x.word.startsWith(w) && x.word !== w)) {
      out.push({ id: `prefix:${p.word}`, kind: "prefix", label: `${p.word}:`, detail: p.hint,
        action: { type: "replace", text: replaceTail(before, `${p.word}:`) } });
    }
  }
  if (tok.prefix || w.length < 2) return out;
  for (const p of people) {
    out.push({ id: `person:${p.name}`, kind: "person", label: `Участник: ${p.name}`, detail: meetingsText(p.meetings),
      action: set("", { kind: "person", value: p.name }) });
  }
  for (const kind of ["group", "category"] as const) {
    for (const o of namedOptions(kind, ctx).slice(1).filter((x) => nameMatches(x.name, w))) {
      out.push({ id: `${kind}:${o.id}`, kind, label: `${kind === "group" ? "Группа" : "Категория"}: ${o.name}`,
        color: o.color ?? null, action: set("", { kind, value: o.id }) });
    }
  }
  // Словесная дата — только подсказка: «вчера», «сентябрь», «бюджет 5 окт».
  const toks = scan(text);
  for (let n = Math.min(TAIL_DATE_WORDS, toks.length); n >= 1; n--) {
    const span = toks.slice(toks.length - n);
    if (span.some((t) => t.prefix || t.quoted)) continue;
    const expr = text.slice(span[0]!.start);
    const chip = dateChip("date", expr, ctx.now);
    if (chip) {
      out.push({ id: `date:${chip.value}`, kind: "date", label: `Дата: ${chip.label}`, detail: rangeHint(chip, ctx.now),
        action: { type: "replace", text: text.slice(0, span[0]!.start), chip } });
      break;
    }
  }
  return out;
}

/** Дни метки словами, если подпись их не называет («Эта неделя» — «5 окт – 11 окт»). */
function rangeHint(chip: Chip, now: Date): string | undefined {
  const { from, to } = dateEdges(chip);
  if (!from || !to) return undefined;
  const days = rangeLabel(from, to, now);
  return days === chip.label ? undefined : days;
}

const meetingsText = (n: number) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? "встреча"
  : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "встречи" : "встреч"}`;

/** Слово под курсором просит участников: `участник:Ан` — «Ан», просто «Ан» — тоже (от двух букв). */
export function personQuery(text: string): string | null {
  const { tok } = tail(text);
  if (!tok) return null;
  if (tok.prefix === "участник") return tok.value;
  if (tok.prefix || tok.quoted) return null;
  const w = tok.value.trim();
  return w.length >= 2 && /^\p{L}/u.test(w) ? w : null;
}
