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

test("поле ввода чата без своего кольца фокуса: фокус показывает кромка сияния строки", () => {
  const css = clean(join("live", "chat.css"));
  expect(css).toMatch(/\.chat-compose__field:focus-visible[^{]*\{[^}]*box-shadow: none/);
});

test("отклик на реакцию гаснет анимацией, но не при «уменьшить движение»", () => {
  const css = clean(join("live", "chat.css"));
  expect(css).toMatch(/\.chat-msg__ack \{[^}]*animation: chat-ack/);
  expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{[^@]*\.chat-msg__ack \{ animation: none; \}/);
});

// --- Доводка 0.4 (пакет A аудита) ---------------------------------------------------------

test("A1: корень панели — плотное стекло на непрозрачной подложке (окно прозрачное: рабочий стол не просвечивает)", () => {
  const css = clean(join("live", "panel.css"));
  expect(css).toMatch(/\.live-panel\.glass--dense \{[^}]*background: linear-gradient\(var\(--glass-2\), var\(--glass-2\)\), var\(--canvas\)/);
  // Размывать под панелью нечего (WebView2 не видит рабочий стол) — фильтр не тратит видеокарту поверх звонка.
  expect(css).toMatch(/\.live-panel\.glass--dense \{[^}]*backdrop-filter: none/);
});

test("A2: строка ввода — своя плотная поверхность, док под ней сплошной и с линией сверху", () => {
  const css = clean(join("live", "chat.css"));
  expect(css).toMatch(/\.chat-compose\.aurora-edge \{[^}]*--edge-bg: var\(--surface-2\)/);
  expect(css).toMatch(/\[data-theme='light'\] \.chat-compose\.aurora-edge \{[^}]*--edge-bg: var\(--surface-1\)/);
  expect(css).toMatch(/\.chat-compose\.aurora-edge \{[^}]*box-shadow: var\(--aurora-edge-glow\), var\(--elev-2\)/);
  // Фокус — кромка сияния вместе с тенью (своя тень не вытесняет кольцо .aurora-edge:focus-within).
  expect(css).toMatch(/\.chat-compose\.aurora-edge:focus-within \{[^}]*inset 0 0 0 1px var\(--accent-line\)/);
  // Подсветка «несут файл» сильнее светлой темы: объявлена позже с той же силой.
  expect(css.lastIndexOf(".chat-compose.aurora-edge.is-over"))
    .toBeGreaterThan(css.indexOf("[data-theme='light'] .chat-compose.aurora-edge"));
  expect(css).toMatch(/\.chat-dock \{[^}]*padding: 10px 12px 12px[^}]*border-top: 1px solid var\(--hairline\)/);
});

test("A3: кегль панели и чата — только по шкале Aurora (--text-*), без 10.5 / 11 / 11.5 / 12.5 px", () => {
  const bad: string[] = [];
  for (const rel of FILES.filter((f) => f.startsWith("live"))) {
    clean(rel).split("\n").forEach((line, i) => {
      if (/font-size:\s*[\d.]+px|font:[^;]*\b[\d.]+px/.test(line)) bad.push(`${rel}:${i + 1}: ${line.trim()}`);
    });
  }
  expect(bad).toEqual([]);
});

test("A6: шапка развёрнутой панели — высоты верхней панели Aurora (56), свёрнутой — 48", () => {
  const css = clean(join("live", "panel.css"));
  expect(css).toMatch(/\.live-head \{[^}]*height: var\(--control-lg\)/);
  expect(css).toMatch(/\.live-panel--open \.live-head \{[^}]*height: var\(--topbar-h\); padding: 0 10px 0 16px/);
});

test("A7: самодельных контролов панели нет — кнопки, тост, бейджи и поле Aurora", () => {
  const all = FILES.filter((f) => f.startsWith("live")).map(clean).join("\n");
  expect(all).not.toMatch(/\.live-chip\b|\.chat-ws__toggle\b|\.session-bar__know\b|\.live-ask__input:focus\b/);
  expect(all).not.toMatch(/\.live-head__quiet-tag \{[^}]*background/);
});
