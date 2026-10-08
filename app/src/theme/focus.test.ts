import { readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";

/**
 * Фокус у всех контролов рисует одна рамка Atlas Aurora
 * (`:focus-visible { outline: none; box-shadow: var(--focus-ring) }`, base.css).
 * Своя `outline` в правиле фокуса давала вторую рамку поверх. Нужна особая
 * рамка — `box-shadow` (у полей — `inset 0 0 0 1px var(--accent-line)`).
 * Сброс `outline: none` (фокус программно, без рамки) — можно.
 */
const SRC = join(process.cwd(), "src");
const AURORA = join("theme", "aurora") + sep;

function css(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) css(p, out);
    else if (e.name.endsWith(".css")) out.push(p);
  }
  return out;
}

/** Правила `селектор { тело }` без комментариев; внутри @-блоков — внутренние правила. */
function rules(text: string): { selector: string; body: string }[] {
  const clean = text.replace(/\/\*[\s\S]*?\*\//g, "");
  return [...clean.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ selector: (m[1] ?? "").trim(), body: m[2] ?? "" }));
}

/** Объявления `outline`, `outline-color/-style/-width` со значением, отличным от «none» / «0». */
function ownOutlines(body: string): string[] {
  const out: string[] = [];
  for (const m of body.matchAll(/(?:^|[;\s])(outline(?:-color|-style|-width)?)\s*:\s*([^;]+)/g)) {
    if (!/^(none|0|0px)\s*(!important)?$/i.test((m[2] ?? "").trim())) out.push(`${m[1]}: ${(m[2] ?? "").trim()}`);
  }
  return out;
}

const focusBad = (text: string) =>
  rules(text).filter((r) => /:focus/.test(r.selector) && ownOutlines(r.body).length > 0).map((r) => r.selector);

test("разбор: свою рамку в правиле фокуса находит, сброс и обычные правила — нет", () => {
  expect(focusBad(".a:focus-visible { outline: 2px solid red; }")).toEqual([".a:focus-visible"]);
  expect(focusBad(".a:focus { outline-color: red }")).toEqual([".a:focus"]);
  expect(focusBad("@media (x) { .a:focus-within { border: 0; outline: 1px dashed } }")).toEqual([".a:focus-within"]);
  expect(focusBad(".a:focus { outline: none; } .b:focus-visible { outline: 0 !important }")).toEqual([]);
  expect(focusBad(".a { outline: 1px dashed red; } .b:focus { box-shadow: var(--focus-ring); outline-offset: -2px }")).toEqual([]);
});

test("окно не рисует вторую рамку фокуса: нет своей outline в правилах :focus / :focus-visible", () => {
  const bad: string[] = [];
  const files = css(SRC).filter((f) => !relative(SRC, f).startsWith(AURORA));
  expect(files.length).toBeGreaterThan(20);
  for (const f of files) {
    for (const r of rules(readFileSync(f, "utf8"))) {
      if (!/:focus/.test(r.selector)) continue;
      for (const o of ownOutlines(r.body)) bad.push(`${relative(SRC, f)}: ${r.selector} { ${o} }`);
    }
  }
  expect(bad).toEqual([]);
});

/*
 * Своя `box-shadow` у фокусируемого элемента вытесняет рамку фокуса Aurora:
 * `:focus-visible` в base.css — (0,1,0), правило окна грузится позже и обычно
 * не слабее. Выбранное рисуется `outline` (её правило фокуса не трогает), а
 * тень, без которой элементу никак, повторяется в своём `:focus-visible`
 * вместе с `var(--focus-ring)`. Фокусируемость угадывается по селектору:
 * кнопки, состояния aria, известные фокусируемые блоки окна.
 */
const FOCUSABLE_HINT =
  /\bbutton\b|\.btn\b|\[aria-(?:pressed|current|selected|expanded|checked)|\[tabindex|\[role=|__item\b|__swatch\b|-float\b|__new\b|__main\b|__opt\b/;

/** Тень у элемента, который фокуса не получает, хотя селектор похож: селектор → причина. */
const SHADOW_ALLOW = new Map<string, string>([]);

const norm = (s: string) => s.replace(/\s+/g, " ").trim();
const shadowOf = (body: string) => /(?:^|[;\s])box-shadow\s*:\s*([^;]+)/.exec(body)?.[1]?.trim();

/** Части селекторов с тенью на фокусируемом элементе без пары `:focus-visible` с рамкой фокуса. */
function shadowBad(text: string): string[] {
  const all = rules(text);
  const ringed = new Set<string>();
  for (const r of all) {
    if (!/var\(--focus-ring\)/.test(shadowOf(r.body) ?? "")) continue;
    for (const part of r.selector.split(",")) {
      if (part.includes(":focus-visible")) ringed.add(norm(part.replace(":focus-visible", "")));
    }
  }
  const bad: string[] = [];
  for (const r of all) {
    const shadow = shadowOf(r.body);
    if (!shadow || /^none\b/.test(shadow)) continue;
    for (const raw of r.selector.split(",")) {
      const part = norm(raw);
      if (part.includes(":focus") || !FOCUSABLE_HINT.test(part)) continue;
      if (ringed.has(part) || SHADOW_ALLOW.has(part)) continue;
      bad.push(part);
    }
  }
  return bad;
}

test("разбор: тень на кнопке без пары с рамкой фокуса находит, пару и выбранное через outline — нет", () => {
  expect(shadowBad('button.x[aria-pressed="true"] { box-shadow: inset 0 0 0 1px red }')).toEqual(['button.x[aria-pressed="true"]']);
  expect(shadowBad(".a__new { box-shadow: 0 4px 14px red }")).toEqual([".a__new"]);
  expect(shadowBad(".a__new { box-shadow: 0 4px 14px red } .a__new:focus-visible { box-shadow: var(--focus-ring), 0 4px 14px red }")).toEqual([]);
  expect(shadowBad('.a__item[aria-current="page"] { outline: 1px solid var(--control-line); outline-offset: -1px }')).toEqual([]);
  expect(shadowBad(".card { box-shadow: 0 4px 14px red } .a__swatch { box-shadow: none }")).toEqual([]);
  // Пара без рамки фокуса не спасает.
  expect(shadowBad(".a__swatch { box-shadow: 0 0 0 1px red } .a__swatch:focus-visible { box-shadow: 0 0 0 2px blue }")).toEqual([".a__swatch"]);
});

test("своя тень не прячет рамку фокуса у кнопок, пунктов и переключателей окна", () => {
  const bad: string[] = [];
  for (const f of css(SRC).filter((x) => !relative(SRC, x).startsWith(AURORA))) {
    for (const part of shadowBad(readFileSync(f, "utf8"))) bad.push(`${relative(SRC, f)}: ${part}`);
  }
  expect(bad).toEqual([]);
});
