/**
 * «Фильтры» рядом со строкой поиска (вместо прежней кнопки «Категории»): окно с измерениями —
 * Категория, Группа, Участник, Есть, Длительность, Период. У значений — число встреч при
 * нынешнем запросе (`GET /facets`: каждое измерение считается без своего условия — видно, что
 * даст щелчок). Ноль — бледнее, но выбрать можно.
 *
 * Панель и метки под поиском — два вида одного состояния: щелчок ставит или снимает ту же метку,
 * что дал бы префикс в строке (`категория:`, `участник:`…). Категории запоминаются (как раньше),
 * остальное — на сеанс. Счётчики — с задержкой, прежний запрос отменяется.
 *
 * Резидент до 0.3.5 (`/facets` нет — 404) понимает только категории: прочие измерения
 * скрыты — их условия он молча пропустил бы.
 */

import { ListFilter } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, getCategoriesInfo, getFacets, libraryFilterKey, type Endpoint } from "../../lib/api";
import { NO_CATEGORY, NO_CATEGORY_NAME } from "../../lib/categories";
import { DATE_PRESETS, dateHint, parseDateExpr } from "../../lib/dateExpr";
import {
  chipOf, chipText, hasChip, NO_GROUP, NO_GROUP_NAME, sameChip, type Chip, type ChipKind, type GroupRef,
} from "../../lib/libraryQuery";
import { searchable } from "../../lib/search";
import type { Category, Facets, LibraryFilter, LibraryHas } from "../../lib/types";
import { Button } from "../../ui/Button";
import { CategoryDot } from "../../ui/Category";
import { IconButton } from "../../ui/IconButton";
import { Popover } from "../../ui/Popover";
import { Tip } from "../../ui/Tip";
import { Truncate } from "../../ui/Truncate";

/** Счётчики — не на каждую букву поиска. */
export const FACETS_DELAY_MS = 250;

/** Длительность: три готовых промежутка (как у `/facets`), каждый — одна или две метки. */
const DURATIONS: { key: keyof Facets["duration"]; label: string; chips: Chip[] }[] = [
  { key: "lt15", label: "Меньше 15 мин", chips: [{ kind: "shorter", value: "900" }] },
  { key: "m15_60", label: "15–60 мин", chips: [{ kind: "longer", value: "900" }, { kind: "shorter", value: "3600" }] },
  { key: "gt60", label: "Больше 1 ч", chips: [{ kind: "longer", value: "3600" }] },
];
const HAS_OPTIONS: { value: LibraryHas; label: string }[] = [
  { value: "summary", label: "Итоги" }, { value: "analysis", label: "Анализ" }, { value: "assistant", label: "Ассистент" },
];
/** Период: готовые варианты из DATE_PRESETS. */
const PERIODS = ["эта неделя", "этот месяц"].filter((p) => DATE_PRESETS.includes(p));

type Counts = {
  facets: Facets | null; categories: Record<string, number> | null; categoriesNone: number | null;
  /** Резидент без `/facets`: только категории. */
  legacy: boolean;
};

export function FiltersButton({
  endpoint, q, filter, chips, categories, groups, now, onToggle, onReplace, onClear, onMorePeople, onOpen, scopeName,
}: {
  endpoint: Endpoint | null;
  /** Область группы из кнопки-списка над списком (её имя): числа — в ней, а не по всей библиотеке. */
  scopeName?: string | null;
  /** Текст поиска: при поиске числа — среди найденного. */
  q: string;
  /** Нынешний фильтр (все условия) — для счётчиков. */
  filter: LibraryFilter;
  /** Метки: запомненные категории и метки сеанса. */
  chips: Chip[];
  categories: Category[];
  /** Группы; null — резидент без групп (измерения нет). */
  groups: GroupRef[] | null;
  now: Date;
  /** Поставить или снять метку. */
  onToggle: (chip: Chip) => void;
  /** Заменить все метки этих видов (длительность, период — по одному варианту). */
  onReplace: (kinds: ChipKind[], add: Chip[]) => void;
  /** Снять все условия. */
  onClear: () => void;
  /** «ещё…» у участников: к строке поиска с `участник:`. */
  onMorePeople: () => void;
  /** Окно открывается: недописанные префиксы строки — сначала в метки. */
  onOpen?: () => void;
}) {
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [counts, setCounts] = useState<Counts>({ facets: null, categories: null, categoriesNone: null, legacy: false });
  const [period, setPeriod] = useState("");
  const scopeQ = searchable(q) ? q : "";
  const key = libraryFilterKey(filter);
  const filterRef = useRef(filter);
  filterRef.current = filter;
  const first = useRef(true);

  // Счётчики, пока окно открыто: при открытии — сразу, при смене условий — с задержкой.
  useEffect(() => {
    if (!open || !endpoint) { first.current = true; return; }
    const controller = new AbortController();
    const delay = first.current ? 0 : FACETS_DELAY_MS;
    first.current = false;
    const timer = setTimeout(() => {
      const f = libraryFilterKey(filterRef.current) ? filterRef.current : undefined;
      getFacets(endpoint, scopeQ || undefined, f, controller.signal)
        .then((facets) => {
          if (!controller.signal.aborted) setCounts({ facets, categories: null, categoriesNone: null, legacy: false });
        })
        .catch((cause) => {
          if (controller.signal.aborted) return;
          // Резидент до 0.3.5 без /facets: числа хотя бы у категорий, как раньше.
          if (cause instanceof ApiError && cause.status === 404) {
            setCounts({ facets: null, categories: null, categoriesNone: null, legacy: true });
            getCategoriesInfo(endpoint, scopeQ || undefined)
              .then((info) => {
                if (!controller.signal.aborted) {
                  setCounts({ facets: null, categories: info.counts, categoriesNone: info.none, legacy: true });
                }
              })
              .catch(() => {});
          }
        });
    }, delay);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [open, endpoint, scopeQ, key]);

  const close = () => { setOpen(false); setPeriod(""); button.current?.focus(); };
  const facets = counts.facets;
  const ctx = { categories, groups: groups ?? [] };
  const where = scopeName ? `в «${scopeName}»` : "по всей библиотеке";
  const scopeNote = facets ? (facets.scope === "search"
    ? `Число встреч — среди найденных поиском${scopeName ? ` в «${scopeName}»` : ""}` : `Число встреч — ${where}`)
    : counts.categories ? `Число встреч — ${where}` : "";

  const catCount = (id: string): number | null => {
    if (facets) return id === NO_CATEGORY ? facets.categories.none : facets.categories.items.find((x) => x.id === id)?.count ?? 0;
    if (counts.categories) return id === NO_CATEGORY ? counts.categoriesNone : counts.categories[id] ?? 0;
    return null;
  };
  const groupCount = (id: string): number | null => {
    if (!facets) return null;
    if (id === NO_GROUP) return facets.groups.none;
    return facets.groups.items.find((x) => x.id === id)?.count ?? facets.groups.unknown.find((x) => x.id === id)?.count ?? 0;
  };

  // --- варианты измерений ---------------------------------------------------------
  const catOptions = [{ id: NO_CATEGORY, name: NO_CATEGORY_NAME, color: null as string | null },
    ...categories.map((c) => ({ id: c.id, name: c.name, color: c.color as string | null }))];
  const unknownGroups = (facets?.groups.unknown ?? []).filter((u) => !(groups ?? []).some((g) => g.id === u.id));
  const groupOptions = groups === null ? [] : [{ id: NO_GROUP, name: NO_GROUP_NAME, color: null as string | null },
    ...groups.map((g) => ({ id: g.id, name: g.name, color: g.color ?? null })),
    ...unknownGroups.map((u) => ({ id: u.id, name: "Группа без названия", color: null }))];
  const chosenPeople = chips.filter((c) => c.kind === "person").map((c) => c.value);
  const people = [...(facets?.people ?? []).map((p) => ({ name: p.name, count: p.count as number | null })),
    ...chosenPeople.filter((n) => !facets?.people.some((p) => p.name === n)).map((name) => ({ name, count: null }))];
  const durations = chips.filter((c) => c.kind === "longer" || c.kind === "shorter");
  const durationOn = (d: (typeof DURATIONS)[number]) =>
    d.chips.length === durations.length && d.chips.every((c) => hasChip(durations, c));
  const dateChip = chips.find((c) => c.kind === "date");
  const periodChips = PERIODS.map((p) => ({ text: p, chip: chipOf("дата", p, { ...ctx, now })! }));
  const customDate = dateChip && !periodChips.some((p) => sameChip(p.chip, dateChip)) ? dateChip : null;
  const typedPeriod = period.trim() ? parseDateExpr(period, now) : null;
  const applyPeriod = () => {
    const chip = chipOf("дата", period, { ...ctx, now });
    if (!chip) return;
    onReplace(["date"], [chip]);
    setPeriod("");
  };

  const active = chips.length;
  const option = (key: string, label: string, checked: boolean, count: number | null, onChange: () => void,
    dot?: string | null, autoFocus = false) => (
    <label key={key} className={`cat-filter__option${count === 0 ? " filters__option--zero" : ""}`}>
      <input type="checkbox" className="cb" checked={checked} onChange={onChange} autoFocus={autoFocus} />
      {dot !== undefined && <CategoryDot color={dot} />}
      <Truncate className="cat-filter__name">{label}</Truncate>
      <span className="cat-filter__option-count" aria-hidden="true">{count ?? ""}</span>
    </label>
  );
  const legacy = counts.legacy;
  const showCategories = categories.length > 0 || chips.some((c) => c.kind === "category");
  const showGroups = !legacy && groups !== null
    && (groups.length > 0 || unknownGroups.length > 0 || chips.some((c) => c.kind === "group"));

  return (
    <>
      {/* Кнопка-значок с контуром (макет LIBRARY) — высотой с поле поиска. Число условий — значком
          поверх неё: ширина кнопки не меняется, поиск не сжимается. */}
      <span className={`rec-filter${active ? " rec-filter--active" : ""}`}>
        <IconButton ref={button} icon={ListFilter} variant="secondary" size="md" aria-haspopup="dialog"
          aria-expanded={open} label={active ? `Фильтры · ${active}` : "Фильтры"}
          tooltip="Фильтры: категория, группа, участник, период, длительность"
          onClick={() => { if (!open) onOpen?.(); setOpen((v) => !v); }} />
        {active > 0 && <span className="cat-filter__count num" aria-hidden="true">{active}</span>}
      </span>
      {open && button.current && (
        <Popover anchor={button.current} label="Фильтры" width={290} align="end" onClose={close} anchorToggles>
          <div className="filters">
            {showCategories && (
              <fieldset className="cat-filter__list filters__dim">
                <legend className="eyebrow cat-filter__legend">Категория</legend>
                {catOptions.map((o, i) => option(`c:${o.id}`, o.name, hasChip(chips, { kind: "category", value: o.id }),
                  catCount(o.id), () => onToggle({ kind: "category", value: o.id }), o.color, i === 0))}
              </fieldset>
            )}
            {showGroups && (
              <fieldset className="cat-filter__list filters__dim">
                <legend className="eyebrow cat-filter__legend">Группа</legend>
                {groupOptions.map((o, i) => option(`g:${o.id}`, o.name, hasChip(chips, { kind: "group", value: o.id }),
                  groupCount(o.id), () => onToggle({ kind: "group", value: o.id }), o.color, !showCategories && i === 0))}
              </fieldset>
            )}
            {!legacy && (<>
            <fieldset className="cat-filter__list filters__dim">
              <legend className="eyebrow cat-filter__legend">Участник</legend>
              {people.length === 0 && <span className="filters__empty">{facets ? "Имён в расшифровках нет" : "…"}</span>}
              {people.map((p) => option(`p:${p.name}`, p.name, chosenPeople.includes(p.name), p.count,
                () => onToggle({ kind: "person", value: p.name })))}
              <button type="button" className="link filters__more" onClick={() => { setOpen(false); onMorePeople(); }}>
                ещё…
              </button>
            </fieldset>
            <fieldset className="cat-filter__list filters__dim">
              <legend className="eyebrow cat-filter__legend">Есть</legend>
              {HAS_OPTIONS.map((h) => option(`h:${h.value}`, h.label, hasChip(chips, { kind: "has", value: h.value }),
                facets ? facets.has[h.value] : null, () => onToggle({ kind: "has", value: h.value })))}
            </fieldset>
            <fieldset className="cat-filter__list filters__dim">
              <legend className="eyebrow cat-filter__legend">Длительность</legend>
              {DURATIONS.map((d) => option(`d:${d.key}`, d.label, durationOn(d), facets ? facets.duration[d.key] : null,
                () => onReplace(["longer", "shorter"], durationOn(d) ? [] : d.chips)))}
            </fieldset>
            <fieldset className="cat-filter__list filters__dim">
              <legend className="eyebrow cat-filter__legend">Период</legend>
              {periodChips.map((p) => option(`t:${p.text}`, chipText(p.chip, ctx), !!dateChip && sameChip(dateChip, p.chip), null,
                () => onReplace(["date"], dateChip && sameChip(dateChip, p.chip) ? [] : [p.chip])))}
              {customDate && option("t:custom", chipText(customDate, ctx), true, null, () => onReplace(["date"], []))}
              <div className="filters__period">
                <Tip content="Например: 5 окт, сентябрь, с 1.09 по 15.09, прошлая неделя">
                  <input type="text" className="field field--sm filters__period-input" aria-label="Выбрать период"
                    placeholder="Выбрать… 5 окт, сентябрь"
                    value={period}
                    onChange={(e) => setPeriod(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); applyPeriod(); } }} />
                </Tip>
                {period.trim() && (
                  <span className={`filters__period-note${typedPeriod ? "" : " filters__period-note--bad"}`} role="status">
                    {typedPeriod ? typedPeriod.label : dateHint(period, now) ?? "Не понял дату"}
                  </span>
                )}
                {typedPeriod && <Button onClick={applyPeriod}>Применить</Button>}
              </div>
            </fieldset>
            </>)}
            {legacy && (
              <p className="filters__empty">Служба записи старой версии: остальные фильтры появятся после обновления.</p>
            )}
          </div>
          <div className="cat-filter__foot">
            <span className="cat-filter__scope">{scopeNote}</span>
            {active > 0 && <Button onClick={onClear}>Сбросить все</Button>}
          </div>
        </Popover>
      )}
    </>
  );
}
