import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";

/**
 * Классы окна, совпадавшие с классами Atlas Aurora по имени, но с другим
 * смыслом, переименованы (этап 2 0.4). Старые имена больше не встречаются ни
 * в CSS окна (кроме скопированной дизайн-системы), ни в className разметки.
 */
const SRC = join(process.cwd(), "src");
const OLD = ["tabs", "card", "search", "help", "empty"];

function files(dir: string, ext: string[], out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) files(p, ext, out);
    else if (ext.some((x) => e.name.endsWith(x)) && !e.name.includes(".test.")) out.push(p);
  }
  return out;
}
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, "");

test("CSS окна не объявляет прежних блоков tabs/card/search/help/empty", () => {
  const bad: string[] = [];
  for (const f of files(SRC, [".css"])) {
    if (relative(SRC, f).startsWith(join("theme", "aurora"))) continue;
    const css = strip(readFileSync(f, "utf8"));
    for (const name of OLD) {
      // .name как отдельный класс: после — не буква, цифра, «-» или «_».
      if (new RegExp(`\\.${name}(?![\\w-])`).test(css)) bad.push(`${relative(SRC, f)}: .${name}`);
    }
  }
  expect(bad).toEqual([]);
});

test("разметка не использует прежние классы tabs/card/search/help/empty", () => {
  const bad: string[] = [];
  for (const f of files(SRC, [".tsx", ".ts"])) {
    const src = readFileSync(f, "utf8");
    for (const m of src.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
      const tokens = (m[1] ?? m[2] ?? "").split(/\s+/);
      for (const name of OLD) if (tokens.includes(name)) bad.push(`${relative(SRC, f)}: ${name}`);
    }
  }
  expect(bad).toEqual([]);
});
