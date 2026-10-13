/**
 * Узкая лента «Расшифровки» (0.6.1): ширину меряет сама лента (`.transcript` — контейнер), а не
 * окно: при открытой панели «Спикеры» лента уже окна. Уже 900 px — имя и время строкой над
 * текстом; уже 700 px — карта разметки скрыта. В jsdom раскладки нет — проверяем правила.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

const css = (name: string) => readFileSync(join(__dirname, name), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
/** Тело блока `@container (max-width: N px)` — все такие блоки файла подряд. */
function container(text: string, px: number): string {
  const out: string[] = [];
  const head = new RegExp(`@container\\s*\\(max-width:\\s*${px}px\\)\\s*\\{`, "g");
  for (const m of text.matchAll(head)) {
    let depth = 1;
    let i = m.index! + m[0].length;
    const from = i;
    for (; i < text.length && depth > 0; i++) depth += text[i] === "{" ? 1 : text[i] === "}" ? -1 : 0;
    out.push(text.slice(from, i - 1));
  }
  return out.join("\n");
}

test("лента — контейнер своей ширины", () => {
  expect(css("card.css")).toMatch(/\.transcript\s*\{[^}]*container-type:\s*inline-size/);
});

test("уже 900 px: реплика в одну колонку, имя и время — строкой над текстом", () => {
  const narrow = container(css("card.css"), 900);
  expect(narrow).toMatch(/\.turn\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  expect(narrow).toMatch(/\.turn__gutter\s*\{[^}]*flex-direction:\s*row/);
  // «… N реплик» — без отступа под левое поле, которого больше нет.
  expect(container(css("markup.css"), 900)).toMatch(/\.turns-more\s*\{[^}]*padding-left:\s*0/);
});

test("уже 700 px: карты разметки нет", () => {
  expect(container(css("markupMap.css"), 700)).toMatch(/\.markup-map\s*\{[^}]*display:\s*none/);
});
