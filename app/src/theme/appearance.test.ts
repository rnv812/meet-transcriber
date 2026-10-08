import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  CACHE_KEY, DEFAULT_APPEARANCE, appearanceFromEvent, appearanceFromSettings, appearanceToSettings, applyAppearance,
  readCached, resolveTheme, writeCached,
} from "./appearance";

afterEach(() => localStorage.clear());

test("из настроек резидента: значения проверяются, мусор — умолчания", () => {
  expect(appearanceFromSettings({ ui: { theme: "light", aurora: "amber", aurora_style: "waves", motion: false } }))
    .toEqual({ theme: "light", aurora: "amber", auroraStyle: "waves", motion: false });
  expect(appearanceFromSettings({ ui: { theme: "pink", aurora: 3, aurora_style: null } })).toEqual(DEFAULT_APPEARANCE);
  expect(appearanceFromSettings(null)).toEqual(DEFAULT_APPEARANCE);
  expect(appearanceFromSettings({ ui: "строка" })).toEqual(DEFAULT_APPEARANCE);
});

test("в настройки — ключи резидента", () => {
  expect(appearanceToSettings({ theme: "dark", aurora: "blue", auroraStyle: "glow", motion: true }))
    .toEqual({ ui: { theme: "dark", aurora: "blue", aurora_style: "glow", motion: true } });
});

test("системная тема следует ОС, явная — нет", () => {
  expect(resolveTheme("system", true)).toBe("dark");
  expect(resolveTheme("system", false)).toBe("light");
  expect(resolveTheme("dark", false)).toBe("dark");
  expect(resolveTheme("light", true)).toBe("light");
});

test("атрибуты на корне: тема, палитра, вид, пауза движения", () => {
  const root = document.createElement("html");
  applyAppearance(root, { theme: "system", aurora: "red", auroraStyle: "waves", motion: false }, false);
  expect(root.dataset.theme).toBe("light");
  expect(root.dataset.aurora).toBe("red");
  expect(root.dataset.auroraStyle).toBe("waves");
  expect(root.dataset.motion).toBe("paused");
  applyAppearance(root, { ...DEFAULT_APPEARANCE, theme: "dark" }, false);
  expect(root.dataset.theme).toBe("dark");
  expect(root.dataset.motion).toBeUndefined();
});

test("кеш: туда и обратно; мусор и пустота — умолчания", () => {
  const a = { theme: "light", aurora: "green", auroraStyle: "glow", motion: true } as const;
  writeCached(a, localStorage);
  expect(readCached(localStorage)).toEqual(a);
  localStorage.setItem(CACHE_KEY, "{не json");
  expect(readCached(localStorage)).toEqual(DEFAULT_APPEARANCE);
  localStorage.setItem(CACHE_KEY, JSON.stringify({ theme: "x", aurora: "violet" }));
  expect(readCached(localStorage)).toEqual(DEFAULT_APPEARANCE);
  localStorage.removeItem(CACHE_KEY);
  expect(readCached(localStorage)).toEqual(DEFAULT_APPEARANCE);
});

test("недоступное хранилище не роняет окно", () => {
  const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } } as unknown as Storage;
  expect(readCached(broken)).toEqual(DEFAULT_APPEARANCE);
  expect(() => writeCached(DEFAULT_APPEARANCE, broken)).not.toThrow();
});

test("localStorage, которого нет совсем (геттер бросает), не роняет вызов без аргумента", () => {
  const desc = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get() { throw new Error("SecurityError"); },
  });
  try {
    expect(() => readCached()).not.toThrow();
    expect(readCached()).toEqual(DEFAULT_APPEARANCE);
    expect(() => writeCached(DEFAULT_APPEARANCE)).not.toThrow();
  } finally {
    if (desc) Object.defineProperty(globalThis, "localStorage", desc);
  }
});

// Ранний старт (public/appearance-boot.js) и модель (readCached) должны давать одно и то же
// на любом содержимом кеша: иначе окно мигает чужой темой до ответа резидента.
const BOOT_SRC = readFileSync(join(process.cwd(), "public", "appearance-boot.js"), "utf8");
const ATTRS = ["data-theme", "data-aurora", "data-aurora-style", "data-motion"] as const;

const CACHE_FIXTURES: ReadonlyArray<readonly [string, string | null]> = [
  ["тёмный, полный", JSON.stringify({ theme: "dark", aurora: "violet", auroraStyle: "glow", motion: true })],
  ["светлый, волны, без движения", JSON.stringify({ theme: "light", aurora: "amber", auroraStyle: "waves", motion: false })],
  ["системный, зелёный", JSON.stringify({ theme: "system", aurora: "green", auroraStyle: "glow", motion: true })],
  ["частично испорченный (палитра неизвестна)", JSON.stringify({ theme: "dark", aurora: "bad", auroraStyle: "waves", motion: false })],
  ["тема неизвестна", JSON.stringify({ theme: "x", aurora: "violet", auroraStyle: "waves", motion: false })],
  ["палитры нет", JSON.stringify({ theme: "dark", auroraStyle: "waves", motion: false })],
  ["ключа нет", null],
  ["не JSON", "{не json"],
  ["JSON null", "null"],
  ["JSON массив", "[]"],
];

// Подсказка оболочки (window.__MEET_THEME__, initialization_script): решение
// startup_theme() из config.json; берётся только когда кеша нет или он негодный.
const HINTS: readonly unknown[] = [undefined, "dark", "light", "system", "розовая", 1];

const PARITY_CASES = CACHE_FIXTURES.flatMap(([label, raw]) =>
  HINTS.flatMap((hint) => [true, false].map((systemDark) => [label, raw, hint, systemDark] as const)));

type HintHost = { __MEET_THEME__?: unknown };

function setHint(hint: unknown): () => void {
  const host = globalThis as HintHost;
  if (hint === undefined) delete host.__MEET_THEME__;
  else host.__MEET_THEME__ = hint;
  return () => { delete host.__MEET_THEME__; };
}

function stubMatchMedia(dark: boolean): () => void {
  const original = window.matchMedia;
  window.matchMedia = ((query: string) => ({
    matches: dark, media: query, onchange: null,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia;
  return () => { window.matchMedia = original; };
}

const clearAttrs = (root: HTMLElement) => ATTRS.forEach((n) => root.removeAttribute(n));
const snapshot = (root: HTMLElement) => ATTRS.map((n) => root.getAttribute(n));

/** Атрибуты <html> после раннего старта. */
function bootAttrs(raw: string | null, hint: unknown, systemDark: boolean): (string | null)[] {
  const root = document.documentElement;
  if (raw === null) localStorage.removeItem(CACHE_KEY);
  else localStorage.setItem(CACHE_KEY, raw);
  const restoreMedia = stubMatchMedia(systemDark);
  const restoreHint = setHint(hint);
  try {
    clearAttrs(root);
    new Function(BOOT_SRC)();
    return snapshot(root);
  } finally {
    restoreHint();
    restoreMedia();
    clearAttrs(root);
  }
}

test.each(PARITY_CASES)("ранний старт совпадает с readCached: %s, подсказка %s, система тёмная: %s",
  (_label, raw, hint, systemDark) => {
    const root = document.documentElement;
    const fromBoot = bootAttrs(raw, hint, systemDark);
    expect(fromBoot[0]).not.toBeNull();
    const restoreHint = setHint(hint);
    try {
      clearAttrs(root);
      applyAppearance(root, readCached(localStorage), systemDark);
      expect(snapshot(root)).toEqual(fromBoot);
    } finally {
      restoreHint();
      clearAttrs(root);
    }
  });

test("подсказка оболочки: обновившийся без кеша на светлой ОС открывается тёмным", () => {
  expect(bootAttrs(null, "dark", false)[0]).toBe("dark");
  const restore = setHint("dark");
  try {
    expect(readCached(localStorage)).toEqual({ ...DEFAULT_APPEARANCE, theme: "dark" });
  } finally {
    restore();
  }
});

test("подсказка оболочки: негодный кеш — тема из подсказки, остальное — умолчания", () => {
  const bad = JSON.stringify({ theme: "x", aurora: "amber", auroraStyle: "waves", motion: false });
  expect(bootAttrs(bad, "light", true)).toEqual(["light", "violet", "glow", null]);
  localStorage.setItem(CACHE_KEY, bad);
  const restore = setHint("light");
  try {
    expect(readCached(localStorage)).toEqual({ ...DEFAULT_APPEARANCE, theme: "light" });
  } finally {
    restore();
  }
});

test("подсказка оболочки: годный кеш важнее подсказки, мусорная подсказка не действует", () => {
  const cached = JSON.stringify({ theme: "light", aurora: "green", auroraStyle: "glow", motion: true });
  expect(bootAttrs(cached, "dark", true)[0]).toBe("light");
  expect(bootAttrs(null, "розовая", false)[0]).toBe("light");
  localStorage.setItem(CACHE_KEY, cached);
  const restore = setHint("dark");
  try {
    expect(readCached(localStorage).theme).toBe("light");
  } finally {
    restore();
  }
});

test("подсказка оболочки за бросающим геттером не роняет readCached", () => {
  Object.defineProperty(globalThis, "__MEET_THEME__", { configurable: true, get() { throw new Error("нет"); } });
  try {
    expect(readCached(localStorage)).toEqual(DEFAULT_APPEARANCE);
  } finally {
    delete (globalThis as HintHost).__MEET_THEME__;
  }
});

test("событие оболочки разбирается как кеш", () => {
  expect(appearanceFromEvent({ theme: "light", aurora: "blue", auroraStyle: "waves", motion: false }))
    .toEqual({ theme: "light", aurora: "blue", auroraStyle: "waves", motion: false });
  expect(appearanceFromEvent("мусор")).toEqual(DEFAULT_APPEARANCE);
});

test("умолчания оформления не меняются по ссылке", () => {
  expect(Object.isFrozen(DEFAULT_APPEARANCE)).toBe(true);
});
