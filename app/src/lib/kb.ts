/** Пути внутри базы знаний: папка группы, исключения ассистента. */

const trimSlashes = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "");

/**
 * Путь `path` относительно базы `root` через «/» (без «/» по краям): "" — сама
 * база, null — вне базы. Регистр не важен (Windows), разделители — любые.
 */
export function kbRelative(root: string, path: string): string | null {
  const r = trimSlashes(root.trim());
  const p = trimSlashes(path.trim());
  if (!r || !p) return null;
  if (p.toLowerCase() === r.toLowerCase()) return "";
  if (!p.toLowerCase().startsWith(`${r.toLowerCase()}/`)) return null;
  return p.slice(r.length + 1).replace(/^\/+/, "");
}

/** Полный путь папки `rel` внутри базы `root` (разделитель — как у базы). */
export function kbJoin(root: string, rel: string): string {
  const sep = root.includes("\\") ? "\\" : "/";
  const base = root.replace(/[\\/]+$/, "");
  return rel ? `${base}${sep}${rel.replace(/\//g, sep)}` : base;
}

/**
 * Запись исключения `assist.kb_exclude` из того, что ввёл человек: «Папка/» —
 * через «/», с «/» на конце; абсолютный путь, буква диска, «..» и «.» — null
 * (исключение — всегда папка внутри базы, как у резидента).
 */
export function kbExcludeEntry(raw: string): string | null {
  const text = raw.trim().replace(/\\/g, "/").replace(/\/\*{1,2}$/, "");
  if (!text || text.startsWith("/") || /^[A-Za-z]:/.test(text)) return null;
  const parts = text.split("/").filter((part) => part !== "");
  if (!parts.length || parts.some((part) => part === "." || part === "..")) return null;
  return `${parts.join("/")}/`;
}
