/**
 * Поиск по тексту встречи: разбор запроса, совпадения и что подсветить.
 *
 * Те же правила — в резиденте (`src/meet/search.py`, поиск по всем записям);
 * общие случаи лежат в `tests/fixtures/search_cases.json`, и их проверяют оба
 * набора тестов, чтобы правила не разошлись.
 *
 * * Текст и запрос сначала приводятся к NFC («й» из «и» + знак — одна буква);
 *   подсветка — по приведённому тексту (`Prepared.text`), его и показываем.
 * * Сравнение — по словам: слово — непрерывная цепочка букв и цифр, остальное
 *   (знаки препинания, дефис, пробелы) только разделяет. Регистр не важен,
 *   «ё» равна «е». Подсветка — по исходному тексту, с его регистром и знаками.
 * * «Фраза в кавычках» ("…", «…», “…”) — слова подряд, точно (без окончаний);
 *   знаки препинания между ними не мешают.
 * * Слова без кавычек — ключевые: реплика подходит, если в ней есть все.
 *   Ключевое слово совпадает со словом текста, если то начинается с его основы:
 *   у слов от 5 букв отрезается одно частое окончание (`ENDINGS`, сначала
 *   длинные), но так, чтобы основа осталась не короче 4 букв; короткие слова —
 *   просто начало слова («код» → «кодировка», но не «перекодировка»).
 * * `спикер:Анна` или `спикер:"Анна П"` — только реплики этого спикера (каждое
 *   слово — начало слова в имени). Несколько — любой из них. Без других слов
 *   подходят все его реплики, подсвечивать нечего.
 */

export type Range = [number, number];
export type Token = { norm: string; start: number; end: number };

export type Query = {
  /** Фразы — нормализованные слова подряд. */
  phrases: string[][];
  /** Ключевые слова (нормализованные, без повторов). */
  keywords: string[];
  /** Их основы — в том же порядке. */
  stems: string[];
  /** Фильтры по спикеру: нормализованные слова каждого. */
  speakers: string[][];
};

/** Окончания для «лёгкой основы»: длинные — первыми. */
export const ENDINGS = [
  "ться",
  "ами", "ями",
  "ой", "ей", "ий", "ый", "ая", "яя", "ое", "ее", "ом", "ем", "ам", "ям", "ах", "ях",
  "ов", "ев", "ия", "ие", "ию", "ии", "ть", "ет", "ут", "ют", "ит", "ат", "ят",
  "а", "я", "ы", "и", "у", "ю", "е", "о",
];
/** Поиск по записям — от двух символов: одна буква находит почти всё (MIN_QUERY в meet/search.py). */
export const MIN_QUERY = 2;
export const searchable = (q: string): boolean => nfc(q).trim().length >= MIN_QUERY;

const MIN_STEMMED = 5;
const MIN_STEM = 4;

const WORD_RE = /[\p{L}\p{N}]+/gu;
// Части запроса: [спикер:]"фраза" | спикер:значение | любой кусок без пробелов.
const QUERY_RE = /(спикер:)?(?:"([^"]*)"?|«([^»]*)»?|“([^”]*)”?)|спикер:(\S*)|(\S+)/giu;

export const normWord = (w: string): string => w.toLowerCase().replace(/ё/g, "е");
/** Текст к NFC: составные буквы — одной, как у резидента (`unicodedata.normalize`). */
export const nfc = (text: string): string => text.normalize("NFC");

/** Слова текста с их местом в исходной строке. */
export function tokenize(text: string): Token[] {
  const out: Token[] = [];
  for (const m of text.matchAll(WORD_RE)) {
    out.push({ norm: normWord(m[0]), start: m.index, end: m.index + m[0].length });
  }
  return out;
}

const words = (text: string): string[] => tokenize(text).map((t) => t.norm);

export function stem(word: string): string {
  if (word.length < MIN_STEMMED) return word;
  for (const ending of ENDINGS) {
    if (word.endsWith(ending) && word.length - ending.length >= MIN_STEM) return word.slice(0, -ending.length);
  }
  return word;
}

export function parseQuery(q: string): Query {
  const phrases: string[][] = [];
  const keywords: string[] = [];
  const speakers: string[][] = [];
  for (const m of nfc(q).matchAll(QUERY_RE)) {
    const quoted = m[2] ?? m[3] ?? m[4];
    if (quoted !== undefined) {
      const ws = words(quoted);
      if (ws.length) (m[1] ? speakers : phrases).push(ws);
    } else if (m[5] !== undefined) {
      const ws = words(m[5]);
      if (ws.length) speakers.push(ws);
    } else {
      for (const w of words(m[6] ?? "")) if (!keywords.includes(w)) keywords.push(w);
    }
  }
  return { phrases, keywords, stems: keywords.map(stem), speakers };
}

/** Искать нечего: ни слов, ни фраз, ни спикера. */
export const isEmptyQuery = (q: Query): boolean => !q.phrases.length && !q.keywords.length && !q.speakers.length;

/** Подходит ли спикер: любой из фильтров, каждое слово фильтра — начало слова в имени. */
export function speakerMatches(speaker: Token[], q: Query): boolean {
  if (!q.speakers.length) return true;
  return q.speakers.some((filter) => filter.every((w) => speaker.some((t) => t.norm.startsWith(w))));
}

function merge(ranges: Range[]): Range[] {
  ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const out: Range[] = [];
  for (const r of ranges) {
    const last = out[out.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else out.push([r[0], r[1]]);
  }
  return out;
}

/**
 * Совпадение в тексте (без учёта спикера): null — не подходит; иначе что
 * подсветить (пусто — подходит без подсветки: в запросе только спикер).
 * `norm` — нормализованный текст целиком: быстрый отсев до разбора по словам.
 */
export function matchTokens(tokens: Token[], q: Query, norm?: string): Range[] | null {
  if (norm !== undefined) {
    for (const s of q.stems) if (!norm.includes(s)) return null;
    for (const p of q.phrases) if (!norm.includes(p[0]!)) return null;
  }
  for (const s of q.stems) if (!tokens.some((t) => t.norm.startsWith(s))) return null;
  const ranges: Range[] = [];
  for (const p of q.phrases) {
    let found = false;
    for (let i = 0; i + p.length <= tokens.length; i++) {
      let ok = true;
      for (let j = 0; j < p.length; j++) {
        if (tokens[i + j]!.norm !== p[j]) { ok = false; break; }
      }
      if (ok) {
        found = true;
        ranges.push([tokens[i]!.start, tokens[i + p.length - 1]!.end]);
      }
    }
    if (!found) return null;
  }
  if (q.stems.length) {
    for (const t of tokens) if (q.stems.some((s) => t.norm.startsWith(s))) ranges.push([t.start, t.end]);
  }
  return merge(ranges);
}

/** Текст, разобранный один раз: поиск по нему при каждом нажатии клавиши не разбирает его заново. */
export type Prepared = { text: string; norm: string; tokens: Token[]; speaker: Token[] };

/** `text` в результате — после NFC: подсветка (`Range`) — по нему. */
export function prepare(raw: string, speaker = ""): Prepared {
  const text = nfc(raw);
  return { text, norm: normWord(text), tokens: tokenize(text), speaker: tokenize(nfc(speaker)) };
}

/** Совпадение в реплике с учётом спикера; пустой запрос не находит ничего. */
export function matchPrepared(p: Prepared, q: Query): Range[] | null {
  if (isEmptyQuery(q) || !speakerMatches(p.speaker, q)) return null;
  return matchTokens(p.tokens, q, p.norm);
}

/** Одно найденное место: реплика и (если есть) кусок в ней. */
export type Hit = { turn: number; range: Range | null };

/** Все совпадения по репликам: в каждой подходящей — каждое вхождение, по порядку. */
export function findHits(turns: Prepared[], q: Query): { hits: Hit[]; ranges: Map<number, Range[]> } {
  const hits: Hit[] = [];
  const ranges = new Map<number, Range[]>();
  if (isEmptyQuery(q)) return { hits, ranges };
  turns.forEach((p, turn) => {
    const found = matchPrepared(p, q);
    if (!found) return;
    ranges.set(turn, found);
    if (found.length) for (const range of found) hits.push({ turn, range });
    else hits.push({ turn, range: null });
  });
  return { hits, ranges };
}

/** Куски текста для отрисовки: обычные и подсвеченные (с номером совпадения). */
export function splitByRanges(text: string, ranges: Range[]): { text: string; mark: boolean }[] {
  const out: { text: string; mark: boolean }[] = [];
  let at = 0;
  for (const [s, e] of ranges) {
    if (s > at) out.push({ text: text.slice(at, s), mark: false });
    out.push({ text: text.slice(s, e), mark: true });
    at = e;
  }
  if (at < text.length) out.push({ text: text.slice(at), mark: false });
  return out;
}
