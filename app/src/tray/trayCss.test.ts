// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Панель трея — на токенах Atlas Aurora и следует теме и палитре окна
 * (`data-theme`/`data-aurora` от useAppearance), а не теме системы: своих
 * `--tp-*` и правил на `prefers-color-scheme` в ней нет.
 */
const css = readFileSync(join(process.cwd(), "src", "tray", "tray.css"), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "");

/** Тело правила с точно таким селектором (первое вхождение). */
function rule(selector: string): string {
  const at = css.split(/\}/).find((chunk) => chunk.split("{")[0]?.trim() === selector);
  return at?.split("{")[1] ?? "";
}

test("тема — от окна: нет prefers-color-scheme и своих токенов --tp-*", () => {
  expect(css).not.toMatch(/prefers-color-scheme/);
  expect(css).not.toMatch(/--tp-[\w-]+\s*:/);
  expect(css).not.toMatch(/var\(--tp-/);
});

test("без литералов цвета: только токены Aurora", () => {
  expect(css).not.toMatch(/#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(/i);
});

test("системное стекло macOS: корень без своей заливки", () => {
  const native = rule('.tp[data-glass="native"]');
  expect(native).not.toBe("");
  expect(native).not.toMatch(/background/);
});

test("таймер записи — 40 px display-шрифтом Aurora (как часы карточки записи), цифры не прыгают", () => {
  const timer = rule(".tp__timer");
  expect(timer).toMatch(/font:\s*500 40px\/1 var\(--font-display\)/);
  expect(timer).toMatch(/font-variant-numeric:\s*tabular-nums/);
  expect(timer).not.toMatch(/--font-code/);
});

test("строка последней записи — высотой --control-lg, отступ по сетке", () => {
  const row = rule(".tp__recent-row");
  expect(row).toMatch(/height:\s*var\(--control-lg\)/);
  expect(row).toMatch(/padding:\s*0 var\(--s-2\)/);
});

test("свои метки и плашки ушли — бейджи и выноски Aurora", () => {
  expect(css).not.toMatch(/\.tp__chip\b|\.tp__warn\b|\.tp__error\b/);
});

test("занятая кнопка вопроса не пустая: индикатор не прячется (подпись прячет button.css)", () => {
  expect(css).not.toMatch(/btn__spinner[^{]*\{[^}]*display:\s*none/);
});
