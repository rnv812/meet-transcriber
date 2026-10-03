/**
 * Ссылки на задачи Jira в расшифровке, итогах и наблюдениях: ключ задачи
 * («SPR-131») становится ссылкой на `<integrations.jira_base_url>/browse/<ключ>`.
 * Адрес пуст — ссылок нет. Открывает их оболочка (`open_url`), и только на
 * хост из настроек: его же проверяет Rust (windows.rs, `jira_prefix`).
 *
 * Настройки (0.3.1): проекты Jira (`integrations.jira_projects` — ключ и свои
 * варианты произношения), проект по умолчанию (`jira_default_project`) и
 * необязательный шаблон ключа для текста (`jira_pattern`, «Дополнительно»):
 * пусто — ключи проектов, а без проектов — любой ключ вида «ПРОЕКТ-123».
 * Ключ не цепляется внутри слова: «XSPR-1» и «SPR-12a» — не ссылки.
 */

import { createContext } from "react";

export const DEFAULT_JIRA_KEYS = "[A-Z][A-Z0-9]+-\\d+";
const MAX_LEN = 200;
/** https://хост[:порт][/путь] — без логина, запроса и фрагмента. */
const BASE = /^https:\/\/(?![.-])[A-Za-z0-9.-]+(?<![.-])(?::\d{1,5})?(?:\/[A-Za-z0-9._~%/-]*)?$/;
const PROJECTS = /^[A-Z][A-Z0-9]+(?:\s*,\s*[A-Z][A-Z0-9]+)*$/;
/** Синтаксис, которого нет в JavaScript (или который понимается по-разному в Python и JS). */
const NOT_PORTABLE = /\(\?[aiLmsux#P]/;

/** Ключ проекта, как в Jira (и в settings.JIRA_PROJECT_KEY). */
export const PROJECT_KEY = /^[A-Z][A-Z0-9_]{1,19}$/;
export const PROJECTS_MAX = 30;
export const ALIASES_MAX = 10;
export const ALIAS_MAX = 40;

export type JiraProject = { key: string; aliases: string[] };

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

/** Ошибка шаблона ключа для текста («Дополнительно») для человека; null — годится. */
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

/** Ошибка ключа нового проекта; `taken` — уже добавленные ключи. */
export function projectKeyError(raw: string, taken: string[] = []): string | null {
  const key = raw.trim().toUpperCase();
  if (!key) return null;
  if (!PROJECT_KEY.test(key)) {
    return "Ключ проекта — латинские буквы и цифры, начинается с буквы, от 2 до 20 знаков (например, ORION)";
  }
  if (taken.includes(key)) return `Проект ${key} уже в списке`;
  return null;
}

/** Ошибка своего варианта названия проекта (как в settings.jira_alias_error). */
export function aliasError(raw: string): string | null {
  const text = raw.trim().split(/\s+/).join(" ");
  if (!text) return null;
  if (text.length > ALIAS_MAX) return `Не длиннее ${ALIAS_MAX} символов`;
  if (/\p{N}/u.test(text)) return "Без цифр: цифры читаются как номер задачи";
  if (/[^\p{L} \-'’]/u.test(text)) return "Только буквы, пробелы и дефис";
  if ((text.match(/\p{L}/gu) ?? []).length < 3) return "Хотя бы три буквы";
  return null;
}

/** Ошибка списка проектов целиком (для «Сохранить»); null — годится. */
export function projectsError(projects: JiraProject[]): string | null {
  if (projects.length > PROJECTS_MAX) return `Проектов больше ${PROJECTS_MAX}`;
  const seen: string[] = [];
  for (const p of projects) {
    if (!PROJECT_KEY.test(p.key)) return `«${p.key}»: негодный ключ проекта`;
    if (seen.includes(p.key)) return `Проект ${p.key} указан дважды`;
    seen.push(p.key);
    if (p.aliases.length > ALIASES_MAX) return `${p.key}: вариантов названия больше ${ALIASES_MAX}`;
    for (const a of p.aliases) {
      const error = aliasError(a) ?? (a.trim() ? null : "Пустой вариант названия");
      if (error) return `${p.key}: ${error}`;
    }
  }
  return null;
}

type Raw = Record<string, unknown> | undefined;

/** Проекты из настроек (`integrations.jira_projects`): битые записи пропускаются. */
export function jiraProjects(integrations: Raw): JiraProject[] {
  const raw = integrations?.jira_projects;
  if (!Array.isArray(raw)) return [];
  const out: JiraProject[] = [];
  for (const item of raw) {
    const p = item as { key?: unknown; aliases?: unknown } | null;
    const key = typeof p?.key === "string" ? p.key : "";
    if (!PROJECT_KEY.test(key) || out.some((x) => x.key === key)) continue;
    const aliases = Array.isArray(p?.aliases) ? p.aliases.filter((a): a is string => typeof a === "string") : [];
    out.push({ key, aliases });
  }
  return out;
}

/** Шаблон ключа в тексте: свой (`jira_pattern`), иначе ключи проектов, иначе любой ключ. */
export function literalPattern(integrations: Raw): string {
  const own = typeof integrations?.jira_pattern === "string" ? integrations.jira_pattern.trim() : "";
  if (own && !jiraKeysError(own)) return keysPattern(own);
  const projects = jiraProjects(integrations);
  return projects.length ? `(?:${projects.map((p) => p.key).join("|")})-\\d+` : DEFAULT_JIRA_KEYS;
}

/** Ссылки из настроек (`GET /settings`); null — выключены или адрес не задан. */
export function jiraLinker(settings: Record<string, unknown> | null | undefined): JiraLinker | null {
  const view = settings?.transcript_view as Raw;
  if (view?.jira === false) return null;
  const integrations = settings?.integrations as Raw;
  const base = typeof integrations?.jira_base_url === "string" ? integrations.jira_base_url.trim() : "";
  if (!base || jiraBaseError(base)) return null;
  try {
    return {
      re: new RegExp(`(?<![\\p{L}\\p{N}_-])(?:${literalPattern(integrations)})(?![\\p{L}\\p{N}_])`, "gu"),
      base: base.replace(/\/+$/, ""),
    };
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
