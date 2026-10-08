// @vitest-environment node
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";

/**
 * Прежние имена переменных окна (до 0.4) удалены вместе с
 * theme/legacy-aliases.css (этап 7): стили и код окна берут токены Atlas
 * Aurora напрямую (--bg → --canvas, --text → --ink, --line → --hairline …).
 * Ссылка на удалённое имя молча делает свойство недействительным. Исключение —
 * имя, которое окно объявляет само как своё (с причиной ниже).
 */
const SRC = join(process.cwd(), "src");
const REMOVED = ["--bg", "--surface", "--surface-hover", "--line", "--line-2", "--text", "--text-2", "--text-3",
  "--accent-hi", "--accent-glow", "--violet", "--red-glow", "--red", "--ok", "--run", "--err", "--amber", "--t",
  "--nav-w", "--list-w"];
/** Своё имя окна → где и зачем объявлено. */
const OWN: Record<string, string> = {
  "--list-w": "ширина списка записей: умолчание — на .app в theme/tokens.css, после замера ставит app/ShellResize",
};
const names = REMOVED.filter((n) => !OWN[n]).map((n) => n.slice(2)).join("|");
/** Использование: `var(--имя)` / `var(--имя, запас)`; `--text-xl` и `--tone` — другие имена. */
const USE = new RegExp(`var\\(\\s*--(?:${names})\\s*[,)]`);
/** Объявление: `--имя:`, не модификатор класса (`.btn--red:`). */
const DECL = new RegExp(`(?<![\\w-])--(?:${names})\\s*:`);

function walk(dir: string, ext: RegExp, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) walk(p, ext, out);
    else if (ext.test(e.name)) out.push(p);
  }
  return out;
}
const relOf = (f: string) => relative(SRC, f).split(sep).join("/");
/** Комментарии вырезаются, переводы строк остаются: номера строк в сообщении верны. */
const strip = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ""));
/** CSS окна — всё под src, кроме скопированной дизайн-системы. */
const windowCss = () => walk(SRC, /\.css$/).filter((f) => !relOf(f).startsWith("theme/aurora/"));
/** Код окна (inline-стили `var(--…)`); тесты не в счёт — в них имена встречаются как образцы. */
const windowCode = () => walk(SRC, /\.tsx?$/).filter((f) => !/\.test\.[jt]sx?$/.test(f));

function offending(re: RegExp, files: string[], clean: (t: string) => string): string[] {
  const bad: string[] = [];
  for (const f of files) {
    clean(readFileSync(f, "utf8")).split("\n").forEach((line, i) => {
      if (re.test(line)) bad.push(`${relOf(f)}:${i + 1}: ${line.trim()}`);
    });
  }
  return bad;
}

test("разбор: удалённые имена находит, токены Aurora с похожим началом — нет", () => {
  for (const s of ["a { color: var(--text); }", "a { color: var( --text-2 , red) }", "a { transition: width var(--t); }",
    "a { border: 1px solid var(--line-2); }", "style={{ color: \"var(--ok)\" }}"]) expect(USE.test(s), s).toBe(true);
  for (const s of ["a { font-size: var(--text-xl); }", "a { color: var(--tone); }", "a { color: var(--surface-2); }",
    "a { color: var(--accent-line); }", "a { left: var(--list-w); }"]) expect(USE.test(s), s).toBe(false);
  expect(DECL.test(":root { --bg: #000 }")).toBe(true);
  expect(DECL.test(".btn--red:hover { color: red }")).toBe(false);
});

test("theme/legacy-aliases.css удалён", () => {
  expect(existsSync(join(SRC, "theme", "legacy-aliases.css"))).toBe(false);
});

test("CSS окна не ссылается на удалённые имена переменных и не объявляет их снова", () => {
  expect(offending(USE, windowCss(), strip)).toEqual([]);
  expect(offending(DECL, windowCss(), strip)).toEqual([]);
});

test("код окна не ссылается на удалённые имена переменных", () => {
  expect(offending(USE, windowCode(), (t) => t)).toEqual([]);
});

test("свои имена окна объявлены в его CSS", () => {
  const css = windowCss().map((f) => strip(readFileSync(f, "utf8"))).join("\n");
  for (const name of Object.keys(OWN)) {
    expect(REMOVED, name).toContain(name);
    expect(new RegExp(`(?<![\\w-])${name}\\s*:`).test(css), name).toBe(true);
  }
});
