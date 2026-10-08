/**
 * Цвета терминала вкладки «Агент» — из токенов Atlas Aurora («Блок кода»:
 * `--code-bg`, `--code-ink`, `--code-comment`…) текущей темы и палитры.
 *
 * xterm рисует на холсте и CSS-переменных не видит: цвета читаются из стилей
 * страницы при создании терминала и заново, когда окно меняет тему или
 * палитру (атрибуты `data-theme`, `data-aurora` на <html>, theme/appearance).
 */

import type { ITheme } from "@xterm/xterm";

/** Цвет терминала → токен Aurora. Нет токена — умолчание xterm. */
export const TERMINAL_TOKENS = {
  background: "--code-bg",
  foreground: "--code-ink",
  cursor: "--code-ink",
  cursorAccent: "--code-bg",
  selectionBackground: "--selection",
  // ANSI: серый (строка о завершении, приглушённый текст агента) — комментарий;
  // остальное — тона блока кода и статусов: читаются и в светлой теме.
  black: "--code-punct",
  brightBlack: "--code-comment",
  red: "--danger",
  brightRed: "--danger",
  green: "--success",
  brightGreen: "--success",
  yellow: "--code-string",
  brightYellow: "--warning",
  blue: "--code-function",
  brightBlue: "--code-number",
  magenta: "--code-keyword",
  brightMagenta: "--code-keyword",
  cyan: "--code-property",
  brightCyan: "--code-property",
  white: "--code-ink",
  brightWhite: "--code-ink",
} as const satisfies Partial<Record<keyof ITheme, string>>;

/** Атрибуты <html>, которыми окно меняет тему и палитру (theme/appearance). */
const APPEARANCE_ATTRS = ["data-theme", "data-aurora", "data-aurora-style"];

/** Похоже на готовый цвет, а не на нераскрытую переменную или пустоту. */
const isColor = (v: string) => v !== "" && !v.includes("var(");

/**
 * Значение токена как цвет. Сначала — вычисленный `color` пробного элемента:
 * браузер раскрывает в нём и `var()`, и `color-mix()`. Где не раскрыл
 * (jsdom), — само значение переменной.
 */
function reader(): { read: (name: string) => string | null; done: () => void } {
  const host = document.body ?? document.documentElement;
  const probe = document.createElement("span");
  probe.hidden = true;
  host.appendChild(probe);
  const vars = getComputedStyle(host);
  return {
    read(name) {
      const raw = vars.getPropertyValue(name).trim();
      if (!raw) return null;
      probe.style.color = "";
      probe.style.color = `var(${name})`;
      const computed = getComputedStyle(probe).color.trim();
      if (isColor(computed)) return computed;
      return isColor(raw) ? raw : null;
    },
    done: () => probe.remove(),
  };
}

/** Цвета терминала по текущей теме и палитре окна. */
export function terminalTheme(): ITheme {
  if (typeof document === "undefined") return {};
  const { read, done } = reader();
  const theme: Record<string, string> = {};
  try {
    for (const [key, name] of Object.entries(TERMINAL_TOKENS)) {
      const value = read(name);
      if (value) theme[key] = value;
    }
  } finally {
    done();
  }
  return theme as ITheme;
}

/** Окно сменило тему или палитру — `onChange`. Возвращает, как перестать следить. */
export function watchAppearance(onChange: () => void): () => void {
  if (typeof MutationObserver === "undefined") return () => {};
  const observer = new MutationObserver(() => onChange());
  observer.observe(document.documentElement, { attributes: true, attributeFilter: APPEARANCE_ATTRS });
  return () => observer.disconnect();
}
