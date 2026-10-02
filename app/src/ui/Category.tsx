/**
 * Категория встречи: цветная метка (`CategoryChip`) и меню выбора
 * (`CategoryMenu`) — в карточке оно открывается по метке под названием.
 *
 * Меню — обычное меню с одиночным выбором: фокус на текущей категории,
 * ↑/↓/Home/End — по пунктам, Enter или пробел — выбрать, Esc — закрыть
 * (это делает Popover вокруг меню).
 */

import { useEffect, useRef, type KeyboardEvent } from "react";
import { NO_CATEGORY_NAME } from "../lib/categories";
import type { Category } from "../lib/types";
import "./category.css";

/** Цветная точка категории; без цвета — пустой кружок «Без категории». */
export function CategoryDot({ color }: { color?: string | null }) {
  return (
    <span className={`cat-dot${color ? "" : " cat-dot--none"}`} aria-hidden="true"
      style={color ? { background: color } : undefined} />
  );
}

/** Метка категории (span): в списке записей она внутри кнопки записи. */
export function CategoryChip({ category, small = false }: { category: Category | null; small?: boolean }) {
  return (
    <span className={`cat-chip${small ? " cat-chip--small" : ""}${category ? "" : " cat-chip--none"}`}
      title={category?.description || undefined}>
      <CategoryDot color={category?.color} />
      <span className="cat-chip__name">{category?.name ?? NO_CATEGORY_NAME}</span>
    </span>
  );
}

export function CategoryMenu({ list, current, onPick, onSettings, label = "Категория встречи" }: {
  list: Category[];
  /** id выбранной категории; null — «Без категории». */
  current: string | null;
  onPick: (id: string | null) => void;
  /** «Настроить категории…» — раздел настроек; нет — пункта нет. */
  onSettings?: () => void;
  label?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const known = current !== null && list.some((c) => c.id === current) ? current : null;

  useEffect(() => {
    (box.current?.querySelector<HTMLButtonElement>("[aria-checked=true]")
      ?? box.current?.querySelector<HTMLButtonElement>("[role^=menuitem]"))?.focus();
  }, []);

  const onKeyDown = (e: KeyboardEvent) => {
    const all = [...(box.current?.querySelectorAll<HTMLButtonElement>("[role^=menuitem]") ?? [])];
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    let next = -1;
    if (e.key === "ArrowDown") next = (at + 1) % all.length;
    else if (e.key === "ArrowUp") next = (at - 1 + all.length) % all.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = all.length - 1;
    if (next >= 0) {
      e.preventDefault();
      all[next]?.focus();
    }
  };

  const option = (id: string | null, name: string, color: string | null, hint?: string) => (
    <button key={id ?? "_none"} type="button" role="menuitemradio" aria-checked={known === id} tabIndex={-1}
      className={`cat-menu__item${known === id ? " cat-menu__item--current" : ""}`} title={hint || undefined}
      onClick={() => onPick(id)}>
      <CategoryDot color={color} />
      <span className="cat-menu__name">{name}</span>
      {known === id && <span className="cat-menu__check" aria-hidden="true">✓</span>}
    </button>
  );

  return (
    <div ref={box} className="cat-menu" role="menu" aria-label={label} onKeyDown={onKeyDown}>
      <div className="cat-menu__list">
        {option(null, NO_CATEGORY_NAME, null)}
        {list.map((c) => option(c.id, c.name, c.color, c.description))}
      </div>
      {onSettings && (
        <>
          <div className="cat-menu__sep" role="separator" />
          <button type="button" role="menuitem" tabIndex={-1} className="cat-menu__item cat-menu__item--action"
            onClick={onSettings}>
            Настроить категории…
          </button>
        </>
      )}
    </div>
  );
}
