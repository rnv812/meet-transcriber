/**
 * Чипы-источники у сообщения агента: документ, который он упомянул, —
 * открыть щелчком (оболочка, `open_material`).
 *
 * Meet ничего не угадывает (v4-simple): чип появляется только при точном
 * упоминании того, что Meet и так знает.
 *
 * - **Вложения чата** (записи журнала `attachment`, разобранные и не убранные):
 *   в тексте есть их название целиком (`План запуска.pptx`) или ссылка
 *   `mat:a3`. Открывается исходный файл (`source`), если оболочка его пустит,
 *   иначе копия во встрече (`assistant/files`, текст в `assistant/materials`).
 * - **Документы базы знаний** (`GET /assistant/kb-docs`, без исключённых):
 *   в тексте есть путь документа от базы — с расширением или без, но с
 *   папкой (`Проекты/Альфа/План.md`, `Проекты/Альфа/План`), ссылка `kb:путь`
 *   или имя файла с расширением (`План.md`), если такое имя в базе одно и в
 *   нём не меньше KB_NAME_MIN символов.
 *
 * Сравнение — без учёта регистра, «ё» как «е», «\» как «/»; совпадение не
 * должно продолжать слово (`План.md` не найдётся в `СтарыйПлан.md`). Не
 * больше MAX_SOURCES чипов у сообщения; вложения — первыми.
 */

import type { ChatMessage, KbDocs } from "../lib/types";

export type Source = {
  key: string;
  label: string;
  kind: "doc" | "image" | "kb";
  /** Что открыть, по порядку: первый путь, который оболочка пустит. */
  paths: string[];
};

export const MAX_SOURCES = 4;
/** Имя файла без папки узнаётся, только если в нём не меньше стольких символов. */
export const KB_NAME_MIN = 5;
/** Вложение узнаётся по названию не короче. */
const ATTACHMENT_NAME_MIN = 3;
/** Сообщение, в котором может быть документ: расширение файла или ссылка kb:/mat:. */
const DOC_HINT = /\.(md|txt|docx?|pptx?|xlsx?|csv|pdf|png|jpe?g|gif|webp|bmp|tiff?)(?![\p{L}\p{N}_])|\]\((kb|mat):/iu;

const fold = (text: string) => text.toLowerCase().replace(/ё/g, "е").replace(/\\/g, "/");
const wordChar = (c: string | undefined) => c !== undefined && /[\p{L}\p{N}_]/u.test(c);

/** Упоминание `needle` в тексте `hay` (оба свёрнуты), не посреди слова. */
function mentioned(hay: string, needle: string, min = ATTACHMENT_NAME_MIN): boolean {
  if (needle.length < min) return false;
  for (let at = hay.indexOf(needle); at >= 0; at = hay.indexOf(needle, at + 1)) {
    if (!wordChar(hay[at - 1]) && !wordChar(hay[at + needle.length])) return true;
  }
  return false;
}

/** Есть ли в тексте признак упоминания документа (иначе базу знаний и не спрашиваем). */
export const mayMentionDocs = (text: string): boolean => DOC_HINT.test(text);

/** Индекс базы для поиска: свёрнутые пути и имена, сколько раз встречается каждое имя. */
export type KbIndex = {
  root: string;
  docs: { rel: string; path: string; noExt: string; name: string }[];
  names: Map<string, number>;
};

export function kbIndex(kb: KbDocs | null): KbIndex | null {
  if (!kb?.root || !kb.docs.length) return null;
  const sep = kb.root.includes("\\") ? "\\" : "/";
  const base = kb.root.replace(/[\\/]+$/, "");
  const names = new Map<string, number>();
  const docs = kb.docs.map((rel) => {
    const path = fold(rel);
    const name = path.split("/").pop() ?? path;
    names.set(name, (names.get(name) ?? 0) + 1);
    return { rel, path, noExt: path.replace(/\.[^./]+$/, ""), name };
  });
  return { root: `${base}${sep}`, docs, names };
}

export function findSources(text: string, attachments: ChatMessage[], kb: KbIndex | null): Source[] {
  if (!text || !mayMentionDocs(text) && !attachments.length) return [];
  const hay = fold(text);
  const out: Source[] = [];
  const seen = new Set<string>();
  const add = (s: Source) => {
    const first = s.paths[0];
    if (!first || seen.has(fold(first)) || out.length >= MAX_SOURCES) return;
    seen.add(fold(first));
    out.push(s);
  };
  for (const a of attachments) {
    if (a.kind !== "attachment" || a.status !== "ready" || !a.name) continue;
    const name = fold(a.name);
    if (!mentioned(hay, name) && !hay.includes(`mat:${a.id.toLowerCase()}`)) continue;
    const paths = [a.source, a.path].filter((p): p is string => typeof p === "string" && !!p);
    add({ key: a.id, label: a.name, kind: a.type === "image" ? "image" : "doc", paths });
  }
  if (kb && mayMentionDocs(text)) {
    for (const d of kb.docs) {
      if (out.length >= MAX_SOURCES) break;
      const nested = d.path.includes("/");
      const hit = (nested && mentioned(hay, d.path)) || (nested && mentioned(hay, d.noExt))
        || hay.includes(`kb:${d.path}`)
        || (kb.names.get(d.name) === 1 && mentioned(hay, d.name, KB_NAME_MIN));
      if (!hit) continue;
      const native = kb.root.endsWith("\\") ? d.rel.replace(/\//g, "\\") : d.rel;
      add({ key: `kb:${d.rel}`, label: d.rel.split("/").pop() ?? d.rel, kind: "kb", paths: [`${kb.root}${native}`] });
    }
  }
  return out;
}
