import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";

/**
 * Примитивы окна и общие стили — только на токенах Atlas Aurora: свой цвет
 * не меняется вместе с темой и палитрой. Литерал цвета — ошибка; если он
 * нужен, — исключение ниже с причиной.
 */
const SRC = join(process.cwd(), "src");
const EXCEPTIONS: Record<string, string> = {};
const COLOR = /#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(/i;

function css(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) css(p, out);
    else if (e.name.endsWith(".css")) out.push(p);
  }
  return out;
}

test("ui/*.css и theme/tokens.css — без литералов цвета", () => {
  const files = [...css(join(SRC, "ui")), join(SRC, "theme", "tokens.css")];
  const bad: string[] = [];
  for (const f of files) {
    const rel = relative(SRC, f);
    if (EXCEPTIONS[rel]) continue;
    const lines = readFileSync(f, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").split("\n");
    lines.forEach((line, i) => { if (COLOR.test(line)) bad.push(`${rel}:${i + 1}: ${line.trim()}`); });
  }
  expect(bad).toEqual([]);
});

/**
 * Текст акцентного цвета — только `--accent-line` (текстовый токен Aurora:
 * сигнал в тёмной теме, средний тон в светлой, ≥ 4,5:1). `--accent` и
 * `--accent-hover` — заливки и рамки: в светлой теме текстом они не читаются.
 */
const ACCENT_TEXT = /(^|[;{\s])color\s*:\s*var\(--accent(-hover)?\)/;

test("разбор: цвет текста --accent / --accent-hover находит, заливку и рамку — нет", () => {
  expect(ACCENT_TEXT.test(".a { color: var(--accent-hover); }")).toBe(true);
  expect(ACCENT_TEXT.test(".a { margin: 0; color:var(--accent) }")).toBe(true);
  expect(ACCENT_TEXT.test(".a { color: var(--accent-line); }")).toBe(false);
  expect(ACCENT_TEXT.test(".a { background-color: var(--accent); border-color: var(--accent-hover); }")).toBe(false);
  expect(ACCENT_TEXT.test(".a { background: var(--accent); text-decoration-color: var(--accent); }")).toBe(false);
});

test("ui/*.css — текст акцентного цвета только через --accent-line", () => {
  const bad: string[] = [];
  for (const f of css(join(SRC, "ui"))) {
    const lines = readFileSync(f, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").split("\n");
    lines.forEach((line, i) => { if (ACCENT_TEXT.test(line)) bad.push(`${relative(SRC, f)}:${i + 1}: ${line.trim()}`); });
  }
  expect(bad).toEqual([]);
});
