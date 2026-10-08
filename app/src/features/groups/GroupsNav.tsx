/**
 * Дерево групп (в поповере кнопки-списка над списком записей, GroupsPicker):
 * «Все записи», группы по порядку (цвет и число встреч), неизвестные («Группа
 * без названия»), «Без группы» и «+ Новая группа». Щелчок — область списка (и поиска).
 *
 * Клавиатура: ↑/↓/Home/End — по строкам, Enter/Пробел — выбрать область,
 * Alt+↑/↓ — переставить группу, Shift+F10 или клавиша меню — меню группы.
 * Мышью: «⋯» или правая кнопка — меню; группу можно перетащить на другое
 * место (линия вставки), а встречи из списка — на группу или «Без группы».
 *
 * Список — `<ul>`: строки — кнопки с `aria-current` у выбранной области,
 * «⋯» — отдельная кнопка.
 */

import { CircleDashed, Copy, Ellipsis, Layers, Plus, TriangleAlert, X } from "lucide-react";
import {
  useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent, type MouseEvent,
  type PointerEvent,
} from "react";
import { ALL_NAME, meetingsText, NO_GROUP, NO_GROUP_NAME, shiftId, UNKNOWN_NAME, type GroupScope } from "../../lib/groups";
import { CategoryDot } from "../../ui/Category";
import { Icon } from "../../ui/Icon";
import type { DragView } from "./drag";
import { GroupMenu } from "./GroupMenu";
import type { GroupsUi } from "./useGroupsUi";
import "./groups.css";

/** Состояние перетаскивания (вне React: панель перерисовывается только при смене цели). */
export function useDragView(ui: GroupsUi): DragView | null {
  return useSyncExternalStore(ui.drag.subscribe, ui.drag.view, ui.drag.view);
}

type Row = {
  key: string;
  scope: GroupScope;
  label: string;
  count: number;
  /** Цвет точки; null — пустой кружок («Без группы», неизвестная). */
  color?: string | null;
  /** Строка группы из списка: переставляется, у неё меню. */
  group?: string;
  /** Неизвестная группа: своё меню. */
  unknown?: string;
  /** Куда можно отпустить встречи: id группы или NO_GROUP. */
  drop?: string;
};

/** Имя файла из пути (копия повреждённого файла групп): полный путь — в подсказке. */
const baseName = (path: string) => path.split(/[\\/]/).pop() || path;

/**
 * Имя копии в две части: начало («.meet-groups.json.broken-») сжимается многоточием, время в
 * конце видно всегда — только им копии и отличаются.
 */
export function splitCopyName(name: string): [string, string] {
  const at = name.lastIndexOf("broken-");
  const cut = at >= 0 ? at + "broken-".length : Math.max(0, name.length - 16);
  return [name.slice(0, cut), name.slice(cut)];
}

export function GroupsNav({ ui, active, onOpen }: {
  ui: GroupsUi;
  /** Открыт раздел «Записи»: выбранная область отмечена. */
  active: boolean;
  /**
   * Выбрали область — показать список записей. `apply` — сменить область: её зовут, только когда
   * уход туда состоялся (из настроек с несохранённым могут остаться — тогда область прежняя).
   */
  onOpen: (apply: () => void) => void;
}) {
  const view = useDragView(ui);
  const list = useRef<HTMLUListElement>(null);
  const [menu, setMenu] = useState<{ id: string; at: { x: number; y: number } | null } | null>(null);
  const menuAnchor = useRef<HTMLButtonElement | null>(null);
  /** После перестановки с клавиатуры — фокус обратно на ту же группу. */
  const refocus = useRef<string | null>(null);
  const editable = !ui.readOnly;

  const rows: Row[] = [
    { key: "all", scope: null, label: ALL_NAME, count: ui.total },
    ...ui.groups.map((g) => ({ key: g.id, scope: g.id, label: g.name, count: g.count, color: g.color, group: g.id,
      drop: g.id })),
    ...ui.unknown.map((g) => ({ key: g.id, scope: g.id, label: UNKNOWN_NAME, count: g.count, color: null, unknown: g.id })),
    { key: NO_GROUP, scope: NO_GROUP, label: NO_GROUP_NAME, count: ui.none, color: null, drop: NO_GROUP },
  ];
  const order = ui.groups.map((g) => g.id);

  // Переставили с клавиатуры — фокус обратно на ту же группу (React переносит строку, и фокус
  // с неё слетает). Только если фокус не ушёл в другое место: в поиск, в карточку.
  useLayoutEffect(() => {
    const id = refocus.current;
    if (!id) return;
    refocus.current = null;
    const at = document.activeElement;
    if (at && at !== document.body && at.isConnected && !list.current?.contains(at)) return;
    list.current?.querySelector<HTMLButtonElement>(`[data-scope-key="${id}"]`)?.focus();
  }, [ui.groups]);

  // Меню группы, которой больше нет (удалили в другом окне), — закрыть.
  useEffect(() => {
    if (menu && !ui.groups.some((g) => g.id === menu.id) && !ui.unknown.some((g) => g.id === menu.id)) setMenu(null);
  }, [menu, ui.groups, ui.unknown]);

  const choose = (scope: GroupScope) => onOpen(() => ui.setScope(scope));
  const openMenu = (id: string, button: HTMLButtonElement | null, at: { x: number; y: number } | null) => {
    menuAnchor.current = button;
    setMenu({ id, at });
  };
  const closeMenu = (focusBack = true) => {
    setMenu(null);
    if (focusBack) menuAnchor.current?.focus();
  };

  const onListKey = (e: KeyboardEvent<HTMLUListElement>) => {
    const target = e.target as HTMLElement;
    if (!target.matches("[data-scope-key]")) return;
    const buttons = [...(list.current?.querySelectorAll<HTMLButtonElement>("[data-scope-key]") ?? [])];
    const at = buttons.indexOf(target as HTMLButtonElement);
    const group = target.dataset.group;
    if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault();
      if (!group || !editable) return;
      const delta = e.key === "ArrowUp" ? -1 : 1;
      // У края порядок тот же — ни запроса, ни ожидания фокуса.
      const ids = ui.groups.map((g) => g.id);
      if (shiftId(ids, group, delta) === ids) return;
      refocus.current = group;
      void ui.shift(group, delta).then((ok) => { if (!ok && refocus.current === group) refocus.current = null; });
      return;
    }
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    let next = -1;
    if (e.key === "ArrowDown") next = Math.min(buttons.length - 1, at + 1);
    else if (e.key === "ArrowUp") next = Math.max(0, at - 1);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = buttons.length - 1;
    if (next < 0) return;
    e.preventDefault();
    buttons[next]?.focus();
  };

  const dropTarget = view?.payload.kind === "meetings" && view.target?.kind === "group"
    ? view.target.group ?? NO_GROUP : null;
  const slot = view?.payload.kind === "group" && view.target?.kind === "slot" ? view.target.index : null;
  const draggingGroup = view?.payload.kind === "group" ? view.payload.id : null;

  return (
    <div className="nav-groups" data-drag-scroll="">
      <ul ref={list} className="nav-groups__list" aria-label="Группы встреч" onKeyDown={onListKey}>
        {rows.map((row) => {
          const current = active && ui.scope === row.scope;
          // Одна остановка Tab на весь список (выбранная область), дальше — стрелки; «⋯» — Shift+F10.
          const tabStop = ui.scope === row.scope || (row.scope === null && !rows.some((r) => r.scope === ui.scope));
          // Только для чтения: неизвестную группу всё равно можно убрать из встреч (это их meta.json).
          const menuId = editable ? row.group ?? row.unknown : row.unknown;
          const at = row.group ? order.indexOf(row.group) : -1;
          const cls = ["nav-group",
            current ? "nav-group--current" : "",
            row.drop && dropTarget === row.drop ? "nav-group--drop" : "",
            view?.payload.kind === "meetings" && row.drop ? "nav-group--droppable" : "",
            row.group && slot === at ? "nav-group--insert-before" : "",
            row.group && slot === order.length && at === order.length - 1 ? "nav-group--insert-after" : "",
            row.group && draggingGroup === row.group ? "nav-group--dragging" : "",
            menu && menu.id === menuId ? "nav-group--menu" : "",
          ].filter(Boolean).join(" ");
          const title = `${row.label} · ${meetingsText(row.count)}`;
          const onContext = menuId ? (e: MouseEvent<HTMLElement>) => {
            e.preventDefault();
            // «⋯» не виден (нет места) — меню у самой строки (и фокус вернётся на неё).
            const more = (e.currentTarget as HTMLElement).querySelector<HTMLButtonElement>(".nav-group__more");
            const button = more && more.getClientRects().length
              ? more : (e.currentTarget as HTMLElement).querySelector<HTMLButtonElement>(".nav-group__main");
            // Shift+F10 / клавиша меню приходят без координат указателя.
            openMenu(menuId, button, e.clientX === 0 && e.clientY === 0 ? null : { x: e.clientX, y: e.clientY });
          } : undefined;
          const onPointerDown = row.group && editable ? (e: PointerEvent<HTMLButtonElement>) => {
            const id = row.group!;
            ui.drag.begin(e.nativeEvent, () => ({ kind: "group", id, label: row.label }));
          } : undefined;
          return (
            <li key={row.key} className={cls} data-row-key={row.key} data-group-row={row.group} data-drop-group={row.drop}
              onContextMenu={onContext}>
              <button type="button" className="nav-group__main" data-scope-key={row.scope ?? "all"}
                data-group={row.group} aria-current={current ? "true" : undefined} title={title}
                tabIndex={tabStop ? 0 : -1}
                aria-keyshortcuts={[row.group && editable ? "Alt+ArrowUp Alt+ArrowDown" : "", menuId ? "Shift+F10" : ""]
                  .filter(Boolean).join(" ") || undefined}
                onClick={() => choose(row.scope)} onPointerDown={onPointerDown}>
                {row.scope === null ? <Icon as={Layers} size="sm" className="nav-group__icon" />
                  : row.scope === NO_GROUP ? <Icon as={CircleDashed} size="sm" className="nav-group__icon" />
                    : <span className="nav-group__icon"><CategoryDot color={row.color} /></span>}
                <span className="nav-group__name">{row.label}</span>
                <span className="nav-group__count num" aria-hidden="true">{row.count}</span>
                <span className="sr-only">, {meetingsText(row.count)}</span>
              </button>
              {menuId && (
                <button type="button" className="nav-group__more" aria-label={`Действия с группой «${row.label}»`}
                  aria-haspopup="menu" aria-expanded={menu?.id === menuId} tabIndex={-1}
                  onClick={(e) => (menu?.id === menuId ? closeMenu() : openMenu(menuId, e.currentTarget, null))}>
                  <Icon as={Ellipsis} size="sm" />
                </button>
              )}
              {menu && menu.id === menuId && (
                <GroupMenu ui={ui} id={menuId} at={menu.at} anchor={menuAnchor} align="start" onClose={closeMenu} />
              )}
            </li>
          );
        })}
      </ul>
      {editable && (
        <button type="button" className="nav-group__add" onClick={() => ui.create()}>
          <Icon as={Plus} size="sm" className="nav-group__icon" />
          <span className="nav-group__name">Новая группа</span>
        </button>
      )}
      {ui.readOnly && (
        <p className="nav-groups__note" role="note" title="Группы записаны более новой версией Meet">
          <Icon as={TriangleAlert} size="sm" />
          <span className="nav-groups__note-text">Группы записаны более новой версией Meet — здесь их можно только смотреть.</span>
        </p>
      )}
      {ui.broken && (
        <div className="nav-groups__note nav-groups__note--warn" role="alert"
          title={ui.broken.copy ? `Файл групп повреждён, копия: ${ui.broken.copy}` : "Файл групп повреждён"}>
          <Icon as={TriangleAlert} size="sm" />
          <span className="nav-groups__note-text">
            {/* Полный путь длинный: виден файл, весь путь — в подсказке, для диктора и кнопкой «Скопировать». */}
            {ui.broken.copy ? <>Файл групп повреждён, копия: <span className="nav-groups__path" aria-hidden="true">
              {splitCopyName(baseName(ui.broken.copy)).map((part, i) => (
                <span key={i} className={i ? "nav-groups__path-tail" : "nav-groups__path-head"}>{part}</span>
              ))}</span><span className="sr-only">{ui.broken.copy}</span></>
              : "Файл групп повреждён. При первом изменении групп он будет сохранён рядом как копия."}
          </span>
          {ui.broken.copy && typeof navigator !== "undefined" && navigator.clipboard && (
            <button type="button" className="nav-groups__close" aria-label="Скопировать путь к копии"
              title="Скопировать путь к копии" onClick={() => void navigator.clipboard.writeText(ui.broken!.copy!)}>
              <Icon as={Copy} size="sm" />
            </button>
          )}
          <button type="button" className="nav-groups__close" aria-label="Скрыть предупреждение"
            onClick={ui.dismissBroken}><Icon as={X} size="sm" /></button>
        </div>
      )}
    </div>
  );
}
