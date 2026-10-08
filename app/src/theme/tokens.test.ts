// @vitest-environment node
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";

/**
 * Каждая переменная `var(--имя)` в CSS окна объявлена: либо `--имя:` в каком-то
 * CSS под src, либо задаётся из кода (`style={{ "--имя": … }}`, `setProperty("--имя"`).
 * Необъявленная переменная молча делает свойство недействительным (так заголовок
 * «Голоса» остался шрифтом по умолчанию из-за несуществующего --text-xl).
 */
const SRC = join(process.cwd(), "src");
/** Имя → причина, по которой оно объявляется не в CSS и не из кода окна. */
const EXCEPTIONS: Record<string, string> = {
  "--edge-bg": "необязательный крючок скопированной дизайн-системы (.aurora-edge): задаётся на элементе, иначе запасное --surface-1",
};

function walk(dir: string, ext: RegExp, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) walk(p, ext, out);
    else if (ext.test(e.name)) out.push(p);
  }
  return out;
}
/** Комментарии вырезаются, переводы строк остаются: номера строк в сообщении верны. */
const strip = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ""));

function declaredNames(): Set<string> {
  const names = new Set<string>();
  for (const f of walk(SRC, /\.css$/)) {
    // (?<![\w-]) — не модификатор класса вроде `.btn--primary:hover`.
    for (const m of strip(readFileSync(f, "utf8")).matchAll(/(?<![\w-])(--[\w-]+)\s*:/g)) names.add(m[1]!);
  }
  for (const f of walk(SRC, /\.tsx?$/)) {
    const src = readFileSync(f, "utf8");
    // Имя в строковом литерале кода: `style={{ "--a": … }}`, `["--a" as string]`, `setProperty("--a"`,
    // `cssVar="--spk-w"` у PaneResizer — во всех случаях переменную ставит код.
    for (const m of src.matchAll(/["'`](--[\w-]+)["'`]/g)) names.add(m[1]!);
  }
  return names;
}

test("разбор: объявление и использование переменных находятся", () => {
  expect([...strip("a { --x: 1 } /* var(--no) */ b { c: var(--x) }").matchAll(/var\(\s*(--[\w-]+)/g)].map((m) => m[1]))
    .toEqual(["--x"]);
});

test("все var(--…) в CSS окна объявлены в CSS или заданы из кода", () => {
  const declared = declaredNames();
  const missing: string[] = [];
  for (const f of walk(SRC, /\.css$/)) {
    const rel = relative(SRC, f);
    strip(readFileSync(f, "utf8")).split("\n").forEach((line, i) => {
      for (const m of line.matchAll(/var\(\s*(--[\w-]+)/g)) {
        const name = m[1]!;
        if (!declared.has(name) && !EXCEPTIONS[name]) missing.push(`${rel}:${i + 1}: ${name}`);
      }
    });
  }
  expect(missing).toEqual([]);
});
