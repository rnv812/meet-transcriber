/**
 * Окно на macOS — системный WebKit (WKWebView), а не Chromium: на macOS 13.0
 * (минимальная, tauri.macos.conf.json) это Safari 16.1. Сборка нацелена на
 * chrome110 и такого не переписывает, а в WebKit 16.1–16.3:
 *
 * - нет просмотра назад в регулярных выражениях (`(?<=…)`, `(?<!…)`, Safari
 *   16.4) — литерал с ним роняет разбор всего бандла: окно пустое;
 * - нет `color-mix()` (Safari 16.2) — правило с ним отбрасывается.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { findJira, jiraBaseError, jiraLinker } from "./jira";
import { removeTerm } from "./textfix";

// Папка src окна — от этого файла, а не от рабочей папки: из корня репозитория
// `process.cwd()/src` — это Python, и проверка прошла бы впустую.
const ROOT = join(__dirname, "..");

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) sources(path, out);
    else if (/\.(tsx?|css)$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(path);
  }
  return out;
}

/**
 * Скопированные файлы Atlas Aurora (theme/aurora/*, первая строка
 * «/* Atlas Aurora»): в них color-mix() — как в дизайн-системе; на macOS 13
 * их цвета подменяет theme/aurora-fallbacks.css. Во всех остальных файлах окна —
 * запрет.
 */
const isVendored = (path: string) =>
  relative(ROOT, path).startsWith(join("theme", "aurora")) &&
  readFileSync(path, "utf8").startsWith("/* Atlas Aurora");

/** Запрос возможности `@supports not (color: color-mix(…))` — не использование color-mix(): так WebKit без него включает запасные правила. */
const COLOR_MIX_QUERY = /@supports\s+not\s*\(\s*color:\s*color-mix\([^()]*\)\s*\)/g;

/** Код без комментариев: в них о запретном можно писать. */
const code = (text: string) =>
  text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "").replace(COLOR_MIX_QUERY, "@supports not (…)");

test("в коде окна нет просмотра назад в регулярках и color-mix() — их нет в WebKit macOS 13.0", () => {
  const bad: string[] = [];
  const files = sources(ROOT);
  expect(files.length).toBeGreaterThan(100);
  expect(files.some((f) => f.endsWith("jira.ts"))).toBe(true);
  for (const path of files) {
    const text = code(readFileSync(path, "utf8"));
    if (/\(\?<[=!]/.test(text)) bad.push(`${relative(ROOT, path)}: (?<= / (?<!`);
    if (/color-mix\(/.test(text) && !isVendored(path)) bad.push(`${relative(ROOT, path)}: color-mix()`);
  }
  expect(bad).toEqual([]);
});

test("исключение для color-mix() — только скопированные файлы Aurora", () => {
  expect(isVendored(join(ROOT, "theme", "aurora", "palettes.css"))).toBe(true);
  expect(isVendored(join(ROOT, "theme", "aurora", "index.css"))).toBe(false);
  expect(isVendored(join(ROOT, "theme", "tokens.css"))).toBe(false);
});

test("адрес Jira: узел не начинается и не кончается точкой или дефисом — как раньше", () => {
  expect(jiraBaseError("https://jira.example.com")).toBeNull();
  expect(jiraBaseError("https://j:8443/base")).toBeNull();
  expect(jiraBaseError("https://-jira.example.com")).not.toBeNull();
  expect(jiraBaseError("https://jira.example.com.")).not.toBeNull();
  expect(jiraBaseError("https://jira-.example.com-:8080")).not.toBeNull();
});

test("ключ Jira — не часть слова: слева ни буквы, ни цифры, ни дефиса", () => {
  const linker = jiraLinker({ integrations: { jira_base_url: "https://jira.example.com" } })!;
  const keys = (text: string) => findJira(text, linker).map((m) => [m.start, m.key]);
  expect(keys("SPR-131 и ABC-7, а также xSPR-1, x-SPR-2 и SPR-3x")).toEqual([[0, "SPR-131"], [10, "ABC-7"]]);
  expect(keys("(SPR-5)")).toEqual([[1, "SPR-5"]]);
});

test("removeTerm без просмотра назад: строки и переводы строк — как были", () => {
  expect(removeTerm("альфа\nбета\nгамма", "бета")).toBe("альфа\nгамма");
  expect(removeTerm("альфа\nбета\n", "бета")).toBe("альфа\n");
  expect(removeTerm("бета", "бета")).toBe("");
  expect(removeTerm("", "бета")).toBe("");
});
