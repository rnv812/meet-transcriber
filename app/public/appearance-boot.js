// Оформление до React: тема и палитра из прошлого запуска (theme/appearance.ts,
// ключ meet.appearance), чтобы окно не мигало чужой темой. Правило то же, что у
// readCached: если тема или палитра в кеше не из списка — весь кеш отбрасывается
// и берутся умолчания (система, фиолетовая, glow, движение включено), иначе
// окно сначала покажет одно, а React потом другое. Источник истины — настройки
// резидента: окно перепроверит их сразу после старта.
(function () {
  var THEMES = ["system", "dark", "light"];
  var PALETTES = ["violet", "green", "blue", "red", "amber"];
  var a = null;
  try { a = JSON.parse(localStorage.getItem("meet.appearance") || "null"); } catch (e) { a = null; }
  var ok = a !== null && typeof a === "object" && !Array.isArray(a) &&
    THEMES.indexOf(a.theme) >= 0 && PALETTES.indexOf(a.aurora) >= 0;
  if (!ok) a = { theme: "system", aurora: "violet", auroraStyle: "glow", motion: true };
  var root = document.documentElement;
  var dark = !window.matchMedia || window.matchMedia("(prefers-color-scheme: dark)").matches;
  root.setAttribute("data-theme", a.theme === "system" ? (dark ? "dark" : "light") : a.theme);
  root.setAttribute("data-aurora", a.aurora);
  root.setAttribute("data-aurora-style", a.auroraStyle === "waves" ? "waves" : "glow");
  if (a.motion === false) root.setAttribute("data-motion", "paused");
})();
