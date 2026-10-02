/**
 * Фильтр списка записей по категориям: кнопка «Категории» рядом с поиском,
 * в окошке — флажки с числом встреч (среди найденных поиском) и «Без
 * категории». Выбрано несколько — показываются записи любой из них; поиск и
 * фильтр действуют вместе. Включённый фильтр виден меткой под поиском, «✕» на
 * ней — снять его.
 */

import { useRef, useState } from "react";
import { NO_CATEGORY, NO_CATEGORY_NAME } from "../../lib/categories";
import type { Category } from "../../lib/types";
import { CategoryDot } from "../../ui/Category";
import { Popover } from "../../ui/Popover";

export function CategoryFilter({ list, counts, selected, onChange }: {
  list: Category[];
  /** Сколько записей (из найденных) в каждой категории; ключ «Без категории» — NO_CATEGORY. */
  counts: Map<string, number>;
  /** Выбранные ключи (id категорий, NO_CATEGORY); пусто — фильтр выключен. */
  selected: string[];
  onChange: (keys: string[]) => void;
}) {
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const options = [{ key: NO_CATEGORY, name: NO_CATEGORY_NAME, color: null as string | null },
    ...list.map((c) => ({ key: c.id, name: c.name, color: c.color }))];
  const toggle = (key: string) => onChange(
    selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key]);
  const close = () => { setOpen(false); button.current?.focus(); };

  return (
    <>
      <button ref={button} type="button" aria-haspopup="dialog" aria-expanded={open}
        className={`cat-filter__button${selected.length ? " cat-filter__button--active" : ""}`}
        title="Показать записи только выбранных категорий"
        onClick={() => setOpen((v) => !v)}>
        {selected.length ? `Категории · ${selected.length}` : "Категории"}
      </button>
      {open && button.current && (
        <Popover anchor={button.current} label="Фильтр по категориям" width={250} onClose={close}>
          <fieldset className="cat-filter__list">
            <legend className="eyebrow cat-filter__legend">Показывать категории</legend>
            {options.map((o, i) => (
              <label key={o.key} className="cat-filter__option">
                <input type="checkbox" checked={selected.includes(o.key)} autoFocus={i === 0}
                  onChange={() => toggle(o.key)} />
                <CategoryDot color={o.color} />
                <span className="cat-filter__name">{o.name}</span>
                <span className="cat-filter__count" aria-label={`записей: ${counts.get(o.key) ?? 0}`}>
                  {counts.get(o.key) ?? 0}
                </span>
              </label>
            ))}
          </fieldset>
          {selected.length > 0 && (
            <div className="cat-filter__foot">
              <button type="button" className="btn" onClick={() => onChange([])}>Показать все</button>
            </div>
          )}
        </Popover>
      )}
    </>
  );
}

/** Метка включённого фильтра под поиском: какие категории, «✕» — снять. */
export function CategoryFilterChip({ list, selected, onClear }: {
  list: Category[];
  selected: string[];
  onClear: () => void;
}) {
  const names = selected.map((k) => (k === NO_CATEGORY ? NO_CATEGORY_NAME : list.find((c) => c.id === k)?.name ?? k));
  return (
    <span className="cat-filter__chip">
      <span className="cat-filter__chip-text" title={names.join(", ")}>{`Категории: ${names.join(", ")}`}</span>
      <button type="button" className="cat-filter__clear" aria-label="Снять фильтр по категориям" onClick={onClear}>✕</button>
    </span>
  );
}
