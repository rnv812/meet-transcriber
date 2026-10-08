// Оформление до React: тема и палитра из прошлого запуска (theme/appearance.ts,
// ключ meet.appearance), чтобы окно не мигало чужой темой. Источник истины —
// настройки резидента: окно перепроверит их сразу после старта.
(function () {
  var a = {};
  try { a = JSON.parse(localStorage.getItem("meet.appearance") || "{}") || {}; } catch (e) { a = {}; }
  var root = document.documentElement;
  var dark = !window.matchMedia || window.matchMedia("(prefers-color-scheme: dark)").matches;
  var theme = a.theme === "dark" || a.theme === "light" ? a.theme : (dark ? "dark" : "light");
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-aurora", ["violet", "green", "blue", "red", "amber"].indexOf(a.aurora) >= 0 ? a.aurora : "violet");
  root.setAttribute("data-aurora-style", a.auroraStyle === "waves" ? "waves" : "glow");
  if (a.motion === false) root.setAttribute("data-motion", "paused");
})();
