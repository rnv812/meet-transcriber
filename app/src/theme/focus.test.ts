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
