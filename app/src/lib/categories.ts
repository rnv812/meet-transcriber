/**
 * Категории встреч в окне: какая категория у записи, палитра цветов, новые id,
 * фильтр списка по категориям.
 *
 * Категория, которой больше нет в настройках (её удалили), показывается как
 * «Без категории»: meta.json записи не правится, и «Сбросить к стандартным»
 * возвращает встречам их категории.
 */

import type { Category, Recording } from "./types";

export const NO_CATEGORY_NAME = "Без категории";
/** Ключ «Без категории» в фильтре списка (id категорий — латиница без «_» в начале). */
export const NO_CATEGORY = "_none";

/** Палитра: заметные на тёмной теме цвета, различимые между собой. */
export const CATEGORY_PALETTE: { color: string; name: string }[] = [
  { color: "#4c8bf5", name: "Синий" },
  { color: "#3aa7b8", name: "Бирюзовый" },
  { color: "#2fa36b", name: "Зелёный" },
  { color: "#5a9e3a", name: "Травяной" },
  { color: "#c9a227", name: "Жёлтый" },
  { color: "#e08a2e", name: "Оранжевый" },
  { color: "#e5484d", name: "Красный" },
  { color: "#d6457a", name: "Розовый" },
  { color: "#8e6cd8", name: "Фиолетовый" },
  { color: "#a0703c", name: "Коричневый" },
  { color: "#9aa0a6", name: "Серый" },
];

/** Категория записи из списка настроек; нет, «Без категории» или удалена — null. */
export function categoryOf(rec: Pick<Recording, "category">, list: Category[]): Category | null {
  const id = rec.category?.id;
  return id ? list.find((c) => c.id === id) ?? null : null;
}

/** Ключ записи для фильтра: id известной категории или NO_CATEGORY. */
export function categoryKey(rec: Pick<Recording, "category">, list: Category[]): string {
  return categoryOf(rec, list)?.id ?? NO_CATEGORY;
}

const TRANSLIT: Record<string, string> = {
  а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y", к: "k", л: "l",
  м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "ts", ч: "ch", ш: "sh",
  щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
};

/** Латинская основа id по имени: «Встреча с клиентом» → "vstrecha-s-klientom". */
export function slugOf(name: string): string {
  const latin = [...name.toLowerCase()].map((ch) => TRANSLIT[ch] ?? ch).join("");
  return latin.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 20).replace(/-+$/, "");
}

/**
 * id новой категории: основа по имени и случайный хвост (id не меняется при
 * переименовании и не совпадает с удалённой когда-то категорией). Формат —
 * как проверяет резидент: латиница, цифры, «-», не длиннее 32.
 */
export function newCategoryId(name: string, taken: Iterable<string>, random: () => number = Math.random): string {
  const used = new Set(taken);
  const base = slugOf(name) || "cat";
  for (;;) {
    const tail = Math.floor(random() * 36 ** 4).toString(36).padStart(4, "0");
    const id = `${base}-${tail}`;
    if (!used.has(id)) return id;
  }
}

// --- фильтр списка -----------------------------------------------------------

const FILTER_KEY = "meet.categoryFilter";

/** Выбранные в фильтре категории (у этого окна); хранилища нет — пусто. */
export function loadCategoryFilter(): string[] {
  try {
    const raw = window.localStorage.getItem(FILTER_KEY);
    const got: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(got) ? got.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function saveCategoryFilter(keys: string[]): void {
  try {
    if (keys.length) window.localStorage.setItem(FILTER_KEY, JSON.stringify(keys));
    else window.localStorage.removeItem(FILTER_KEY);
  } catch {
    /* фильтр просто не запомнится */
  }
}
