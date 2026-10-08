import { readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";

/**
 * Страж «плоско» (0.5): сияние — только на пустых экранах и в мастере первого
 * запуска; остальное окно без отсвета, кромки сияния, свечения и градиентов.
 * Исключения — списком с причиной.
 */
const ROOT = join(process.cwd(), "src");
const rel = (p: string) => relative(ROOT, p).split(sep).join("/");

function files(dir: string, ext: RegExp, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) { if (rel(p) !== "theme/aurora") files(p, ext, out); }
    else if (ext.test(e.name) && !/\.test\.tsx?$/.test(e.name)) out.push(p);
  }
  return out;
}
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

/** Где сияние в разметке — по замыслу. */
const GLOW_ALLOWED: Record<string, string> = {
  "features/recordings/LibraryEmpty.tsx": "пустая библиотека — полное сияние",
  "features/recordings/LibraryHome.tsx": "пустой экран (ничего не выбрано) — карточка «Новая встреча» на тихом сиянии",
  "features/wizard/Wizard.tsx": "мастер первого запуска",
};

/** Градиенты в стилях окна, которые не свечение. */
const GRADIENT_ALLOWED: Record<string, string> = {
  "features/card/player.css": "затухание текста к краю (маска прокрутки), не отсвет",
  "features/settings/appearance.css": "образцы палитр в «Оформлении» — показывают само сияние",
  "live/chat.css": "точки «пишет» и стекло поверх плотного фона (однотонный слой)",
  "live/panel.css": "стекло поверх плотного фона (однотонный слой)",
  "ui/primitives.css": "бегущий блик шкалы «работа идёт» — признак хода, не украшение",
  "ui/slider.css": "закрашенная часть дорожки ползунка (резкая граница, не переход)",
  "theme/aurora-fallbacks.css": "запасные значения самих токенов сияния для WebKit",
};

test("классы сияния в разметке — только на пустых экранах и в мастере", () => {
  const bad = files(ROOT, /\.tsx$/)
    .filter((f) => /className=\{?["'`][^"'`]*\baurora(-wash|-edge)?(?=["'`\s$])/.test(strip(readFileSync(f, "utf8"))))
    .map(rel).filter((f) => !(f in GLOW_ALLOWED));
  expect(bad).toEqual([]);
});

test("стили окна: без свечения сияния и без градиентов вне списка", () => {
  const glow: string[] = [];
  const gradients: string[] = [];
  for (const f of files(ROOT, /\.css$/)) {
    const css = strip(readFileSync(f, "utf8"));
    const name = rel(f);
    if (/--aurora-edge-glow|--aurora-line|--aurora-glow/.test(css) && name !== "theme/aurora-fallbacks.css") glow.push(name);
    if (/(linear|radial|conic)-gradient\(/.test(css) && !(name in GRADIENT_ALLOWED)) gradients.push(name);
  }
  expect(glow).toEqual([]);
  expect(gradients).toEqual([]);
});

test("кнопки плоские: главная без градиента и свечения, ИИ и тональная — мягкий акцент", () => {
  const css = strip(readFileSync(join(ROOT, "ui", "button.css"), "utf8"));
  expect(css).toMatch(/:root \.btn\.btn--primary\.btn--primary \{ --btn-g1: var\(--btn-g2\); --btn-g3: var\(--btn-g2\); \}/);
  expect(css).toMatch(/:root \.btn\.btn--primary\.btn--primary:not\(:focus-visible\),\s*:root \.btn\.btn--aurora\.btn--aurora:not\(:focus-visible\) \{ box-shadow: none; \}/);
  expect(css).toMatch(/:root \.btn\.btn--aurora\.btn--aurora \{ background: var\(--accent-soft\); color: var\(--accent-line\); \}/);
  expect(css).toMatch(/:root \.btn\.btn--tonal\.btn--tonal \{ background: var\(--accent-soft\); \}/);
});
