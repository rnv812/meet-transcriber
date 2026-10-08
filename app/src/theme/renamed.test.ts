import { readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";

/**
 * Классы окна, совпадавшие с классами Atlas Aurora по имени, но с другим
 * смыслом, переименованы (этап 2 0.4). Старые имена больше не встречаются ни
 * в CSS окна (кроме скопированной дизайн-системы), ни в className разметки —
 * ни в строке, ни в шаблонной строке, ни в ветке условия или склейке.
 */
const SRC = join(process.cwd(), "src");
const AURORA = join("theme", "aurora") + sep;
const OLD = ["tabs", "card", "search", "help", "empty"];
/** Блоки, у которых и элементы (`.tabs__item`) были переименованы вместе с блоком. */
const OLD_BLOCKS = ["tabs", "help", "empty"];

function files(dir: string, ext: string[], out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) files(p, ext, out);
    else if (ext.some((x) => e.name.endsWith(x)) && !e.name.includes(".test.")) out.push(p);
  }
  return out;
}
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, "");
const windowCss = () => files(SRC, [".css"]).filter((f) => !relative(SRC, f).startsWith(AURORA));

/** Выражения после `className=` / `className:` — строка, `{…}` с вложенными скобками. */
function classExpressions(src: string): string[] {
  const out: string[] = [];
  for (const m of src.matchAll(/\bclassName\s*[=:]\s*/g)) {
    let i = m.index! + m[0].length;
    const open = src[i];
    if (open === '"' || open === "'") {
      const end = src.indexOf(open, i + 1);
      if (end > 0) out.push(src.slice(i, end + 1));
    } else if (open === "{") {
      let depth = 0;
      const start = i;
      for (; i < src.length; i++) {
        if (src[i] === "{") depth++;
        else if (src[i] === "}" && --depth === 0) break;
      }
      out.push(src.slice(start, i + 1));
    }
  }
  return out;
}

/** Имена классов из всех строковых литералов выражения: "a b", 'a', `a ${x} b`, ветки `?:` и склейка `+`. */
function classTokens(expr: string): string[] {
  const tokens: string[] = [];
  const add = (text: string) => tokens.push(...text.split(/\s+/).filter(Boolean));
  for (const m of expr.matchAll(/"([^"\\]*)"|'([^'\\]*)'/g)) add(m[1] ?? m[2] ?? "");
  for (const m of expr.matchAll(/`([^`]*)`/g)) add((m[1] ?? "").replace(/\$\{[^}]*\}/g, " "));
  return tokens;
}

const oldClassesIn = (src: string) => {
  const found: string[] = [];
  for (const expr of classExpressions(src)) {
    for (const name of classTokens(expr)) if (OLD.includes(name)) found.push(name);
    // Старое имя перед подстановкой в шаблоне: `card${…}` / `tabs ${…}`.
    for (const m of expr.matchAll(/(^|[\s`"'{])(tabs|card|search|help|empty)(?=\s*\$\{)/g)) found.push(m[2] ?? "");
  }
  return [...new Set(found)];
};

test("разбор: находит прежние классы в строке, шаблоне, ветке условия и склейке", () => {
  expect(oldClassesIn('<div className="tabs x" />')).toEqual(["tabs"]);
  expect(oldClassesIn("<div className={`card ${a ? 'b' : ''}`} />")).toEqual(["card"]);
  expect(oldClassesIn("<div className={`tabs ${a}`} />")).toEqual(["tabs"]);
  expect(oldClassesIn("<div className={`card${a}`} />")).toEqual(["card"]);
  expect(oldClassesIn('<div className={a ? "empty" : "x"} />')).toEqual(["empty"]);
  expect(oldClassesIn('<div className={"x " + (a ? "help" : "")} />')).toEqual(["help"]);
  expect(oldClassesIn("<div className={`x ${a ? \"search\" : \"\"}`} />")).toEqual(["search"]);
  expect(oldClassesIn("const p = { className: 'empty y' };")).toEqual(["empty"]);
  // Не старые: другие классы, элементы блока, идентификаторы и подстановки.
  expect(oldClassesIn('<div className="empty-state card__title tabs-x" />')).toEqual([]);
  expect(oldClassesIn("<div className={`x ${empty} ${search ? 'ok' : ''}`} />")).toEqual([]);
  expect(oldClassesIn("<div className={card} />")).toEqual([]);
});

test("CSS окна не объявляет прежних блоков tabs/card/search/help/empty", () => {
  const bad: string[] = [];
  for (const f of windowCss()) {
    const css = strip(readFileSync(f, "utf8"));
    for (const name of OLD) {
      // .name как отдельный класс: после — не буква, цифра, «-» или «_».
      if (new RegExp(`\\.${name}(?![\\w-])`).test(css)) bad.push(`${relative(SRC, f)}: .${name}`);
    }
  }
  expect(bad).toEqual([]);
});

test("CSS окна не оставляет элементов прежних блоков (.tabs__*, .help__*, .empty__*)", () => {
  const bad: string[] = [];
  for (const f of windowCss()) {
    const css = strip(readFileSync(f, "utf8"));
    for (const name of OLD_BLOCKS) {
      if (new RegExp(`\\.${name}__`).test(css)) bad.push(`${relative(SRC, f)}: .${name}__`);
    }
  }
  expect(bad).toEqual([]);
});

/**
 * Файлы, где имя — уже класс Atlas Aurora (прежний блок окна переименован, CSS
 * окна его не объявляет — проверка выше): файл → имена.
 */
const AURORA_IN_MARKUP = new Map<string, string[]>([
  // Подгруппа настроек — `.card` Aurora (feedback.css), этап 4.
  [join("features", "settings", "Section.tsx"), ["card"]],
  // Строка поиска по записям — `.search` Aurora (controls.css): значок слева в поле, этап 3.
  [join("features", "recordings", "SearchSuggest.tsx"), ["search"]],
  // «Поиск по настройкам» — `.search` Aurora (значок слева в поле), этап 4.
  [join("features", "settings", "SettingsSearch.tsx"), ["search"]],
  // Вкладки карточки записи — `.tabs` Aurora (controls.css), этап 3.
  [join("features", "card", "CardTabs.tsx"), ["tabs"]],
  // Поиск над лентой «Расшифровки» — `.search` Aurora; «Наблюдения» — `.card aurora-wash`, этап 3.
  [join("features", "card", "TranscriptView.tsx"), ["search"]],
  [join("features", "card", "markup.tsx"), ["card"]],
  // «Мой голос» и таблица людей — `.card`, «Найти человека» — `.search` Aurora, этап 3.
  [join("features", "voices", "VoicesPane.tsx"), ["card", "search"]],
  // «Идёт запись» (уровни, «Позвать ассистента»), «Расшифровывается» (этапы) — `.card` Aurora;
  // пустая библиотека — `.empty` Aurora на сиянии, этап 3.
  [join("features", "card", "RecordingNow.tsx"), ["card"]],
  [join("features", "card", "Transcribing.tsx"), ["card"]],
  [join("features", "recordings", "LibraryEmpty.tsx"), ["empty"]],
  // Живая панель: вкладки узкой области и сегменты частоты и профиля — `.tabs` Aurora, этап 5.
  [join("live", "LiveWorkspace.tsx"), ["tabs"]],
  [join("live", "SessionBar.tsx"), ["tabs"]],
]);

test("разметка не использует прежние классы tabs/card/search/help/empty", () => {
  const bad: string[] = [];
  for (const f of files(SRC, [".tsx", ".ts"])) {
    const allowed = AURORA_IN_MARKUP.get(relative(SRC, f)) ?? [];
    for (const name of oldClassesIn(readFileSync(f, "utf8"))) {
      if (!allowed.includes(name)) bad.push(`${relative(SRC, f)}: ${name}`);
    }
  }
  expect(bad).toEqual([]);
});
