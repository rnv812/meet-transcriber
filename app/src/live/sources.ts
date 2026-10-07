/**
 * Чипы-источники у сообщения агента: документ, который он упомянул, —
 * открыть щелчком (оболочка, `open_material`).
 *
 * Meet ничего не угадывает (v4-simple): чип появляется только при точном
 * упоминании того, что Meet и так знает.
 *
 * - **Вложения чата** (записи журнала `attachment`, разобранные и не убранные):
 *   в тексте есть их название целиком с расширением (`План запуска.pptx`;
 *   папка без расширения по имени не узнаётся) или ссылка `mat:a3` (не
 *   `mat:a31`). Открывается исходный файл (`source`), если оболочка его
 *   пустит, иначе копия во встрече (`assistant/files`, текст в
 *   `assistant/materials`).
 * - **Документы базы знаний** (`GET /assistant/kb-docs`, без исключённых):
 *   в тексте есть путь документа от базы — с расширением или без, но с
 *   папкой (`Проекты/Альфа/План.md`, `Проекты/Альфа/План` — но не
 *   `Проекты/Альфа/План.pdf`, это другой файл), ссылка `kb:путь` или имя
 *   файла с расширением (`План.md`), если такое имя в базе одно и в нём не
 *   меньше KB_NAME_MIN символов.
 *
 * Сравнение — без учёта регистра, «ё» как «е», «\» как «/»; совпадение не
 * должно продолжать слово (`План.md` не найдётся в `СтарыйПлан.md`) и путь
 * (`Альфа/План.md` не найдётся в `Проекты/Альфа/План.md` — это другой
 * документ). Не больше MAX_SOURCES чипов у сообщения, одинаковые названия —
 * один раз; вложения — первыми. Список базы спрашивается, только если в
 * сообщении есть расширение файла, ссылка `kb:`/`mat:` или путь через «/».
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
/** Сообщение, в котором может быть документ: расширение файла, ссылка kb:/mat: или путь через «/». */
const DOC_HINT = /\.(md|txt|docx?|pptx?|xlsx?|csv|pdf|png|jpe?g|gif|webp|bmp|tiff?)(?![\p{L}\p{N}_])|(?:^|[^\p{L}\p{N}_])(kb|mat):|[\p{L}\p{N}_][\\/][\p{L}\p{N}_]/iu;
/** Название с расширением (у папки его нет). */
const HAS_EXT = /\.[\p{L}\p{N}]{1,5}$/u;

const fold = (text: string) => text.toLowerCase().replace(/ё/g, "е").replace(/\\/g, "/");
const wordChar = (c: string | undefined) => c !== undefined && /[\p{L}\p{N}_]/u.test(c);

type Match = {
  min?: number;
  /** Перед совпадением не «/»: иначе это хвост другого, более длинного пути. */
  whole?: boolean;
  /** Путь без расширения: за ним не «.расширение» — это другой файл. */
  bare?: boolean;
};

/** Упоминание `needle` в тексте `hay` (оба свёрнуты), не посреди слова. */
function mentioned(hay: string, needle: string, { min = ATTACHMENT_NAME_MIN, whole = false, bare = false }: Match = {}): boolean {
  if (needle.length < min) return false;
  for (let at = hay.indexOf(needle); at >= 0; at = hay.indexOf(needle, at + 1)) {
    const before = hay[at - 1];
    const after = hay[at + needle.length];
    if (wordChar(before) || wordChar(after)) continue;
    if (whole && before === "/") continue;
    if (bare && after === "." && wordChar(hay[at + needle.length + 1])) continue;
    return true;
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
  const labels = new Set<string>();
  const add = (s: Source) => {
    const first = s.paths[0];
    const label = fold(s.label);
    if (!first || seen.has(fold(first)) || labels.has(label) || out.length >= MAX_SOURCES) return;
    seen.add(fold(first));
    labels.add(label);
    out.push(s);
  };
  for (const a of attachments) {
    if (a.kind !== "attachment" || a.status !== "ready" || !a.name) continue;
    const name = fold(a.name);
    const byName = HAS_EXT.test(name) && mentioned(hay, name, { whole: true });
    const byLink = new RegExp(`mat:${a.id.toLowerCase()}(?![0-9])`).test(hay);
    if (!byName && !byLink) continue;
    const paths = [a.source, a.path].filter((p): p is string => typeof p === "string" && !!p);
    add({ key: a.id, label: a.name, kind: a.type === "image" ? "image" : "doc", paths });
  }
  if (kb && mayMentionDocs(text)) {
    for (const d of kb.docs) {
      if (out.length >= MAX_SOURCES) break;
      const nested = d.path.includes("/");
      const hit = (nested && mentioned(hay, d.path, { whole: true }))
        || (nested && mentioned(hay, d.noExt, { whole: true, bare: true }))
        || mentioned(hay, `kb:${d.path}`) || mentioned(hay, `kb:${d.noExt}`, { bare: true })
        || (kb.names.get(d.name) === 1 && mentioned(hay, d.name, { min: KB_NAME_MIN, whole: true }));
      if (!hit) continue;
      const native = kb.root.endsWith("\\") ? d.rel.replace(/\//g, "\\") : d.rel;
      add({ key: `kb:${d.rel}`, label: d.rel.split("/").pop() ?? d.rel, kind: "kb", paths: [`${kb.root}${native}`] });
    }
  }
  return out;
}
