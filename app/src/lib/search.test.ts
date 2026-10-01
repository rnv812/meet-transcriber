import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  findHits, matchPrepared, parseQuery, prepare, splitByRanges, stem, tokenize, type Range,
} from "./search";
import { mergeTurns } from "./speakers";
import type { Segment } from "./types";

// Общие случаи с резидентом (tests/test_search.py): правила не должны расходиться.
type Cases = {
  stems: [string, string][];
  queries: { query: string; phrases: string[][]; keywords: string[]; speakers: string[][] }[];
  cases: { name: string; query: string; text: string; speaker?: string; marked: string | null }[];
  turns: { segments: Segment[]; turns: [number, string, string][] }[];
};
// vitest запускается из app/: общие случаи — в tests/ корня репозитория.
const shared = JSON.parse(
  readFileSync(join(process.cwd(), "..", "tests", "fixtures", "search_cases.json"), "utf8")) as Cases;

const marked = (text: string, ranges: Range[]) =>
  splitByRanges(text, ranges).map((p) => (p.mark ? `[${p.text}]` : p.text)).join("");

test.each(shared.stems)("основа %s → %s", (word, expected) => {
  expect(stem(word)).toBe(expected);
});

test.each(shared.queries)("разбор запроса $query", ({ query, phrases, keywords, speakers }) => {
  const q = parseQuery(query);
  expect({ phrases: q.phrases, keywords: q.keywords, speakers: q.speakers }).toEqual({ phrases, keywords, speakers });
});

test.each(shared.cases)("$name", ({ query, text, speaker, marked: expected }) => {
  const p = prepare(text, speaker ?? "");
  const found = matchPrepared(p, parseQuery(query));
  expect(found === null ? null : marked(p.text, found)).toBe(expected);
});

test.each(shared.turns)("реплики склеиваются как у резидента", ({ segments, turns }) => {
  const merged = mergeTurns(segments.map((s) => ({ ...s, uncertain: false })));
  expect(merged.map((t) => [t.start, t.speaker, t.texts.join(" ")])).toEqual(turns);
});

test("подсветка — места в исходном тексте, с его регистром и знаками", () => {
  const text = "«Ёжик», сказала Анна.";
  const [t] = tokenize(text);
  expect(t).toEqual({ norm: "ежик", start: 1, end: 5 });
  expect(matchPrepared(prepare(text), parseQuery("ежик"))).toEqual([[1, 5]]);
});

test("совпадения по порядку: каждое вхождение — отдельно, реплика только спикера — одно", () => {
  const turns = [prepare("Релиз и релиз.", "Анна"), prepare("Ничего.", "Борис"), prepare("После релиза.", "Анна")];
  const { hits, ranges } = findHits(turns, parseQuery("релиз"));
  expect(hits).toEqual([
    { turn: 0, range: [0, 5] }, { turn: 0, range: [8, 13] }, { turn: 2, range: [6, 12] },
  ]);
  expect([...ranges.keys()]).toEqual([0, 2]);
  expect(findHits(turns, parseQuery("спикер:Борис")).hits).toEqual([{ turn: 1, range: null }]);
  expect(findHits(turns, parseQuery("  ")).hits).toEqual([]);
});

test("большая встреча: 3000 реплик разбираются один раз, поиск линейный", () => {
  const text = (i: number) => `Реплика ${i}: обсудили задачи спринта, сроки релиза и бюджет на квартал.`;
  const turns = Array.from({ length: 3000 }, (_, i) => prepare(text(i), i % 2 ? "Анна" : "Борис"));
  const { hits } = findHits(turns, parseQuery("задача \"бюджет на квартал\" спикер:Анна"));
  expect(hits).toHaveLength(1500 * 2);
  expect(hits[0]).toEqual({ turn: 1, range: [20, 26] });
  // Слово, которого нет нигде, отсеивается по нормализованному тексту — без разбора на слова.
  expect(findHits(turns, parseQuery("отсутствует")).hits).toEqual([]);
});
