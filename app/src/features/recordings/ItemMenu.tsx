/**
 * Меню действий записи в списке: по «⋯» и по правой кнопке (или Shift+F10).
 *
 * Клавиатура как у обычного меню: фокус на первом пункте, ↑/↓/Home/End —
 * по пунктам, Esc — закрыть и вернуть фокус на «⋯» (это делает вызывающий в
 * `onClose`). Клик снаружи закрывает. Положение — fixed, в пределах окна.
 */

import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";

export type MenuItem = { label: string; onSelect: () => void; danger?: boolean };

const MARGIN = 8;

export function ItemMenu({ at, label, items, note, anchor, onClose }: {
  /** Точка, откуда раскрыть: под «⋯» или под указателем. */
  at: { x: number; y: number };
  label: string;
  items: MenuItem[];
  /** Пояснение над пунктами (подтверждение удаления). */
  note?: string;
  /** Кнопка, открывшая меню: нажатие на неё закрывает меню само (повторным кликом), а не как «снаружи». */
  anchor?: RefObject<HTMLElement | null>;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState(at);

  useLayoutEffect(() => {
    const r = box.current?.getBoundingClientRect();
    const w = r?.width ?? 0;
    const h = r?.height ?? 0;
    setPos({
      x: Math.max(MARGIN, Math.min(at.x, window.innerWidth - w - MARGIN)),
      y: Math.max(MARGIN, Math.min(at.y, window.innerHeight - h - MARGIN)),
    });
  }, [at, items.length, note]);

  // Пункты сменились (подтверждение удаления) — фокус снова на первом.
  useEffect(() => {
    box.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
  }, [note]);

  useEffect(() => {
    const down = (e: MouseEvent) => {
      const target = e.target as Node;
      if (box.current?.contains(target) || anchor?.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener("mousedown", down);
    return () => document.removeEventListener("mousedown", down);
  }, [onClose, anchor]);

  const onKeyDown = (e: KeyboardEvent) => {
    const all = [...(box.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? [])];
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    let next = -1;
    if (e.key === "ArrowDown") next = (at + 1) % all.length;
    else if (e.key === "ArrowUp") next = (at - 1 + all.length) % all.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = all.length - 1;
    else if (e.key === "Escape" || e.key === "Tab") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
      return;
    }
    if (next >= 0) {
      e.preventDefault();
      all[next]?.focus();
    }
  };

  return (
    <div ref={box} className="item-menu" role="menu" aria-label={label} onKeyDown={onKeyDown}
      style={{ left: pos.x, top: pos.y }}>
      {note && <div className="item-menu__note">{note}</div>}
      {items.map((item) => (
        <button key={item.label} type="button" role="menuitem" tabIndex={-1}
          className={`item-menu__item${item.danger ? " item-menu__item--danger" : ""}`}
          onClick={item.onSelect}>{item.label}</button>
      ))}
    </div>
  );
}
