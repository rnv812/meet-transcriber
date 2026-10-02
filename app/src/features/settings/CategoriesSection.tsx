/**
 * Настройки «Категории встреч»: список категорий (порядок — порядок в меню и
 * фильтре), цвет, название и описание для ИИ; добавить, удалить, «Сбросить к
 * стандартным». Список — часть черновика настроек: применяется кнопкой
 * «Сохранить» (`PATCH /settings` с `categories` целиком).
 *
 * id категории не меняется при переименовании; у новой он появляется при
 * сохранении (`categoriesToSave`). Удалённая категория у встреч просто
 * перестаёт показываться («Без категории»): их meta.json не правится, и
 * «Сбросить к стандартным» вернёт стандартные категории встречам.
 */

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";
import { getCategoriesInfo, type CategoriesInfo, type Endpoint } from "../../lib/api";
import { CATEGORY_PALETTE, NO_CATEGORY_NAME, newCategoryId } from "../../lib/categories";
import type { Category } from "../../lib/types";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { IconButton } from "../../ui/IconButton";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Popover } from "../../ui/Popover";

/** Категория в черновике: у новой id пустой, `key` — только для окна. */
export type DraftCategory = Category & { key?: string };

const NAME_MAX = 40;
const DESCRIPTION_MAX = 200;
const ICON = { size: 15, strokeWidth: 1.75, "aria-hidden": true } as const;

export function draftCategories(value: unknown): DraftCategory[] {
  if (!Array.isArray(value)) return [];
  return value.filter((c): c is DraftCategory => !!c && typeof c === "object"
    && typeof (c as Category).id === "string" && typeof (c as Category).name === "string")
    .map((c) => ({ ...c, color: typeof c.color === "string" ? c.color : "#9aa0a6",
      description: typeof c.description === "string" ? c.description : "" }));
}

const clean = (s: string) => s.trim().split(/\s+/).filter(Boolean).join(" ");
const norm = (s: string) => clean(s).toLowerCase().replace(/ё/g, "е");

/** Что не так со списком (пустое или повторённое название); всё хорошо — null. */
export function categoriesError(list: DraftCategory[]): string | null {
  if (list.some((c) => !clean(c.name))) return "У каждой категории должно быть название";
  if (list.some((c) => norm(c.name) === norm(NO_CATEGORY_NAME))) {
    return `Название «${NO_CATEGORY_NAME}» занято: так обозначаются встречи без категории`;
  }
  const names = list.map((c) => norm(c.name));
  if (names.some((n, i) => names.indexOf(n) !== i)) return "Названия категорий не должны повторяться";
  return null;
}

/** Список для сохранения: без служебных полей, с id у новых категорий. */
export function categoriesToSave(list: DraftCategory[], random?: () => number): Category[] {
  const taken = list.map((c) => c.id).filter(Boolean);
  return list.map((c) => {
    let id = c.id;
    if (!id) {
      id = newCategoryId(clean(c.name), taken, random);
      taken.push(id);
    }
    return { id, name: clean(c.name), color: c.color, description: clean(c.description) };
  });
}

/** Изменился ли список относительно сохранённого. */
export function categoriesChanged(draft: unknown, saved: unknown): boolean {
  const a = draftCategories(draft).map((c) => [c.id, clean(c.name), c.color, clean(c.description)]);
  const b = draftCategories(saved).map((c) => [c.id, clean(c.name), c.color, clean(c.description)]);
  return JSON.stringify(a) !== JSON.stringify(b);
}

/** «У 1 встречи», «У 5 встреч». */
function meetings(n: number): string {
  return n % 10 === 1 && n % 100 !== 11 ? `${n} встречи` : `${n} встреч`;
}

export function CategoriesTip() {
  return (
    <HelpTip label="Как работают категории" title="Категории встреч">
      <TipLine>
        Категорию встречи определяет ИИ при анализе встречи, если включено «Определять категорию автоматически»
        (раздел «Анализ встречи»). Описание помогает ИИ отличать категории.
      </TipLine>
      <TipLine>
        Категорию можно выбрать вручную — под названием в карточке встречи или в меню записи в списке. Выбранную
        вручную категорию ИИ не меняет.
      </TipLine>
    </HelpTip>
  );
}

function ColorPicker({ value, name, onChange }: { value: string; name: string; onChange: (color: string) => void }) {
  const button = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const close = () => { setOpen(false); button.current?.focus(); };
  const current = CATEGORY_PALETTE.find((p) => p.color.toLowerCase() === value.toLowerCase());
  const focused = current ?? CATEGORY_PALETTE[0]!;
  // Палитра — одна остановка Tab: стрелки ходят по цветам, Enter или пробел выбирает.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const all = [...e.currentTarget.querySelectorAll<HTMLButtonElement>("[role=radio]")];
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    let next = -1;
    if (step) next = (at + step + all.length) % all.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = all.length - 1;
    if (next < 0) return;
    e.preventDefault();
    all.forEach((b, i) => { b.tabIndex = i === next ? 0 : -1; });
    all[next]?.focus();
  };
  return (
    <>
      <button ref={button} type="button" className="catedit__swatch" style={{ background: value }}
        aria-haspopup="dialog" aria-expanded={open}
        aria-label={`Цвет категории «${name || "без названия"}»: ${current?.name ?? value}`}
        onClick={() => setOpen((v) => !v)} />
      {open && button.current && (
        <Popover anchor={button.current} label="Цвет категории" width={212} onClose={close} anchorToggles>
          <div role="radiogroup" aria-label="Цвет категории" className="catedit__palette" onKeyDown={onKeyDown}>
            {CATEGORY_PALETTE.map((p) => (
              <button key={p.color} type="button" role="radio" aria-checked={p === current} aria-label={p.name}
                title={p.name} className="catedit__color" style={{ background: p.color }}
                tabIndex={p === focused ? 0 : -1} autoFocus={p === focused}
                onClick={() => { onChange(p.color); close(); }} />
            ))}
          </div>
        </Popover>
      )}
    </>
  );
}

export function CategoriesSection({ value, onChange, endpoint }: {
  value: unknown;
  onChange: (list: DraftCategory[]) => void;
  endpoint: Endpoint;
}) {
  const list = draftCategories(value);
  const [info, setInfo] = useState<CategoriesInfo | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [askReset, setAskReset] = useState(false);
  const [focusKey, setFocusKey] = useState<string | null>(null);
  /** Ошибку в названиях показываем, когда из поля названия ушли, а не сразу после «Добавить». */
  const [touched, setTouched] = useState(false);
  const keyCounter = useRef(0);
  const rows = useRef<HTMLUListElement>(null);

  useEffect(() => {
    let live = true;
    getCategoriesInfo(endpoint).then((got) => { if (live) setInfo(got); }).catch(() => {});
    return () => { live = false; };
  }, [endpoint]);

  // Новая категория — фокус в её название.
  useEffect(() => {
    if (!focusKey) return;
    rows.current?.querySelector<HTMLInputElement>(`[data-key="${focusKey}"] input`)?.focus();
    setFocusKey(null);
  }, [focusKey]);

  const keyOf = (c: DraftCategory) => c.key ?? c.id;
  const update = (i: number, patch: Partial<DraftCategory>) =>
    onChange(list.map((c, n) => (n === i ? { ...c, ...patch } : c)));
  const move = (i: number, by: -1 | 1) => {
    const next = [...list];
    const [item] = next.splice(i, 1);
    next.splice(i + by, 0, item!);
    onChange(next);
    // Кнопка могла исчезнуть (край списка) — фокус на соседней у той же строки.
    requestAnimationFrame(() => {
      const row = rows.current?.querySelector(`[data-key="${keyOf(item!)}"]`);
      (row?.querySelector<HTMLButtonElement>(`[data-move="${by}"]:not(:disabled)`)
        ?? row?.querySelector<HTMLButtonElement>("[data-move]:not(:disabled)"))?.focus();
    });
  };
  const add = () => {
    const used = new Set(list.map((c) => c.color.toLowerCase()));
    const color = (CATEGORY_PALETTE.find((p) => !used.has(p.color.toLowerCase())) ?? CATEGORY_PALETTE[0]!).color;
    const key = `new-${++keyCounter.current}`;
    onChange([...list, { id: "", key, name: "", color, description: "" }]);
    setFocusKey(key);
  };
  const remove = (c: DraftCategory) => {
    setConfirm(null);
    onChange(list.filter((x) => x !== c));
  };
  const error = categoriesError(list);

  return (
    <>
      <p className="muted sdesc">
        Категории помогают находить встречи: они видны в списке записей, по ним работает фильтр.
        Порядок здесь — порядок в меню и фильтре.
      </p>
      {list.length ? (
        <ul ref={rows} className="catedit" aria-label="Категории встреч">
          {list.map((c, i) => {
            const k = keyOf(c);
            // Счётчики не пришли (ещё грузятся или резидент не ответил) — без числа.
            const used = !c.id ? 0 : info ? info.counts[c.id] ?? 0 : null;
            const shown = clean(c.name) || "без названия";
            return (
              <li key={k} data-key={k} className="catedit__item">
                <div className="catedit__row">
                  <span className="catedit__move">
                    <button type="button" className="icon-btn" data-move={-1} disabled={i === 0}
                      aria-label={`Поднять «${shown}»`} title="Выше" onClick={() => move(i, -1)}>
                      <ArrowUp {...ICON} />
                    </button>
                    <button type="button" className="icon-btn" data-move={1} disabled={i === list.length - 1}
                      aria-label={`Опустить «${shown}»`} title="Ниже" onClick={() => move(i, 1)}>
                      <ArrowDown {...ICON} />
                    </button>
                  </span>
                  <ColorPicker value={c.color} name={clean(c.name)} onChange={(color) => update(i, { color })} />
                  <input type="text" className="catedit__name" aria-label="Название категории" maxLength={NAME_MAX}
                    placeholder="Название" value={c.name} onChange={(e) => update(i, { name: e.target.value })}
                    onBlur={() => setTouched(true)} />
                  <input type="text" className="catedit__desc" aria-label={`Описание категории «${shown}» для ИИ`}
                    maxLength={DESCRIPTION_MAX} placeholder="Описание для ИИ: какие встречи сюда относятся"
                    value={c.description} onChange={(e) => update(i, { description: e.target.value })} />
                  <IconButton icon={Trash2} label={`Удалить «${shown}»`} tooltip="Удалить" variant="danger"
                    className="catedit__remove" aria-expanded={confirm === k}
                    onClick={() => setConfirm(confirm === k ? null : k)} />
                </div>
                {confirm === k && (
                  <ConfirmDialog inline className="catedit__confirm" title={`Удалить категорию «${shown}»?`}
                    message={used === null ? "Встречи с этой категорией будут показаны «Без категории»."
                      : used > 0 ? `У ${meetings(used)} эта категория будет снята.` : "Встреч с этой категорией нет."}
                    confirmLabel="Удалить" onConfirm={() => remove(c)} onCancel={() => setConfirm(null)} />
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted">Категорий нет: ИИ не будет определять категорию, а фильтр в списке записей скрыт.</p>
      )}
      {error && (touched || !list.some((x) => !x.id && !clean(x.name))) && <p className="error">{error}</p>}
      {askReset && (
        <ConfirmDialog title="Вернуть стандартные категории?" confirmLabel="Сбросить"
          message="Ваши категории и описания будут заменены стандартным списком. Изменение вступит в силу после «Сохранить»."
          onCancel={() => setAskReset(false)}
          onConfirm={() => { setAskReset(false); onChange(info?.defaults ?? []); }} />
      )}
      <div className="catedit__actions">
        <Button onClick={add}>Добавить категорию</Button>
        <Button disabled={!info?.defaults.length} onClick={() => { setConfirm(null); setAskReset(true); }}>
          Сбросить к стандартным…
        </Button>
        <span className="catedit__help">
          <span className="muted">Описание помогает ИИ отличать категории</span>
          <CategoriesTip />
        </span>
      </div>
    </>
  );
}
