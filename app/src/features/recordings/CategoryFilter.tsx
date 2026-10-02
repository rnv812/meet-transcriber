/**
 * Фильтр списка записей по категориям: кнопка «Категории» рядом с поиском,
 * в окошке — флажки с числом встреч и «Без категории». Выбрано несколько —
 * показываются записи любой из них; поиск и фильтр действуют вместе.
 *
 * Фильтрует резидент (до лимита списка), счётчики — тоже от него
 * (`GET /categories`): по всей библиотеке, а при поиске — среди найденных.
 * Включённый фильтр виден метками под поиском: у каждой категории своя «✕»,
 * при двух и больше — «Сбросить».
 */

import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getCategoriesInfo, type CategoriesInfo, type Endpoint } from "../../lib/api";
import { NO_CATEGORY, NO_CATEGORY_NAME } from "../../lib/categories";
import { searchable } from "../../lib/search";
import type { Category } from "../../lib/types";
import { CategoryDot } from "../../ui/Category";
import { Popover } from "../../ui/Popover";
import { Icon } from "../../ui/Icon";

export function CategoryFilter({ list, endpoint, q, selected, onChange }: {
  list: Category[];
  /** Откуда счётчики; нет резидента — без счётчиков. */
  endpoint: Endpoint | null;
  /** Строка поиска: при поиске счётчики — среди найденного. */
  q: string;
  /** Выбранные ключи (id категорий, NO_CATEGORY); пусто — фильтр выключен. */
  selected: string[];
  onChange: (keys: string[]) => void;
}) {
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<CategoriesInfo | null>(null);
  const scopeQ = searchable(q) ? q : "";

  // Счётчики — при каждом открытии (библиотека и поиск могли измениться).
  useEffect(() => {
    if (!open || !endpoint) return;
    let live = true;
    getCategoriesInfo(endpoint, scopeQ || undefined).then((got) => { if (live) setInfo(got); }).catch(() => {});
    return () => { live = false; };
  }, [open, endpoint, scopeQ]);

  const options = [{ key: NO_CATEGORY, name: NO_CATEGORY_NAME, color: null as string | null },
    ...list.map((c) => ({ key: c.id, name: c.name, color: c.color }))];
  const count = (key: string) => (info ? (key === NO_CATEGORY ? info.none : info.counts[key] ?? 0) : null);
  const toggle = (key: string) => onChange(
    selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key]);
  const close = () => { setOpen(false); button.current?.focus(); };
  const scopeNote = info?.scope === "search" ? "Число встреч — среди найденных поиском"
    : "Число встреч — по всей библиотеке";

  return (
    <>
      <button ref={button} type="button" aria-haspopup="dialog" aria-expanded={open}
        className={`cat-filter__button${selected.length ? " cat-filter__button--active" : ""}`}
        title="Показать записи только выбранных категорий"
        aria-label={selected.length ? `Категории · ${selected.length}` : "Категории"}
        onClick={() => setOpen((v) => !v)}>
        Категории
        {/* Число выбранных — значком поверх кнопки: ширина кнопки не меняется, поиск не сжимается. */}
        {selected.length > 0 && <span className="cat-filter__count num" aria-hidden="true">{selected.length}</span>}
      </button>
      {open && button.current && (
        <Popover anchor={button.current} label="Фильтр по категориям" width={250} onClose={close} anchorToggles>
          <fieldset className="cat-filter__list">
            <legend className="eyebrow cat-filter__legend">Показывать категории</legend>
            {options.map((o, i) => (
              <label key={o.key} className="cat-filter__option">
                <input type="checkbox" checked={selected.includes(o.key)} autoFocus={i === 0}
                  onChange={() => toggle(o.key)} />
                <CategoryDot color={o.color} />
                <span className="cat-filter__name">{o.name}</span>
                <span className="cat-filter__count" aria-hidden="true" title={scopeNote}>{count(o.key) ?? ""}</span>
              </label>
            ))}
          </fieldset>
          <div className="cat-filter__foot">
            <span className="cat-filter__scope">{info ? scopeNote : ""}</span>
            {selected.length > 0 && (
              <button type="button" className="btn" onClick={() => onChange([])}>Показать все</button>
            )}
          </div>
        </Popover>
      )}
    </>
  );
}

/** Метки включённого фильтра под поиском: по одной на категорию, «✕» — убрать её; две и больше — «Сбросить». */
export function CategoryFilterChips({ list, selected, onChange }: {
  list: Category[];
  selected: string[];
  onChange: (keys: string[]) => void;
}) {
  return (
    <div className="rec-list__filters" role="group" aria-label="Фильтр по категориям">
      {selected.map((key) => {
        const category = list.find((c) => c.id === key);
        const name = key === NO_CATEGORY ? NO_CATEGORY_NAME : category?.name ?? key;
        return (
          <span key={key} className="cat-filter__chip">
            <CategoryDot color={key === NO_CATEGORY ? null : category?.color} />
            <span className="cat-filter__chip-text" title={name}>{name}</span>
            <button type="button" className="cat-filter__clear" aria-label={`Убрать «${name}» из фильтра`}
              onClick={() => onChange(selected.filter((k) => k !== key))}><Icon as={X} size="sm" /></button>
          </span>
        );
      })}
      {selected.length >= 2 && (
        <button type="button" className="link cat-filter__reset" onClick={() => onChange([])}>Сбросить</button>
      )}
    </div>
  );
}
