/**
 * Кнопка-список групп над списком записей (0.4, макет LIBRARY): имя области
 * списка — «Все записи», «Без группы» или группа с точкой цвета — и число
 * встреч. По нажатию — поповер на стекле с деревом групп (GroupsNav): выбор
 * области, «Новая группа», меню группы, перестановка групп и перетаскивание
 * встреч на группу.
 *
 * Клавиатура: Enter, Пробел или ↓ — открыть (фокус — на выбранной области в
 * дереве), Esc — закрыть и вернуть фокус на кнопку. Выбрали область — поповер
 * закрывается, фокус на кнопке. Ctrl+K (к поиску списка) закрывает дерево.
 *
 * Пока дерево открыто, нажатие на строку записи его не закрывает: с неё
 * начинают перетаскивание на группу. Обычный щелчок по строке (не после
 * перетаскивания — тот щелчок гасит drag.ts) — закрывает. Окно названия группы,
 * подтверждение и уведомление «Отменить», открытые из дерева, его тоже не
 * закрывают: после них фокус возвращается в дерево.
 */

import { ChevronDown, CircleDashed, Layers, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { ALL_NAME, meetingsText, NO_GROUP } from "../../lib/groups";
import { CategoryDot } from "../../ui/Category";
import { Icon } from "../../ui/Icon";
import { Popover } from "../../ui/Popover";
import { Tip } from "../../ui/Tip";
import { GroupsNav } from "./GroupsNav";
import type { GroupsUi } from "./useGroupsUi";
import "./groups.css";

/** Ширина поповера не меньше этой, px (уже — по ширине кнопки). */
const MIN_W = 260;

/** Нажатия снаружи, которые дерево не закрывают: строка записи (перетаскивание), окна и уведомление из дерева. */
const KEEP_OPEN = ".rec-item, [aria-modal='true'], [role='alertdialog'], .backdrop--modal, .toast";
const keepOpen = (target: Element) => target.closest(KEEP_OPEN) !== null;

export function GroupsPicker({ ui }: { ui: GroupsUi }) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const tree = useRef<HTMLDivElement>(null);

  const close = useCallback((focusBack = true) => {
    setOpen(false);
    if (focusBack) button.current?.focus();
  }, []);
  const onPopoverClose = useCallback(() => close(), [close]);

  // Открыли — фокус на выбранной области (единственная остановка Tab в дереве).
  useLayoutEffect(() => {
    if (open) tree.current?.querySelector<HTMLElement>('[data-scope-key][tabindex="0"]')?.focus();
  }, [open]);

  // Фокус ушёл в поиск списка (Ctrl+K, щелчок) — дерево закрывается, фокус остаётся в поиске.
  useEffect(() => {
    if (!open) return;
    const focus = (e: FocusEvent) => {
      if (e.target instanceof Element && e.target.matches(".rec-list input[type=search]")) close(false);
    };
    document.addEventListener("focusin", focus);
    return () => document.removeEventListener("focusin", focus);
  }, [open, close]);

  // Обычный щелчок по записи — открыть её и закрыть дерево (фокус остаётся у записи).
  useEffect(() => {
    if (!open) return;
    const click = (e: MouseEvent) => {
      if (e.target instanceof Element && e.target.closest(".rec-item")) close(false);
    };
    document.addEventListener("click", click);
    return () => document.removeEventListener("click", click);
  }, [open, close]);

  const scope = ui.scope;
  const label = ui.scopeName ?? ALL_NAME;
  const named = scope !== null && ui.scopeName !== null;
  const count = named ? ui.scopeCount : ui.total;
  const color = named && scope !== NO_GROUP ? ui.groups.find((g) => g.id === scope)?.color ?? null : null;

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== "ArrowDown" || e.altKey || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    setOpen(true);
  };

  return (
    <>
      <Tip content={open ? null : count === null ? label : `${label} · ${meetingsText(count)}`} describe={false}>
      <button ref={button} type="button" className="select-btn select-btn--md group-pick" data-groups-picker=""
        aria-label={`Группа встреч: ${label}`} aria-haspopup="dialog" aria-expanded={open}
        onClick={() => setOpen((v) => !v)} onKeyDown={onKeyDown}>
        {!named ? <Icon as={Layers} size="sm" className="group-pick__icon" />
          : scope === NO_GROUP ? <Icon as={CircleDashed} size="sm" className="group-pick__icon" />
            : <span className="group-pick__icon"><CategoryDot color={color} /></span>}
        <span className="group-pick__name">{label}</span>
        {count !== null && <span className="group-pick__count num" aria-hidden="true">{count}</span>}
        {/* Файл групп повреждён: предупреждение — в дереве; здесь — знак, чтобы его открыли. */}
        {ui.broken && (
          <span className="group-pick__warn" aria-label="Файл групп повреждён — подробности в списке групп" role="img">
            <Icon as={TriangleAlert} size="sm" />
          </span>
        )}
        <Icon as={ChevronDown} size="sm" className="ic" />
      </button>
      </Tip>
      {open && button.current && (
        <Popover anchor={button.current} label="Группы" anchorToggles keepOpen={keepOpen}
          width={Math.max(MIN_W, button.current.offsetWidth)} onClose={onPopoverClose}>
          <div ref={tree} data-groups-tree="">
            <GroupsNav ui={ui} active onOpen={(apply) => { apply(); close(); }} />
          </div>
        </Popover>
      )}
    </>
  );
}
