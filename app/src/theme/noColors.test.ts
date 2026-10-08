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
