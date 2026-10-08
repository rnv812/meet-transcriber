import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

/**
 * Все стили окна попадают в одну таблицу: блок, объявленный в двух файлах
 * (`.split` у кнопки «Начать запись» и у «Разделить спикера»), молча ломает
 * один из них — смотря какой файл загрузился позже. Корневой класс блока
 * объявляется ровно в одном файле.
 */
function cssFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? cssFiles(join(dir, e.name)) : e.name.endsWith(".css") ? [join(dir, e.name)] : [],
  );
}

/** Запасные значения для WebKit без color-mix(): по замыслу переопределяют блоки
 *  скопированной дизайн-системы и своих блоков не объявляют (проверка ниже). */
const FALLBACKS = join("theme", "aurora-fallbacks.css");

test("a CSS block class is declared in one file only", () => {
  const src = join(process.cwd(), "src");
  const owners = new Map<string, Set<string>>();
  for (const file of cssFiles(src)) {
    if (relative(src, file) === FALLBACKS) continue;
    const text = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    for (const m of text.matchAll(/(?:^|})\s*([^{}@]+)\{/g)) {
      for (const part of (m[1] ?? "").split(",")) {
        const name = /^\s*\.([A-Za-z][\w-]*)\s*$/.exec(part)?.[1];
        if (!name) continue;
        const set = owners.get(name) ?? new Set<string>();
        set.add(relative(src, file));
        owners.set(name, set);
      }
    }
  }
  const clashes = [...owners].filter(([, files]) => files.size > 1).map(([name, files]) => `${name}: ${[...files].join(", ")}`);
  expect(clashes).toEqual([]);
});

test("запасные значения переопределяют только блоки скопированной дизайн-системы", () => {
  const src = join(process.cwd(), "src");
  const vendored = new Set(cssFiles(join(src, "theme", "aurora"))
    .flatMap((f) => [...readFileSync(f, "utf8").matchAll(/\.([A-Za-z][\w-]*)/g)].map((m) => m[1]!)));
  const css = readFileSync(join(src, FALLBACKS), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  // Первый класс каждого селектора правила (после «{», «}» или «,»).
  const roots = [...css.matchAll(/(?:^|[{},])\s*\.([A-Za-z][\w-]*)/g)].map((m) => m[1]!);
  expect(roots.length).toBeGreaterThan(0);
  expect(roots.filter((name) => !vendored.has(name))).toEqual([]);
});

test("карточка человека в «Голосах» — колонка рядом с сеткой: не наезжает на неё и не выходит за край", () => {
  const css = readFileSync(join(process.cwd(), "src", "features", "voices", "voices.css"), "utf8");
  const card = /\.voices__card \{([^}]*)\}/.exec(css)?.[1] ?? "";
  const main = /\.voices__main \{([^}]*)\}/.exec(css)?.[1] ?? "";
  expect(card).not.toMatch(/position: absolute/);
  expect(card).toMatch(/flex: none/);
  expect(card).toMatch(/width: var\(--person-w/);
  expect(card).toMatch(/max-width: calc\(100% - 200px\)/);
  // Сетка берёт остаток и перестраивается (auto-fill), а не уходит под панель.
  expect(main).toMatch(/flex: 1/);
  expect(main).toMatch(/min-width: 0/);
  expect(css).toMatch(/\.voices__grid \{[^}]*repeat\(auto-fill/);
});

test("«Фильтры»: измерение — и .cat-filter__list, но не сжимается: правило двух классов сильнее при любом порядке файлов", () => {
  // Окно грузит ui/category.css позже recordings.css: при равной специфичности его
  // `.cat-filter__list { max-height: 340px; overflow-y: auto }` побеждало, и измерения
  // в колонке .filters сжимались и наезжали друг на друга (ревью search-ui, C1).
  const src = join(process.cwd(), "src");
  const recordings = readFileSync(join(src, "features", "recordings", "recordings.css"), "utf8");
  const rule = /\.filters > \.filters__dim \{([^}]*)\}/.exec(recordings)?.[1] ?? "";
  expect(rule).toMatch(/flex: none/);
  expect(rule).toMatch(/max-height: none/);
  expect(rule).toMatch(/overflow: visible/);
  // Допущение правила: `.cat-filter__list` везде — одним классом (специфичность 0,1,0 < 0,2,0).
  const selectors = cssFiles(src).flatMap((file) => [...readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "")
    .matchAll(/(?:^|})\s*([^{}@]+)\{/g)].flatMap((m) => (m[1] ?? "").split(",").map((x) => x.trim())))
    .filter((sel) => sel.includes("cat-filter__list"));
  expect(selectors.length).toBeGreaterThan(0);
  expect(selectors.filter((sel) => sel !== ".cat-filter__list")).toEqual([]);
});

test("плотная кнопка (xs) — значок 14 px: правило окна сильнее «.btn svg» дизайн-системы", () => {
  const css = readFileSync(join(process.cwd(), "src", "ui", "button.css"), "utf8");
  expect(css).toMatch(/\.btn\[data-density="compact"\] svg \{[^}]*width: var\(--icon-sm\);[^}]*height: var\(--icon-sm\)/);
});

test("недоступная кнопка показывает подсказку: pointer-events возвращены сильнее «.btn:disabled» Aurora", () => {
  // controls.css: `.btn:disabled { pointer-events: none }` — title с причиной недоступности не всплывал,
  // а нажатие проваливалось к родителю. button.btn:disabled (0,2,1) сильнее при любом порядке файлов.
  const css = readFileSync(join(process.cwd(), "src", "ui", "button.css"), "utf8");
  expect(css).toMatch(/(?:^|\n)button\.btn:disabled \{[^}]*pointer-events: auto;[^}]*cursor: default;/);
});

test("ссылка-кнопка — шрифтом окружающего текста, а не 14 px «.btn» Aurora", () => {
  const css = readFileSync(join(process.cwd(), "src", "ui", "button.css"), "utf8");
  const link = /(?:^|\n)\.btn--link \{([^}]*)\}/.exec(css)?.[1] ?? "";
  expect(link).toMatch(/font: inherit;/);
  // Обход прежнего размера в настройках больше не нужен.
  const settings = readFileSync(join(process.cwd(), "src", "features", "settings", "settings.css"), "utf8");
  expect(settings).not.toMatch(/\.asr-fallback \.btn--link/);
});
