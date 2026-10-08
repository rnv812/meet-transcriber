import {
  CACHE_KEY, DEFAULT_APPEARANCE, appearanceFromSettings, appearanceToSettings, applyAppearance,
  readCached, resolveTheme, writeCached,
} from "./appearance";

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
