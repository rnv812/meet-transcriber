import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";

/**
 * Все стили окна — только на токенах Atlas Aurora: свой цвет не меняется
 * вместе с темой и палитрой. Литерал цвета в CSS окна вне скопированной
 * дизайн-системы (theme/aurora/) и запасных значений без color-mix()
 * (theme/aurora-fallbacks.css) — ошибка; если он нужен, — исключение ниже с причиной.
 */
const SRC = join(process.cwd(), "src");
/** Путь от src через «/» → причина. */
const EXCEPTIONS: Record<string, string> = {
  "theme/scrollbars.css":
    "ползунки прокрутки — серый яркости --ink-3 с прозрачностью: токена с альфой у Aurora нет, а color-mix() " +
    "и относительного цвета нет в WebKit macOS 13.0 (Safari 16.1)",
};
/** Не окна: копия дизайн-системы и её запасные значения для WebKit без color-mix(). */
const isAurora = (rel: string) => rel.startsWith("theme/aurora/") || rel === "theme/aurora-fallbacks.css";
const COLOR = /#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(/i;

function css(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) css(p, out);
    else if (e.name.endsWith(".css")) out.push(p);
  }
  return out;
}
/** Путь от src с «/» на любой ОС: ключи исключений одинаковы на Windows и macOS. */
const relOf = (f: string) => relative(SRC, f).split(sep).join("/");
const readRel = (rel: string) => readFileSync(join(SRC, ...rel.split("/")), "utf8");
/** Комментарии вырезаются, переводы строк остаются: номера строк в сообщении верны. */
const strip = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ""));

function offending(re: RegExp, rel: string, text: string): string[] {
  const bad: string[] = [];
  strip(text).split("\n").forEach((line, i) => { if (re.test(line)) bad.push(`${rel}:${i + 1}: ${line.trim()}`); });
  return bad;
}

test("разбор: литерал после многострочного комментария — с верным номером строки, в комментарии — не литерал", () => {
  const text = "/* #fff\n   rgb(0 0 0) */\na { color: var(--ink); }\nb { color: #123456; }\n";
  expect(offending(COLOR, "x.css", text)).toEqual(["x.css:4: b { color: #123456; }"]);
  expect(offending(COLOR, "x.css", "a { white-space: nowrap; color: var(--ink-2) }")).toEqual([]);
});

test("исключения — существующие файлы окна, не дизайн-системы, путь через «/»", () => {
  for (const rel of Object.keys(EXCEPTIONS)) {
    expect(rel, rel).not.toContain("\\");
    expect(isAurora(rel), rel).toBe(false);
    expect(existsSync(join(SRC, ...rel.split("/"))), rel).toBe(true);
  }
});

test("CSS окна вне theme/aurora/ и aurora-fallbacks.css — без литералов цвета", () => {
  const files = css(SRC).map(relOf).filter((rel) => !isAurora(rel));
  // Проверка видит всё окно, а не только ui/: тему, карточку, настройки, живую панель, панель трея.
  expect(files).toEqual(expect.arrayContaining(["theme/tokens.css", "ui/category.css", "features/settings/ownv.css",
    "live/chat.css", "tray/tray.css"]));
  const bad = files.filter((rel) => !EXCEPTIONS[rel]).flatMap((rel) => offending(COLOR, rel, readRel(rel)));
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
  const bad = css(join(SRC, "ui")).map(relOf).flatMap((rel) => offending(ACCENT_TEXT, rel, readRel(rel)));
  expect(bad).toEqual([]);
});
