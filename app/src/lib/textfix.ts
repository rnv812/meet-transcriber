/**
 * «Исправить…» в расшифровке: выделение → целые слова, место в сегменте,
 * правка списка терминов распознавания (как meet/hotwords.py).
 */

import { nfc } from "./search";

const WORD = /[\p{L}\p{N}]/u;
const isWord = (c: string | undefined) => !!c && WORD.test(c);

/**
 * Выделение [start, end) в тексте реплики → целые слова: недовыделенное слово
 * дополняется, знаки препинания и пробелы по краям отбрасываются. null — слов нет.
 */
export function expandToWords(text: string, start: number, end: number): { start: number; end: number } | null {
  let a = Math.max(0, Math.min(start, end));
  let b = Math.min(text.length, Math.max(start, end));
  while (a < b && !isWord(text[a])) a++;
  while (b > a && !isWord(text[b - 1])) b--;
  if (a >= b) return null;
  while (a > 0 && isWord(text[a - 1])) a--;
  while (b < text.length && isWord(text[b])) b++;
  return { start: a, end: b };
}

/** Слово под местом `at` текста (или сразу перед ним); null — там не слово. */
export function wordAt(text: string, at: number): { start: number; end: number } | null {
  const i = isWord(text[at]) ? at : isWord(text[at - 1]) ? at - 1 : -1;
  return i < 0 ? null : expandToWords(text, i, i + 1);
}

/**
 * Место в реплике (сегменты через пробел, как её показывает окно) → номер
 * сегмента реплики и начало в его тексте (NFC). null — выделение захватывает
 * границу двух сегментов: исправляют слова одного.
 */
export function segmentSpan(texts: string[], start: number, end: number): { k: number; offset: number } | null {
  let pos = 0;
  for (let k = 0; k < texts.length; k++) {
    const len = nfc(texts[k] ?? "").length;
    if (start >= pos && end <= pos + len) return { k, offset: start - pos };
    pos += len + 1;
  }
  return null;
}

const words = (text: string) => (text.normalize("NFC").toLowerCase().replace(/ё/g, "е").match(/[\p{L}\p{N}]+/gu) ?? []);
const meaningful = (w: string) => [...w].length >= 4 || /[\p{Lu}\p{N}A-Za-z]/u.test(w);

/**
 * Что из исправления стоит добавить в термины (как meet/replacements.py,
 * new_terms): слова `replace`, которых нет в `find`, без коротких служебных.
 * Пусто — ничего значимого не изменилось.
 */
export function newTerms(find: string, replace: string): string {
  const have = new Set(words(find));
  return replace.trim().split(/\s+/).map((w) => w.replace(/^[^\p{L}\p{N}_]+|[^\p{L}\p{N}_]+$/gu, ""))
    .filter((w) => w && !words(w).every((x) => have.has(x)) && meaningful(w)).join(" ");
}

const key = (term: string) => term.toLowerCase().replace(/ё/g, "е").split(/\s+/).filter(Boolean).join(" ");

/** Убрать строку с термином (последнюю такую — её добавило «Исправить…»), остальное как есть. */
export function removeTerm(text: string, term: string): string {
  const lines = text.split(/(?<=\n)/);
  const want = term.split(/\s+/).filter(Boolean).join(" ");
  for (let i = lines.length - 1; i >= 0; i--) {
    if ((lines[i]!.split("#", 1)[0] ?? "").trim() === want) return [...lines.slice(0, i), ...lines.slice(i + 1)].join("");
  }
  return text;
}

/** Термин уже в списке (без учёта регистра, «ё» = «е»)? */
export function hasTerm(text: string, term: string): boolean {
  const want = key(term);
  return text.split(/\r?\n/).some((l) => key(l.split("#", 1)[0] ?? "") === want);
}
