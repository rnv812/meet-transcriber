// @vitest-environment node
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const theme = join(process.cwd(), "src", "theme");
const read = (...p: string[]) => readFileSync(join(theme, ...p), "utf8");
/** Имена переменных, объявленных в файле (`--name:`). */
// (?<![\w-]) — не модификатор класса: `.btn--primary:hover` переменной не объявляет.
const declared = (css: string) =>
  new Set([...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/(?<![\w-])(--[\w-]+)\s*:/g)].map((m) => m[1]));

test("токены Atlas Aurora перенесены целиком: обе темы, шрифты и палитры", () => {
  const tokens = read("aurora", "tokens.css");
  expect(tokens).toMatch(/^\/\* Atlas Aurora — generated from tokens\.json \*\//);
  expect(tokens).toContain('[data-theme="light"]');
  for (const m of tokens.matchAll(/url\('([^']+)'\)/g)) {
    expect(existsSync(join(theme, "aurora", m[1]!))).toBe(true);
  }
  const palettes = read("aurora", "palettes.css");
  for (const p of ["violet", "green", "blue", "red", "amber"]) expect(palettes).toContain(`[data-aurora='${p}']`);
  // Селекторы [data-aurora-style=…] в дизайн-системе привязаны к классам .aurora (раздел
  // компонентов, не переносится); здесь — переменные обоих видов: «сияние» и «волны».
  expect(palettes).toContain("data-aurora-style");
  for (const v of ["--aurora:", "--aurora-blobs:"]) expect(palettes).toContain(v);
});

test("палитры — только переменные: классов компонентов этап 1 не приносит", () => {
  const css = read("aurora", "palettes.css").replace(/\/\*[\s\S]*?\*\//g, "");
  expect(css).not.toMatch(/(^|[\s,}])\.[a-z][\w-]*/m);
});

test("окно не переопределяет токены Aurora: свои имена — только псевдонимы", () => {
  const aurora = new Set([...declared(read("aurora", "tokens.css")), ...declared(read("aurora", "palettes.css"))]);
  for (const file of ["legacy-aliases.css", "tokens.css"]) {
    const own = [...declared(read(file))].filter((name) => aurora.has(name));
    expect(own, file).toEqual([]);
  }
});

test("псевдонимы перехода 0.4 — в legacy-aliases.css, не в tokens.css", () => {
  const tokens = read("tokens.css").replace(/\/\*[\s\S]*?\*\//g, "");
  expect(tokens).not.toMatch(/(?:^|\n)(:root|\[data-theme="light"\])\s*\{/);
  const aliases = declared(read("legacy-aliases.css"));
  for (const name of ["--bg", "--text", "--sb-thumb"]) expect(aliases.has(name), name).toBe(true);
});

const cssImports = (src: string) => [...src.matchAll(/import "([^"]+\.css)";/g)].map((m) => m[1]!);

test("все три окна подключают основу Aurora раньше своих стилей, псевдонимы — сразу после неё", () => {
  for (const entry of ["main.tsx", join("live", "main.tsx"), join("tray", "main.tsx")]) {
    const css = cssImports(readFileSync(join(process.cwd(), "src", entry), "utf8"));
    expect(css[0], entry).toMatch(/theme\/aurora\/index\.css$/);
    // Панель трея рисует себя своими --tp-* и прежних имён не знает.
    if (entry.startsWith("tray")) expect(css.some((c) => c.endsWith("legacy-aliases.css")), entry).toBe(false);
    else expect(css[1], entry).toMatch(/theme\/legacy-aliases\.css$/);
  }
});

test("шрифт Onest из npm больше не подключается", () => {
  const pkg = JSON.parse(readFileSync(join(process.cwd(), "package.json"), "utf8"));
  expect(pkg.dependencies?.["@fontsource-variable/onest"]).toBeUndefined();
  expect(readdirSync(join(theme, "aurora", "fonts")).filter((f) => f.endsWith(".woff2"))).toHaveLength(13);
});

test("псевдонимы окна ссылаются только на объявленные переменные", () => {
  const own = read("legacy-aliases.css").replace(/\/\*[\s\S]*?\*\//g, "");
  const known = new Set([
    ...declared(read("aurora", "tokens.css")),
    ...declared(read("aurora", "palettes.css")),
    ...declared(own),
  ]);
  const blocks = [...own.matchAll(/(?:^|\n)(:root|\[data-theme="light"\])\s*\{([^}]*)\}/g)];
  expect(blocks.length).toBeGreaterThanOrEqual(2);
  const missing = blocks.flatMap((b) => [...b[2]!.matchAll(/var\((--[\w-]+)/g)].map((m) => m[1]!)).filter((n) => !known.has(n));
  expect(missing).toEqual([]);
});

test("сборка не встраивает шрифты в CSS: CSP окна не пускает data:-URI", async () => {
  const { default: config } = await import("../../vite.config");
  const limit = config.build?.assetsInlineLimit;
  expect(typeof limit).toBe("function");
  const inline = limit as (file: string, content: Buffer) => boolean | undefined;
  expect(inline("/x/aurora/fonts/unbounded-cyrillic-ext.woff2", Buffer.alloc(1840))).toBe(false);
  expect(inline("/x/icon.svg", Buffer.alloc(100))).toBeUndefined(); // остальное — по умолчанию Vite
});
