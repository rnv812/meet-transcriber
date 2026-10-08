import { readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";

/**
 * Жёсткий белый (`#fff`, `#ffffff`, `white`) вне theme/aurora — ошибка: в
 * светлой теме белый текст на светлой подложке не читается, а на светлом
 * сигнале Aurora в тёмной — слабый контраст. Текст — `--ink` / `--on-accent`,
 * ползунки — `--ink`. Исключение — с причиной.
 */
const SRC = join(process.cwd(), "src");
const EXCEPTIONS: Record<string, string> = {
  "tray/tray.css": "панель трея — собственная фиксированная тёмная палитра, белый на её градиентных кнопках",
};
const WHITE = /#fff(?:fff)?(?![0-9a-z])|(^|[^\w-])white(?![\w-])/i;

function css(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) css(p, out);
    else if (e.name.endsWith(".css")) out.push(p);
  }
  return out;
}

test("разбор: белый цвет находит, white-space и похожие имена — нет", () => {
  expect(WHITE.test("a { color: #fff; }")).toBe(true);
  expect(WHITE.test("a { color: #FFFFFF }")).toBe(true);
  expect(WHITE.test("a { background:white; }")).toBe(true);
  expect(WHITE.test("a { color: var(--c, white); }")).toBe(true);
  expect(WHITE.test("a { white-space: nowrap; }")).toBe(false);
  expect(WHITE.test("a { color: #fff8; }")).toBe(false);
  expect(WHITE.test("a { color: #ffffff80; }")).toBe(false);
  expect(WHITE.test("a { --off-white: 1; }")).toBe(false);
});

test("CSS окна вне theme/aurora — без жёсткого белого", () => {
  const bad: string[] = [];
  for (const f of css(SRC)) {
    const rel = relative(SRC, f).split(sep).join("/");
    if (rel.startsWith("theme/aurora/") || EXCEPTIONS[rel]) continue;
    // Комментарии убираем, сохраняя переводы строк — номера строк остаются верными.
    const lines = readFileSync(f, "utf8").replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, "")).split("\n");
    lines.forEach((line, i) => { if (WHITE.test(line)) bad.push(`${rel}:${i + 1}: ${line.trim()}`); });
  }
  expect(bad).toEqual([]);
});
