import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

/**
 * Стили живой панели и чата (0.4, этап 5) — только на токенах Atlas Aurora:
 * без литералов цвета, без прежних переменных окна (удалены на этапе 7; всё
 * окно сторожат theme/noColors.test.ts и theme/noLegacyVars.test.ts) и с текстом цвета акцента только через
 * `--accent-line`. Общие стили чата во вкладке «Ассистент» карточки
 * (features/card/assistant.css) — туда же.
 */
const SRC = join(process.cwd(), "src");
const FILES = [
  ...readdirSync(join(SRC, "live")).filter((f) => f.endsWith(".css")).map((f) => join("live", f)),
  join("features", "card", "assistant.css"),
];
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8").replace(/\r\n/g, "\n");
const clean = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, "");

const COLOR = /#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(/i;
const LEGACY = /var\(--(bg|surface|surface-hover|line|line-2|text|text-2|text-3|accent-hi|accent-glow|violet|red|red-glow|ok|amber|err|run|t)[,)]/;
const ACCENT_TEXT = /(^|[;{\s])color\s*:\s*var\(--accent(-hover)?\)/;

function offending(re: RegExp): string[] {
  const bad: string[] = [];
  for (const rel of FILES) {
    clean(rel).split("\n").forEach((line, i) => { if (re.test(line)) bad.push(`${rel}:${i + 1}: ${line.trim()}`); });
  }
  return bad;
}

test("файлы найдены: chat.css, live.css, panel.css, assistant.css", () => {
  expect(FILES).toEqual(expect.arrayContaining([join("live", "chat.css"), join("live", "live.css"),
    join("live", "panel.css"), join("features", "card", "assistant.css")]));
});

test("без литералов цвета", () => {
  expect(offending(COLOR)).toEqual([]);
});

test("без прежних переменных окна (--line, --text*, --surface, --err, --amber …)", () => {
  expect(LEGACY.test("a { color: var(--text-2); }")).toBe(true);
  expect(LEGACY.test("a { background: var(--ok, #4cb782); }")).toBe(true);
  expect(LEGACY.test("a { color: var(--ink-2); background: var(--surface-2); }")).toBe(false);
  expect(offending(LEGACY)).toEqual([]);
});

test("текст цвета акцента — только --accent-line", () => {
  expect(offending(ACCENT_TEXT)).toEqual([]);
  expect(clean(join("live", "chat.css"))).toMatch(/\.chat__more \{[^}]*color: var\(--accent-line\)/);
});

test("мёртвых правил переключателя частоты (до сегментов Aurora) нет", () => {
  expect(read(join("live", "chat.css"))).not.toMatch(/session-freq__(group|opt)|session-bar__dot/);
});

test("сенсорный экран: поставленная реакция не приглушена", () => {
  const css = clean(join("live", "chat.css"));
  const touch = css.slice(css.indexOf("@media (hover: none)"));
  expect(touch).toMatch(/\.chat-react__btn\.is-on \{ opacity: 1 !important; \}/);
});

test("отклик на реакцию гаснет анимацией, но не при «уменьшить движение»", () => {
  const css = clean(join("live", "chat.css"));
  expect(css).toMatch(/\.chat-msg__ack \{[^}]*animation: chat-ack/);
  expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{[^@]*\.chat-msg__ack \{ animation: none; \}/);
});
