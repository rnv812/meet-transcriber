/**
 * Ссылки на задачи Jira в расшифровке, итогах и наблюдениях — без агента:
 * ключи вида «SPR-131» по шаблону из настроек (`integrations.jira_keys`)
 * становятся ссылками на `<integrations.jira_base_url>/browse/<ключ>`.
 * Адрес пуст — ссылок нет. Открывает их оболочка (`open_url`), и только на
 * хост из настроек: его же проверяет Rust (windows.rs, `jira_prefix`).
 *
 * Шаблон — регулярное выражение или список ключей проектов через запятую
 * («SPR, OPS» → только задачи этих проектов). Ключ не цепляется внутри слова:
 * «XSPR-1» и «SPR-12a» — не ссылки.
 */

import { createContext } from "react";

export const DEFAULT_JIRA_KEYS = "[A-Z][A-Z0-9]+-\\d+";
const MAX_LEN = 200;
/** https://хост[:порт][/путь] — без логина, запроса и фрагмента. */
const BASE = /^https:\/\/(?![.-])[A-Za-z0-9.-]+(?<![.-])(?::\d{1,5})?(?:\/[A-Za-z0-9._~%/-]*)?$/;
const PROJECTS = /^[A-Z][A-Z0-9]+(?:\s*,\s*[A-Z][A-Z0-9]+)*$/;
/** Синтаксис, которого нет в JavaScript (или который понимается по-разному в Python и JS). */
const NOT_PORTABLE = /\(\?[aiLmsux#P]/;

export type JiraLinker = { re: RegExp; base: string };

/** Ошибка адреса Jira для человека; null — годится (пустой — ссылки выключены). */
export function jiraBaseError(raw: string): string | null {
  const base = raw.trim();
  if (!base) return null;
  if (base.length > MAX_LEN) return "Слишком длинный адрес";
  if (!base.toLowerCase().startsWith("https://")) return "Адрес должен начинаться с https://";
  if (!BASE.test(base) || base.includes("..")) {
    return "Укажите адрес вида https://jira.example.com — без логина, пароля, «?» и «#»";
  }
  return null;
}

/** Регулярное выражение ключа: список проектов → (?:A|B)-\d+, пусто → по умолчанию. */
export function keysPattern(raw: string): string {
  const keys = raw.trim();
  if (!keys) return DEFAULT_JIRA_KEYS;
  if (PROJECTS.test(keys)) return `(?:${keys.split(/\s*,\s*/).join("|")})-\\d+`;
  return keys;
}

/** Ошибка шаблона ключей для человека; null — годится. */
export function jiraKeysError(raw: string): string | null {
  const keys = raw.trim();
  if (!keys) return null;
  if (keys.length > MAX_LEN) return "Слишком длинный шаблон";
  if (NOT_PORTABLE.test(keys)) return "Флаги и именованные группы в шаблоне не поддерживаются";
  let re: RegExp;
  try {
    re = new RegExp(`^(?:${keysPattern(keys)})$`, "u");
  } catch {
    return "Шаблон не разобрался: проверьте скобки и экранирование";
  }
  if (re.test("")) return "Шаблон находит пустую строку — уточните его";
  return null;
}

/** Ссылки из настроек (`GET /settings`); null — выключены или адрес не задан. */
export function jiraLinker(settings: Record<string, unknown> | null | undefined): JiraLinker | null {
  const view = settings?.transcript_view as Record<string, unknown> | undefined;
  if (view?.jira === false) return null;
  const integrations = settings?.integrations as Record<string, unknown> | undefined;
  const base = typeof integrations?.jira_base_url === "string" ? integrations.jira_base_url.trim() : "";
  if (!base || jiraBaseError(base)) return null;
  const raw = typeof integrations?.jira_keys === "string" ? integrations.jira_keys : "";
  const keys = jiraKeysError(raw) ? DEFAULT_JIRA_KEYS : keysPattern(raw);
  try {
    return { re: new RegExp(`(?<![\\p{L}\\p{N}_-])(?:${keys})(?![\\p{L}\\p{N}_])`, "gu"), base: base.replace(/\/+$/, "") };
  } catch {
    return null;
  }
}

/** Адрес задачи: `<база>/browse/<ключ>`. */
export const jiraUrl = (linker: JiraLinker, key: string) => `${linker.base}/browse/${encodeURIComponent(key)}`;

export type JiraMatch = { start: number; end: number; key: string };

/** Ключи задач в тексте: начало, конец, ключ. Пустые совпадения пропускаются. */
export function findJira(text: string, linker: JiraLinker | null): JiraMatch[] {
  if (!linker || !text) return [];
  const out: JiraMatch[] = [];
  for (const m of text.matchAll(linker.re)) {
    if (!m[0]) continue;
    out.push({ start: m.index!, end: m.index! + m[0].length, key: m[0] });
  }
  return out;
}

/** Ссылки Jira карточки (из настроек): расшифровка, итоги, наблюдения. */
export const JiraLinks = createContext<JiraLinker | null>(null);
