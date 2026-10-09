// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Правки главного окна из ревью 0.4 (этап 3, задача 10), которые видны только в CSS:
 * доступность счётчика поиска, строка поиска в узкой карточке, токены шрифта,
 * спокойное движение, рамка фокуса вкладок, слои тостов и листов, поля окна группы.
 */
const SRC = join(process.cwd(), "src");
const read = (...p: string[]) => readFileSync(join(SRC, ...p), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
/** Тело правила с точным селектором `sel` (первое вхождение). */
function rule(css: string, sel: string): string {
  const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = css.match(new RegExp(`(?:^|[}\\s])${esc}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`нет правила ${sel}`);
  return m[1] ?? "";
}
const px = (v: string) => Number(/(\d+(?:\.\d+)?)px/.exec(v)?.[1] ?? NaN);

test("пункт списка с пояснением: название целиком, пояснение переносится (0.5.1, «Л.» вместо «Личный»)", () => {
  const css = read("ui", "select.css");
  const label = rule(css, ".menu.selectbox__menu li .selectbox__label");
  expect(label).toMatch(/flex:\s*none/);
  expect(label).toMatch(/overflow:\s*visible/);
  const detail = rule(css, ".menu.selectbox__menu li small");
  expect(detail).toMatch(/min-width:\s*0/);
  expect(detail).toMatch(/white-space:\s*normal/);
});

test("счётчик совпадений (aria-live) не прячется display: none, когда пуст", () => {
  const css = read("features", "card", "card.css");
  expect(css).not.toMatch(/\.find__count:empty/);
});

test("строка поиска не переносится: поле сжимается, липкие панели не заезжают на второй ряд", () => {
  const css = read("features", "card", "card.css");
  expect(rule(css, ".find")).toMatch(/flex-wrap: nowrap/);
  expect(rule(css, ".find__search")).toMatch(/min-width: 0/);
  // Обёртки ✦ «Улучшить» больше нет: причину недоступности держит сама кнопка.
  expect(css).not.toMatch(/\.find__improve/);
});

test("мелкие подписи ленты и плеера — токеном шрифта, без литерала 11px", () => {
  const markup = read("features", "card", "markup.css");
  for (const sel of [".jtask__ref", ".jtask__more", ".insight__ref"]) {
    expect(rule(markup, sel)).toMatch(/font-size: var\(--text-xs\)/);
  }
  expect(rule(read("features", "card", "player.css"), ".pbar__label")).toMatch(/font-size: var\(--text-xs\)/);
});

test("крутилка плеера стоит при «уменьшить движение» и при паузе сияния", () => {
  const css = read("features", "card", "player.css");
  expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{[^@]*\.player__spinner \{ animation: none; \}/);
  expect(css).toMatch(/\[data-motion="paused"\] \.player__spinner \{ animation: none; \}/);
});

test("контекст агента («transcript.md · summary.md») — моноширинным", () => {
  expect(rule(read("features", "card", "agent.css"), ".agent__context")).toMatch(/font-family: var\(--font-code\)/);
});

test("мастер: будущие шаги на сиянии читаются (--ink-2), список шагов прокручивается", () => {
  const css = read("features", "wizard", "wizard.css");
  expect(rule(css, ".wizard__steps li")).toMatch(/color: var\(--ink-2\)/);
  expect(rule(css, ".wizard__num")).toMatch(/color: var\(--ink-2\)/);
  // Прокрутка — у списка: у колонки (.aurora-wash) отсвет шире её самой — дал бы лишнюю прокрутку.
  expect(rule(css, ".wizard__steps")).toMatch(/min-height: 0; overflow-y: auto/);
  expect(rule(css, ".wizard__side")).not.toMatch(/overflow/);
});

test("вкладки карточки: рамка фокуса вкладки помещается в поля прокручиваемого списка", () => {
  // Толщина кольца — самая большая из тем (тёмная и светлая).
  const rings = [...read("theme", "aurora", "tokens.css").matchAll(/--focus-ring: 0 0 0 (\d+)px/g)].map((m) => Number(m[1]));
  expect(rings.length).toBeGreaterThan(0);
  const ring = Math.max(...rings);
  // Aurora `.tabs` — поля 3px вокруг кнопок: кольцо (2px) рисуется в них, а прокрутка
  // (`overflow-x: auto` у .card-tabs__list) режет только по краю полей.
  const tabs = rule(read("theme", "aurora", "controls.css"), ".tabs");
  expect(px(tabs.match(/padding: ([^;]+)/)?.[1] ?? "")).toBeGreaterThanOrEqual(ring);
  const list = rule(read("features", "card", "assistant.css"), ".card-tabs__list");
  expect(list).toMatch(/overflow-x: auto/);
  expect(list).not.toMatch(/padding/);
});

test("тост — выше затемнения и листов (improve, переразделение, группа, подтверждение)", () => {
  const tokens = rule(read("theme", "aurora", "tokens.css"), ":root");
  const z = (name: string) => Number(tokens.match(new RegExp(`--z-${name}: (\\d+)`))?.[1]);
  for (const layer of ["overlay", "modal", "popover", "palette"]) expect(z("toast")).toBeGreaterThan(z(layer));
  expect(rule(read("theme", "aurora", "overlays.css"), ".toasts")).toMatch(/z-index: var\(--z-toast\)/);
  // Свой z-index у листа или его слоя поднял бы его над тостом.
  for (const f of [["features", "card", "improve.css"], ["features", "card", "rediarize.css"],
    ["features", "groups", "groups.css"], ["ui", "primitives.css"]]) {
    const css = read(...f);
    expect(css).not.toMatch(/\.(sheet|backdrop|confirm-layer|improve|redia|group-dialog)[^{]*\{[^}]*z-index/);
  }
});

test("окно группы: поля и зазоры — по сетке Aurora (--s-*)", () => {
  const css = read("features", "groups", "groups.css");
  for (const sel of [".group-dialog form", ".group-dialog__label", ".group-dialog__palette"]) {
    expect(rule(css, sel)).toMatch(/gap: var\(--s-\d\)/);
  }
});
