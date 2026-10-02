/**
 * Платформа окна: Windows (основная) или macOS (экспериментально, Apple
 * Silicon). По ней выбираются тексты, где ОС названа по имени (хранилище
 * токена, автозапуск, звук собеседников), и профиль движка «Apple Silicon».
 *
 * Определяется по navigator: WebView2 на Windows называет себя «Windows NT»,
 * WKWebView на macOS — «Macintosh» (platform «MacIntel»). В тестах (jsdom)
 * это всегда Windows: тексты и снимки остаются прежними.
 */

export type Os = "windows" | "macos";

type NavigatorLike = { platform?: string; userAgent?: string };

export function detectOs(nav?: NavigatorLike): Os {
  const source = nav ?? (typeof navigator === "undefined" ? undefined : navigator);
  const platform = source?.platform ?? "";
  const agent = source?.userAgent ?? "";
  return /^Mac/i.test(platform) || /Macintosh/.test(agent) ? "macos" : "windows";
}

/** Тексты, где названа ОС. */
export function osTexts(os: Os) {
  const mac = os === "macos";
  return {
    /** Где лежит токен Hugging Face (`keyring`): «сохранён в …». */
    keyring: mac ? "связке ключей macOS" : "диспетчере учётных данных Windows",
    autostart: mac ? "Запускать при входе в macOS" : "Запускать вместе с Windows",
    /** Где живёт значок приложения: «в …». */
    trayArea: mac ? "строке меню" : "области уведомлений",
    /** Системный микрофон: «микрофон … по умолчанию». */
    systemName: mac ? "macOS" : "Windows",
  };
}

export const OS: Os = detectOs();
export const IS_MAC = OS === "macos";
export const OS_TEXT = osTexts(OS);
