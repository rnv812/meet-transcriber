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

test("окно не переопределяет токены Aurora: объявляет только свои имена", () => {
  const aurora = new Set([...declared(read("aurora", "tokens.css")), ...declared(read("aurora", "palettes.css"))]);
  for (const file of ["scrollbars.css", "tokens.css"]) {
    const own = [...declared(read(file))].filter((name) => aurora.has(name));
    expect(own, file).toEqual([]);
  }
});

test("значения тем у окна — только ползунки прокрутки в scrollbars.css; tokens.css тем не объявляет", () => {
  const tokens = read("tokens.css").replace(/\/\*[\s\S]*?\*\//g, "");
  expect(tokens).not.toMatch(/(?:^|\n)(:root|\[data-theme="light"\])\s*\{/);
  const bars = declared(read("scrollbars.css"));
  for (const name of ["--sb-thumb", "--sb-thumb-area", "--sb-thumb-hover", "--sb-thumb-active"]) {
    expect(bars.has(name), name).toBe(true);
  }
});

const cssImports = (src: string) => [...src.matchAll(/import "([^"]+\.css)";/g)].map((m) => m[1]!);

test("все три окна подключают основу Aurora раньше своих стилей, ползунки — сразу после неё", () => {
  for (const entry of ["main.tsx", join("live", "main.tsx"), join("tray", "main.tsx")]) {
    const css = cssImports(readFileSync(join(process.cwd(), "src", entry), "utf8"));
    expect(css[0], entry).toMatch(/theme\/aurora\/index\.css$/);
    // Псевдонимов прежних имён (до этапа 7) нет ни в одном окне.
    expect(css.some((c) => c.endsWith("legacy-aliases.css")), entry).toBe(false);
    // Панель трея — без полос прокрутки окна (своих стилей окна она не берёт).
    if (!entry.startsWith("tray")) expect(css[1], entry).toMatch(/theme\/scrollbars\.css$/);
  }
});

test("шрифт Onest из npm больше не подключается", () => {
  const pkg = JSON.parse(readFileSync(join(process.cwd(), "package.json"), "utf8"));
  expect(pkg.dependencies?.["@fontsource-variable/onest"]).toBeUndefined();
  expect(readdirSync(join(theme, "aurora", "fonts")).filter((f) => f.endsWith(".woff2"))).toHaveLength(13);
});

type Theme = ":root" | '[data-theme="light"]';

/** Яркость `--ink-3` темы в токенах Aurora: блок с `:root` (тёмная) или `[data-theme="light"]` в списке селекторов. */
function ink3(theme: Theme): string | undefined {
  const css = read("aurora", "tokens.css").replace(/\/\*[\s\S]*?\*\//g, "");
  return [...css.matchAll(/(?:^|\n)([^{}\n]+)\{([^}]*)\}/g)]
    .filter((b) => b[1]!.split(",").some((s) => s.trim() === theme))
    .map((b) => /--ink-3:\s*oklch\(([\d.]+%) 0 0\)/.exec(b[2]!)?.[1])
    .find(Boolean);
}

test("ползунки прокрутки — серый яркости --ink-3 своей темы с прозрачностью; в обеих темах те же имена", () => {
  const css = read("scrollbars.css").replace(/\/\*[\s\S]*?\*\//g, "");
  const blocks = [...css.matchAll(/(?:^|\n)(:root|\[data-theme="light"\])\s*\{([^}]*)\}/g)];
  expect(blocks.map((b) => b[1])).toEqual([":root", '[data-theme="light"]']);
  for (const [, theme, body] of blocks) {
    const decls = [...body!.matchAll(/(--[\w-]+):\s*oklch\(([\d.]+%) 0 0 \/ ([\d.]+)\)/g)];
    expect(decls.map((d) => d[1]), theme).toEqual(["--sb-thumb", "--sb-thumb-area", "--sb-thumb-hover", "--sb-thumb-active"]);
    const l = ink3(theme as Theme);
    expect(l, theme).toBeTruthy();
    for (const d of decls) expect(d[2], `${theme} ${d[1]}`).toBe(l);
  }
});

test("компоненты Aurora: base, aurora, controls, feedback, overlays и data перенесены скриптом и подключены после палитр", () => {
  for (const name of ["base", "aurora", "controls", "feedback", "overlays", "data"]) {
    const css = read("aurora", `${name}.css`);
    expect(css.split("\n")[0]).toMatch(new RegExp(`^/\\* Atlas Aurora v2\\.6 — ${name}: `));
  }
  const index = read("aurora", "index.css");
  const order = ["tokens.css", "palettes.css", "base.css", "aurora.css", "controls.css", "feedback.css", "overlays.css", "data.css", "../aurora-fixes.css", "../aurora-fallbacks.css"]
    .map((f) => index.indexOf(`"./${f}"`.replace("./../", "../")));
  expect(order.every((i) => i >= 0)).toBe(true);
  expect([...order].sort((a, b) => a - b)).toEqual(order);
});

test("«Блок кода» (терминал вкладки «Агент») перенесён скриптом в data.css вместе с «Таблицей»", () => {
  const css = read("aurora", "data.css");
  expect(css.split("\n")[0]).toContain("data: Таблица, Блок кода");
  expect(css).toContain("/* ── Блок кода ── */");
  expect(css).toMatch(/\.codeblock \{[^}]*background: var\(--code-bg\)/);
  expect(css).toMatch(/\.code-head \{/);
});

test("правки каскада Aurora: размеры select-btn--md/--sm сильнее «.select-btn» из overlays.css", () => {
  const css = read("aurora-fixes.css");
  expect(css).toMatch(/\.select-btn\.select-btn--md \{[^}]*height: var\(--control\);/);
  expect(css).toMatch(/\.select-btn\.select-btn--sm \{[^}]*height: var\(--control-sm\);/);
  // Скопированные файлы дизайн-системы не правятся.
  expect(read("aurora", "overlays.css")).not.toMatch(/select-btn--md/);
});

test("запасные значения без color-mix(): файл окна, под @supports not", () => {
  const css = read("aurora-fallbacks.css");
  expect(css).toContain("@supports not (color: color-mix(in oklab, red, blue))");
  // Запрос возможности допустим, само значение с color-mix() в правилах — нет.
  const rules = css.replace(/\/\*[\s\S]*?\*\//g, "").replace("@supports not (color: color-mix(in oklab, red, blue))", "");
  expect(rules).not.toMatch(/color-mix\(/);
});

test("запасные значения покрывают кнопки с color-mix()", () => {
  const css = read("aurora-fallbacks.css");
  for (const sel of [".btn--primary {", ".btn--primary:hover", ".btn--aurora", ".btn--tonal:hover", ".btn--deep:hover", ".btn--danger {"]) {
    expect(css, sel).toContain(sel);
  }
});

/*
 * Переменные палитр на color-mix(): без него (macOS 13) var() такой переменной
 * недействителен там, где она подставлена, — фон прозрачный (наведение главной
 * кнопки, ::selection, --accent-soft окна). В @supports not у aurora-fallbacks.css
 * каждая из них получает сплошное значение — кроме перечисленных здесь.
 */
const MIX_EXEMPT = new Map<string, string>([
  // Только свечение в списке теней (`box-shadow: inset …, var(--aurora-edge-glow)`):
  // `none` в середине списка недопустим; у пользователей переменной (.btn--aurora)
  // запасные правила сами снимают тень.
  ["--aurora-edge-glow", "свечение: тень .btn--aurora снята запасным правилом"],
]);

const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

/** Объявления `--имя: значение` с color-mix() в значении: имя → селекторы правил. */
function mixedTokens(css: string): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const m of stripComments(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    for (const decl of (m[2] ?? "").split(";")) {
      const d = /^\s*(--[\w-]+)\s*:([\s\S]*)$/.exec(decl);
      if (!d || !d[2]!.includes("color-mix(")) continue;
      out.set(d[1]!, [...(out.get(d[1]!) ?? []), (m[1] ?? "").trim()]);
    }
  }
  return out;
}

/** Содержимое блока `@supports not (color: color-mix(…))` запасных значений. */
function fallbackBlock(css: string): string {
  const head = "@supports not (color: color-mix(in oklab, red, blue))";
  const text = stripComments(css);
  const start = text.indexOf("{", text.indexOf(head) + head.length);
  let depth = 0;
  for (let i = start; i < text.length; i++) {
    if (text[i] === "{") depth++;
    else if (text[i] === "}" && --depth === 0) return text.slice(start + 1, i);
  }
  return "";
}

/** Специфичность одной части селектора: [id, класс/атрибут/псевдокласс, элемент]. */
function specificity(sel: string): [number, number, number] {
  const s = sel.replace(/::[\w-]+/g, "");
  const ids = (s.match(/#[\w-]+/g) ?? []).length;
  const mid = (s.match(/\.[\w-]+|\[[^\]]*\]|:[\w-]+/g) ?? []).length;
  const el = (s.match(/(?:^|[\s>+~])[a-z][\w-]*/g) ?? []).length;
  return [ids, mid, el];
}
const notWeaker = (a: number[], b: number[]) => {
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i]! > b[i]!;
  return true;
};

/**
 * Что не перекрыто: переменная без запасного значения или селектор палитры,
 * которого запасное правило не перебивает (часть того же вида — с потомком или
 * без — и не слабее; файл запасных значений подключён позже палитр).
 */
function mixGaps(palettes: string, fallbacks: string): string[] {
  const block = fallbackBlock(fallbacks);
  const own = new Map<string, string[]>();
  for (const m of block.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    for (const name of declared(m[2] ?? "")) {
      own.set(name!, [...(own.get(name!) ?? []), ...(m[1] ?? "").split(",").map((p) => p.trim())]);
    }
  }
  const gaps: string[] = [];
  for (const [name, selectors] of mixedTokens(palettes)) {
    if (MIX_EXEMPT.has(name)) continue;
    const parts = own.get(name);
    if (!parts) { gaps.push(name); continue; }
    for (const p of selectors.flatMap((s) => s.split(",").map((x) => x.trim()))) {
      const nested = /\S\s+\S/.test(p);
      const beaten = parts.some((f) => /\S\s+\S/.test(f) === nested && notWeaker(specificity(f), specificity(p)));
      if (!beaten) gaps.push(`${name} @ ${p}`);
    }
  }
  return gaps;
}

test("разбор запасных переменных: пропуск и слабый селектор находит", () => {
  const pal = ":root, [data-aurora] { --a: color-mix(in oklab, red, blue); --b: red }\n[data-theme='light'] [data-aurora='x'] { --a: color-mix(in oklab, red, blue) }";
  expect(mixGaps(pal, "/* */")).toEqual(["--a"]);
  const weak = "@supports not (color: color-mix(in oklab, red, blue)) { :root, [data-aurora], [data-theme] [data-aurora] { --a: red } }";
  expect(mixGaps(pal, weak)).toEqual([]);
  const missing = "@supports not (color: color-mix(in oklab, red, blue)) { :root, [data-aurora] { --a: red } }";
  expect(mixGaps(pal, missing)).toEqual(["--a @ [data-theme='light'] [data-aurora='x']"]);
  expect(specificity("html[data-theme][data-aurora]")).toEqual([0, 2, 1]);
  expect(specificity(":root")).toEqual([0, 1, 0]);
});

test("каждая переменная палитр на color-mix() получает сплошное значение без color-mix()", () => {
  const palettes = read("aurora", "palettes.css");
  // Ожидаемые: без них главная кнопка при наведении и выделение текста прозрачны.
  const mixed = mixedTokens(palettes);
  for (const name of ["--accent-strong-hover", "--accent-soft", "--selection", "--accent-hover", "--accent-press", "--mark-bg", "--scrim-tint"]) {
    expect(mixed.has(name), name).toBe(true);
  }
  expect(mixGaps(palettes, read("aurora-fallbacks.css"))).toEqual([]);
});

test("запасные значения покрывают фон под слоями", () => {
  const css = read("aurora-fallbacks.css");
  expect(css).toContain("dialog::backdrop");
  expect(css).toContain(".backdrop {");
});

test("прозрачные окна (live, tray) сбрасывают фон body из «Базы» Aurora", () => {
  const live = readFileSync(join(process.cwd(), "src", "live", "panel.css"), "utf8");
  const tray = readFileSync(join(process.cwd(), "src", "tray", "tray.css"), "utf8");
  for (const css of [live, tray]) {
    // panel.css: «html, body { background: transparent; }»; tray.css: «html, body, #root { … background: transparent; }»
    expect(css).toMatch(/\bbody\b[^{}]*\{[^}]*background:\s*transparent/);
  }
});

test("сборка не встраивает шрифты в CSS: CSP окна не пускает data:-URI", async () => {
  const { default: config } = await import("../../vite.config");
  const limit = config.build?.assetsInlineLimit;
  expect(typeof limit).toBe("function");
  const inline = limit as (file: string, content: Buffer) => boolean | undefined;
  expect(inline("/x/aurora/fonts/unbounded-cyrillic-ext.woff2", Buffer.alloc(1840))).toBe(false);
  expect(inline("/x/icon.svg", Buffer.alloc(100))).toBeUndefined(); // остальное — по умолчанию Vite
});
