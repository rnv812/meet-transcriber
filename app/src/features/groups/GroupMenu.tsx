/**
 * Меню группы — у строки в левой панели («⋯», правая кнопка, Shift+F10) и у
 * заголовка группы над списком: Переименовать, Цвет ▸, Выше, Ниже, Удалить.
 * У неизвестной группы (id есть у встреч, а в списке нет) — «Назвать…» (с тем
 * же id) и «Убрать из встреч». Файл групп только для чтения — меню групп нет,
 * а у неизвестной остаётся «Убрать из встреч» (правится только meta.json встреч).
 */

import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Palette, Pencil, Trash2, Unlink } from "lucide-react";
import { useState, type RefObject } from "react";
import { CATEGORY_PALETTE } from "../../lib/categories";
import { UNKNOWN_NAME } from "../../lib/groups";
import { CategoryDot } from "../../ui/Category";
import { ItemMenu, type MenuItem } from "../recordings/ItemMenu";
import type { GroupsUi } from "./useGroupsUi";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;

export function GroupMenu({ ui, id, at, anchor, align = "start", onClose }: {
  ui: GroupsUi;
  id: string;
  /** Под указателем (правая кнопка); нет — под кнопкой `anchor`. */
  at?: { x: number; y: number } | null;
  anchor?: RefObject<HTMLElement | null>;
  align?: "start" | "end";
  /** Закрыть и вернуть фокус на кнопку меню (окно, открытое пунктом, вернёт его туда же). */
  onClose: () => void;
}) {
  const [colors, setColors] = useState(false);
  const [back, setBack] = useState(false);
  const order = ui.groups.map((g) => g.id);
  const group = ui.groups.find((g) => g.id === id);
  const index = order.indexOf(id);
  const run = (fn: () => unknown) => () => { onClose(); void fn(); };

  let items: MenuItem[];
  let label: string;
  if (!group) {
    label = `Действия с группой «${UNKNOWN_NAME}»`;
    items = [
      ...(ui.readOnly ? [] : [{ label: "Назвать…", icon: <Pencil {...ICON} />,
        hint: "Дать группе имя: встречи останутся в ней", onSelect: run(() => ui.nameUnknown(id)) }]),
      { label: "Убрать из встреч", icon: <Unlink {...ICON} />, danger: true, separator: !ui.readOnly,
        onSelect: run(() => ui.clearUnknown(id)) },
    ];
  } else if (colors) {
    label = `Цвет группы «${group.name}»`;
    const known = CATEGORY_PALETTE.some((p) => p.color.toLowerCase() === group.color.toLowerCase());
    items = [
      ...CATEGORY_PALETTE.map((p) => {
        const on = p.color.toLowerCase() === group.color.toLowerCase();
        return { label: p.name, icon: <CategoryDot color={p.color} />, checked: on, autoFocus: on,
          onSelect: run(() => (on ? undefined : ui.setColor(id, p.color))) };
      }),
      ...(known ? [] : [{ label: "Свой цвет", icon: <CategoryDot color={group.color} />, checked: true, autoFocus: true,
        onSelect: run(() => undefined) }]),
      { label: "Назад", separator: true, icon: <ChevronLeft {...ICON} />,
        onSelect: () => { setBack(true); setColors(false); } },
    ];
  } else {
    label = `Действия с группой «${group.name}»`;
    items = [
      { label: "Переименовать…", icon: <Pencil {...ICON} />, onSelect: run(() => ui.rename(id)) },
      { label: "Цвет", icon: <Palette {...ICON} />, trailing: <ChevronRight {...ICON} />, autoFocus: back,
        onSelect: () => setColors(true) },
      { label: "Выше", icon: <ArrowUp {...ICON} />, hint: "Alt+↑", disabled: index <= 0,
        onSelect: run(() => ui.shift(id, -1)) },
      { label: "Ниже", icon: <ArrowDown {...ICON} />, hint: "Alt+↓", disabled: index < 0 || index >= order.length - 1,
        onSelect: run(() => ui.shift(id, 1)) },
      { label: "Удалить", icon: <Trash2 {...ICON} />, danger: true, separator: true,
        hint: "Встречи останутся в списке; удаление можно отменить",
        onSelect: run(() => ui.remove(id)) },
    ];
  }

  return (
    <ItemMenu at={at} anchor={anchor} align={align} label={label} items={items}
      note={colors && group ? "Цвет группы" : undefined} onClose={onClose} />
  );
}
