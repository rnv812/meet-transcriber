import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";

/**
 * Страж слов интерфейса (0.5). «Автомод» — словечко Claude Code: в окне —
 * «Действует сам» / «Спрашивает каждое». В комментариях и в ключевых словах
 * поиска настроек (синоним для тех, кто ищет по-старому) слово допустимо.
 */
const ROOT = join(process.cwd(), "src");

function sources(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) sources(p, out);
    else if (/\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)) out.push(p);
  }
  return out;
}

/** Код без комментариев и без строк `keywords: [...]`. */
export function visibleCode(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'`])\/\/.*$/gm, "$1")
    .replace(/keywords:\s*\[[^\]]*\]/g, "");
}

test("разбор: комментарии и ключевые слова не считаются, строки — считаются", () => {
  expect(visibleCode('/** автомод */ const a = 1; // автомод\nkeywords: ["автомод"]')).not.toMatch(/автомод/);
  expect(visibleCode('const t = "Автомод";')).toMatch(/Автомод/);
  expect(visibleCode('const u = "https://x.y/автомод";')).toMatch(/автомод/);
});

test("слова «автомод» нет в текстах окна", () => {
  const bad = sources(ROOT).filter((f) => /автомод/i.test(visibleCode(readFileSync(f, "utf8"))))
    .map((f) => relative(ROOT, f));
  expect(bad).toEqual([]);
});
