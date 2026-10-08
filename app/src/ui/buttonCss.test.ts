// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Недоступная кнопка принимает указатель (button.css: pointer-events: auto —
 * ради подсказки с причиной), поэтому `:hover` вариантов Aurora на ней
 * срабатывал бы. Наведение на неё вид не меняет: на каждый вариант с `:hover`
 * в controls.css — правило окна `button.btn--…:disabled:hover`.
 */
const src = join(process.cwd(), "src");
const strip = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");
const button = strip(readFileSync(join(src, "ui", "button.css"), "utf8"));
const controls = strip(readFileSync(join(src, "theme", "aurora", "controls.css"), "utf8"));

test("наведение на недоступную кнопку не меняет вид ни одного варианта", () => {
  const variants = new Set([...controls.matchAll(/\.btn--([\w-]+):hover/g)].map((m) => m[1]!));
  for (const v of ["primary", "outline", "ghost", "danger"]) expect(variants.has(v), v).toBe(true);
  variants.add("link"); // свой вариант окна
  const missing = [...variants].filter((v) => !button.includes(`button.btn--${v}:disabled:hover`));
  expect(missing).toEqual([]);
});

test("«опасная» кнопка-значок краснеет при наведении только доступной", () => {
  expect(button).toContain(".btn--ghost-danger:hover:not(:disabled)");
  expect(button).not.toMatch(/\.btn--ghost-danger:hover\s*[{,]/);
});

test("нажатая недоступная кнопка при наведении остаётся нажатой", () => {
  expect(button).toMatch(/button\.btn\[aria-pressed="true"\]:disabled:hover\s*\{[^}]*background:\s*var\(--surface-3\)/);
});

test("включённый «Не отвлекать» — акцентного цвета: правило карточки сильнее общего нажатого", () => {
  const card = strip(readFileSync(join(src, "features", "card", "card.css"), "utf8"));
  // Текст цвета акцента — текстовый токен Aurora `--accent-line` (читается и в светлой теме).
  expect(card).toMatch(/\.btn\.live-card__quiet\[aria-pressed="true"\]\s*\{[^}]*color:\s*var\(--accent-line\)/);
});

test("знак агента в кнопке — цвета подписи (на сиянии btn--aurora свой --ink-2 не читается)", () => {
  expect(button).toMatch(/\.btn \.agent-mark \{ color: inherit; \}/);
});
