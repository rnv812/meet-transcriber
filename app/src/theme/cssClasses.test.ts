import { readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";

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

test("карточка человека в «Голосах» — колонка рядом с таблицей: не наезжает на неё и не выходит за край", () => {
  const css = readFileSync(join(process.cwd(), "src", "features", "voices", "voices.css"), "utf8");
  const card = /\.voices__card \{([^}]*)\}/.exec(css)?.[1] ?? "";
  const main = /\.voices__main \{([^}]*)\}/.exec(css)?.[1] ?? "";
  expect(card).not.toMatch(/position: absolute/);
  expect(card).toMatch(/flex: none/);
  expect(card).toMatch(/width: var\(--person-w/);
  expect(card).toMatch(/max-width: calc\(100% - 200px\)/);
  // Таблица берёт остаток и прокручивается внутри, а не уходит под панель.
  expect(main).toMatch(/flex: 1/);
  expect(main).toMatch(/min-width: 0/);
  expect(css).not.toMatch(/\.voices__grid/);
  expect(css).toMatch(/\.voices__table \{[^}]*overflow/);
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

/*
 * Системных контролов в окне нет (0.4, аудит доводки B9): список — ui/Select,
 * бегунок — ui/Slider, флажок и радио — классы Aurora `.cb`/`.rd` (или `.switch`).
 * Родной <select> рисует системную стрелку и список, `range` — синий бегунок,
 * флажок без класса — 13 px системы.
 *
 * NATIVE_ALLOWED — места, которые ещё не переведены (пакеты B, C, D доводки 0.4).
 * Перевёл место — убери строку: устаревшая строка роняет тест так же, как новое
 * нарушение. Пустой список — цель.
 */
const NATIVE_ALLOWED = new Set<string>([
  // Пакет B: голоса (перешли в пакет D).
  "features/voices/PersonCard.tsx: <select>",

  // Пакет C: настройки.
  "features/settings/AsrChoice.tsx: <select>",
  "features/settings/JiraSettings.tsx: <select>",
  "features/settings/LiveHintsRows.tsx: <select>",
  "features/settings/LocalModelRows.tsx: <select>",
  "features/settings/SoundSection.tsx: <select>",
  "features/settings/fields.tsx: range",
  "features/settings/SpeakersSection.tsx: range",
  "features/settings/AppearanceSection.tsx: radio без .rd",
  "features/settings/BrowserCalls.tsx: checkbox без .cb",
  "features/settings/CallPrograms.tsx: checkbox без .cb",
  "features/settings/ExportSection.tsx: checkbox без .cb",
  "features/settings/LocalModelRows.tsx: checkbox без .cb",
  "features/settings/ModelsSection.tsx: checkbox без .cb",
  "features/settings/ModelsSection.tsx: radio без .rd",

  // Пакет D: главное окно, список записей.
  "features/recordings/DateSections.tsx: checkbox без .cb",
  "features/recordings/FilterPanel.tsx: checkbox без .cb",
  "features/recordings/RecordingItem.tsx: checkbox без .cb",
  "features/recordings/RecordingsList.tsx: checkbox без .cb",
]);

/** Сами компоненты: им родной элемент можно (внутри — со своим видом). */
const NATIVE_OWNERS: Record<string, string> = { "ui/Select.tsx": "<select>", "ui/Slider.tsx": "range" };

function tsxFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? tsxFiles(join(dir, e.name))
      : e.name.endsWith(".tsx") && !e.name.endsWith(".test.tsx") ? [join(dir, e.name)] : [],
  );
}

/** Открывающие JSX-теги `<name …>` целиком: `>` внутри `{…}` и строк — не конец тега. */
function jsxTags(src: string, name: string): string[] {
  const out: string[] = [];
  for (const m of src.matchAll(new RegExp(`<${name}(?![\\w-])`, "g"))) {
    let depth = 0;
    let quote = "";
    let i = (m.index ?? 0) + m[0].length;
    for (; i < src.length; i++) {
      const c = src[i]!;
      if (quote) { if (c === quote) quote = ""; continue; }
      if (c === "\"" || c === "'" || c === "`") quote = c;
      else if (c === "{") depth++;
      else if (c === "}") depth--;
      else if (c === ">" && depth === 0) break;
    }
    out.push(src.slice(m.index, i + 1));
  }
  return out;
}

/** Нарушения в тексте .tsx: «<select>», «range», «checkbox без .cb», «radio без .rd». */
function nativeControls(text: string): string[] {
  // Комментарии («родной <select> заменён…») — не разметка.
  const src = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const found = new Set<string>();
  if (jsxTags(src, "select").length) found.add("<select>");
  for (const tag of jsxTags(src, "input")) {
    const type = /\btype="(\w+)"/.exec(tag)?.[1];
    const cls = /\bclassName="([^"]*)"/.exec(tag)?.[1]?.split(/\s+/) ?? [];
    if (type === "range") found.add("range");
    if (type === "checkbox" && !cls.some((c) => c === "cb" || c === "switch")) found.add("checkbox без .cb");
    if (type === "radio" && !cls.includes("rd")) found.add("radio без .rd");
  }
  return [...found];
}

test("разбор системных контролов: теги со стрелочными функциями, классы Aurora — не нарушение", () => {
  expect(jsxTags(`<input type="range" onChange={(e) => f(e.target.value > 1)} /> <inputx>`, "input"))
    .toEqual([`<input type="range" onChange={(e) => f(e.target.value > 1)} />`]);
  expect(nativeControls(`<select value={v}><option /></select>`)).toEqual(["<select>"]);
  expect(nativeControls(`<input type="checkbox" className="cb" /> <input type="radio" className="rd x" />`)).toEqual([]);
  expect(nativeControls(`<input type="checkbox" onChange={() => a > b} />`)).toEqual(["checkbox без .cb"]);
  expect(nativeControls(`<input type="radio" name="r" />`)).toEqual(["radio без .rd"]);
  expect(nativeControls(`<input type="text" />`)).toEqual([]);
  // Тег в комментарии — не разметка.
  expect(nativeControls(`{/* родной <select> заменён */}\n  // <input type="range">\n<b />`)).toEqual([]);
});

test("в окне нет системных списков, бегунков и флажков вне ui/Select и ui/Slider (кроме ещё не переведённых)", () => {
  const src = join(process.cwd(), "src");
  const found: string[] = [];
  for (const file of tsxFiles(src)) {
    const rel = relative(src, file).split(sep).join("/");
    for (const kind of nativeControls(readFileSync(file, "utf8"))) {
      if (NATIVE_OWNERS[rel] === kind) continue;
      found.push(`${rel}: ${kind}`);
    }
  }
  expect(found.filter((x) => !NATIVE_ALLOWED.has(x)).sort()).toEqual([]);
  // Переведённое место — убрать из списка исключений.
  expect([...NATIVE_ALLOWED].filter((x) => !found.includes(x)).sort()).toEqual([]);
});
