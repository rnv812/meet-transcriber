/**
 * Группы встреч в окне: область списка (вся библиотека, группа, «Без группы»),
 * её запоминание, проверка названия (как у резидента, meet.groups) и порядок.
 *
 * У встречи одна группа или никакой. Id группы, которого нет в списке (группу
 * удалили, а встречи остались с её id), — «Группа без названия».
 */

import { plural } from "./format";
import type { GroupInfo } from "./types";

/** «Без группы» в фильтре `groups=` и в области списка (id групп с «_» не начинаются). */
export const NO_GROUP = "_none";
export const NO_GROUP_NAME = "Без группы";
export const ALL_NAME = "Все записи";
export const UNKNOWN_NAME = "Группа без названия";
/** Название группы не длиннее (резидент проверяет так же). */
export const GROUP_NAME_MAX = 60;

/** Область списка: null — все записи, NO_GROUP — без группы, иначе id группы. */
export type GroupScope = string | null;

const SCOPE_KEY = "meet.groupScope";
/** Годный id группы — как у резидента (`^[a-z0-9][a-z0-9_-]{0,39}$`). */
const GROUP_ID = /^[a-z0-9][a-z0-9_-]{0,39}$/;

export const isScope = (value: unknown): value is string =>
  typeof value === "string" && (value === NO_GROUP || GROUP_ID.test(value));

/** Запомненная область списка; хранилища нет или там мусор — все записи. */
export function loadGroupScope(): GroupScope {
  try {
    const raw = window.localStorage.getItem(SCOPE_KEY);
    const got: unknown = raw ? JSON.parse(raw) : null;
    return isScope(got) ? got : null;
  } catch {
    return null;
  }
}

export function saveGroupScope(scope: GroupScope): void {
  try {
    if (scope) window.localStorage.setItem(SCOPE_KEY, JSON.stringify(scope));
    else window.localStorage.removeItem(SCOPE_KEY);
  } catch {
    /* область просто не запомнится */
  }
}

/** «1 встреча», «3 встречи», «12 встреч». */
export const meetingsText = (n: number) => `${n} ${plural(n, "встреча", "встречи", "встреч")}`;

/** Пробелы схлопываются (резидент делает так же: `" ".join(name.split())`). */
export const cleanGroupName = (name: string) => name.split(/\s+/).filter(Boolean).join(" ");

const fold = (name: string) => name.toLocaleLowerCase("ru").replace(/ё/g, "е");

/**
 * Что не так с названием группы — теми же словами, что ответил бы резидент;
 * всё хорошо — null. `self` — переименовываемая группа (её прежнее имя не повтор).
 */
export function groupNameError(name: string, groups: Pick<GroupInfo, "id" | "name">[], self?: string): string | null {
  const clean = cleanGroupName(name);
  if (!clean) return "Нужно название группы";
  if ([...clean].length > GROUP_NAME_MAX) return `Название группы — не длиннее ${GROUP_NAME_MAX} символов`;
  if (fold(clean) === fold(ALL_NAME)) return `«${ALL_NAME}» — не название группы`;
  if (groups.some((g) => g.id !== self && fold(g.name) === fold(clean))) return `Группа «${clean}» уже есть`;
  return null;
}

/** Ответ резидента («группа «X» уже есть») — с заглавной буквы, как подпись в окне. */
export const sentence = (text: string) => (text ? text[0]!.toLocaleUpperCase("ru") + text.slice(1) : text);

/**
 * Новый порядок: `moving` встаёт перед элементом, который сейчас на месте
 * `index` (0…длина; длина — в конец). Тот же порядок — тот же массив.
 */
export function reorderIds(ids: string[], moving: string, index: number): string[] {
  const from = ids.indexOf(moving);
  if (from < 0) return ids;
  const to = Math.max(0, Math.min(ids.length, index));
  if (to === from || to === from + 1) return ids;
  const rest = ids.filter((id) => id !== moving);
  rest.splice(to > from ? to - 1 : to, 0, moving);
  return rest;
}

/** Группа на шаг выше (-1) или ниже (+1); у края — тот же массив. */
export function shiftId(ids: string[], id: string, delta: -1 | 1): string[] {
  const at = ids.indexOf(id);
  if (at < 0) return ids;
  return reorderIds(ids, id, delta < 0 ? at - 1 : at + 2);
}
