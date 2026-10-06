/**
 * Список записей по разделам дат (lib/dateSections): у каждого раздела —
 * заголовок-кнопка со счётчиком (sticky внутри `.pane-list`) и свой список.
 * Свёрнутый раздел строк не рисует.
 *
 * Клавиатура заголовка: Enter/Пробел — свернуть или развернуть, ← — свернуть,
 * → — развернуть, Alt+↑/↓ — к соседнему заголовку (из строки Alt+↑ — к
 * заголовку её раздела). Правая кнопка (Shift+F10) — меню «Свернуть все» /
 * «Развернуть все». В режиме выбора у заголовка — флажок раздела в трёх
 * состояниях: он берёт и строки свёрнутого раздела.
 */

import { ChevronRight, ChevronsDownUp, ChevronsUpDown } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { DateSection, SectionGroup } from "../../lib/dateSections";
import { Icon } from "../../ui/Icon";
import { ItemMenu } from "./ItemMenu";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;

export function DateSections<T extends { id: string }>({
  groups, isOpen, onToggle, onAll, picking = false, picked, onPickSection, renderItem, onKeyDown, multiselectable,
}: {
  groups: SectionGroup<T>[];
  isOpen: (section: DateSection) => boolean;
  onToggle: (section: DateSection, open: boolean) => void;
  /** «Свернуть все» / «Развернуть все». */
  onAll: (open: boolean) => void;
  /** Режим выбора нескольких записей: у заголовков — флажки разделов. */
  picking?: boolean;
  picked?: ReadonlySet<string>;
  /** Флажок раздела: отметить (true) или снять (false) все его записи. */
  onPickSection?: (items: T[], on: boolean) => void;
  renderItem: (item: T, section: DateSection) => ReactNode;
  /** Клавиши списка (Ctrl+A, Esc) — со всех разделов сразу. */
  onKeyDown?: (e: KeyboardEvent<HTMLDivElement>) => void;
  multiselectable?: boolean;
}) {
  const base = useId();
  const heads = useRef(new Map<string, HTMLButtonElement>());
  const [menu, setMenu] = useState<{ at: { x: number; y: number } | null } | null>(null);
  /** Заголовок, у которого открыто меню: к нему меню прижимается и на него возвращается фокус. */
  const menuAnchor = useRef<HTMLButtonElement | null>(null);
  const keys = groups.map((g) => g.section.key);

  const focusHead = (i: number) => heads.current.get(keys[Math.max(0, Math.min(keys.length - 1, i))]!)?.focus();
  const closeMenu = () => {
    setMenu(null);
    menuAnchor.current?.focus();
  };

  return (
    <div className="date-secs" onKeyDown={onKeyDown}>
      {groups.map(({ section, items }, i) => {
        const open = isOpen(section);
        const headId = `${base}-h${i}`;
        const listId = `${base}-l${i}`;
        const n = picked ? items.filter((r) => picked.has(r.id)).length : 0;
        const onHeadKey = (e: KeyboardEvent<HTMLButtonElement>) => {
          if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            // Стрелки — только разделу: плеер открытой карточки перематывал бы по ним.
            e.preventDefault();
            const want = e.key === "ArrowRight";
            if (want !== open) onToggle(section, want);
          }
        };
        const onSectionKey = (e: KeyboardEvent<HTMLElement>) => {
          if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
          e.preventDefault();
          e.stopPropagation();
          const fromHead = e.target === heads.current.get(section.key);
          focusHead(e.key === "ArrowDown" ? i + 1 : fromHead ? i - 1 : i);
        };
        return (
          <section key={section.key} className={`date-sec${open ? " date-sec--open" : ""}`} aria-labelledby={headId}
            onKeyDown={onSectionKey}>
            <div className="date-sec__head">
              {picking && onPickSection && (
                <input type="checkbox" className="date-sec__pick" aria-label={`Выбрать все в разделе «${section.label}»`}
                  checked={n > 0 && n === items.length}
                  ref={(el) => { if (el) el.indeterminate = n > 0 && n < items.length; }}
                  onChange={() => onPickSection(items, n < items.length)} />
              )}
              <h3 className="date-sec__title">
                <button type="button" id={headId} className="date-sec__toggle" aria-expanded={open} aria-controls={listId}
                  ref={(el) => { if (el) heads.current.set(section.key, el); else heads.current.delete(section.key); }}
                  onClick={() => onToggle(section, !open)} onKeyDown={onHeadKey}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    menuAnchor.current = e.currentTarget;
                    // Shift+F10 и клавиша меню приходят без координат указателя — меню под заголовком.
                    setMenu({ at: e.clientX === 0 && e.clientY === 0 ? null : { x: e.clientX, y: e.clientY } });
                  }}>
                  <Icon as={ChevronRight} size="sm" className="date-sec__chevron" />
                  {section.label}{" "}<span className="date-sec__count num">· {items.length}</span>
                </button>
              </h3>
            </div>
            {/* Свёрнутый — пустой скрытый список: строки не рисуются, aria-controls указывает на существующее. */}
            <ul id={listId} className="rec-list__items" aria-label={section.label} hidden={!open}
              aria-multiselectable={(open && multiselectable) || undefined}>
              {open && items.map((item) => renderItem(item, section))}
            </ul>
          </section>
        );
      })}
      {menu && (
        <ItemMenu at={menu.at} label="Разделы" onClose={closeMenu}
          anchor={menuAnchor}
          items={[
            { label: "Свернуть все", icon: <ChevronsDownUp {...ICON} />, onSelect: () => { onAll(false); closeMenu(); } },
            { label: "Развернуть все", icon: <ChevronsUpDown {...ICON} />, onSelect: () => { onAll(true); closeMenu(); } },
          ]} />
      )}
    </div>
  );
}
