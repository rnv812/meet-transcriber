/**
 * «Переместить в группу ▸»: пункты одиночного выбора (`menuitemradio`) — каждая
 * группа, «Без группы», черта и «Новая группа…» (создать и сразу перенести).
 * Один и тот же список — в меню записи (одна встреча или все выбранные) и у
 * кнопки «В группу» на панели выбора.
 */

import { FolderPlus } from "lucide-react";
import { NO_GROUP_NAME } from "../../lib/groups";
import type { GroupInfo } from "../../lib/types";
import { CategoryDot } from "../../ui/Category";
import type { MenuItem } from "../recordings/ItemMenu";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;

export type MoveToGroup = {
  /** Группы на момент открытия меню (функция: строки списка не перерисовываются от каждого перечитывания). */
  groups: () => GroupInfo[];
  /** Какие встречи переносит пункт у записи `id`: выбранные, если она среди них, иначе она одна. */
  targets: (id: string) => string[];
  /** Общая группа встреч: id, null — все без группы, undefined — группы разные. */
  current: (ids: string[]) => string | null | undefined;
  move: (ids: string[], group: string | null) => void;
  /** «Новая группа…» — создать и перенести в неё; нет — пункта нет (файл групп только для чтения). */
  create?: (ids: string[]) => void;
};

/**
 * Пункты выбора группы для встреч `ids`; `close` — закрыть меню до действия (фокус
 * — на кнопку меню; окно названия вернёт его туда же). `tail` — пункты в конце
 * («Назад» в подменю записи).
 */
export function moveItems(m: MoveToGroup, ids: string[], close: () => void,
  tail: MenuItem[] = []): MenuItem[] {
  const now = m.current(ids);
  const pick = (group: string | null) => () => { close(); if (now !== group) m.move(ids, group); };
  return [
    ...m.groups().map((g) => ({
      label: g.name, icon: <CategoryDot color={g.color} />, checked: now === g.id, autoFocus: now === g.id,
      onSelect: pick(g.id),
    })),
    { label: NO_GROUP_NAME, icon: <CategoryDot />, checked: now === null, autoFocus: now === null,
      onSelect: pick(null) },
    ...(m.create ? [{
      label: "Новая группа…", separator: true, icon: <FolderPlus {...ICON} />,
      onSelect: () => { close(); m.create?.(ids); },
    }] : []),
    ...tail.map((item, i) => (i === 0 && !m.create ? { ...item, separator: true } : item)),
  ];
}
