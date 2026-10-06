/**
 * Меню действий записи в списке: по «⋯» и по правой кнопке (или Shift+F10).
 *
 * Клавиатура как у обычного меню: фокус на первом пункте, ↑/↓/Home/End —
 * по пунктам, Esc — закрыть и вернуть фокус на «⋯» (это делает вызывающий в
 * `onClose`). Клик снаружи закрывает. Положение — fixed, по общему правилу
 * (ui/floating): под кнопкой (или под указателем), у края окна — с другой
 * стороны, всегда целиком в окне; пересчитывается при прокрутке и смене размера.
 */

import { Check, ChevronRight } from "lucide-react";
import {
  Fragment, useEffect, useId, useMemo, useRef, type KeyboardEvent, type ReactNode, type RefObject,
} from "react";
import { floatingStyle, pointAnchor, useFloating, type Align } from "../../ui/floating";
import { Icon } from "../../ui/Icon";

export type MenuItem = {
  label: string;
  onSelect: () => void;
  danger?: boolean;
  /** Значок слева от подписи (16 px, lucide-react). */
  icon?: ReactNode;
  /** Черта перед пунктом: отделяет опасное от обычного. */
  separator?: boolean;
  /** Подсказка при наведении: что именно сделает пункт. */
  hint?: string;
  /** Пункт виден, но сейчас недоступен (идёт другое действие); стрелки его пропускают. */
  disabled?: boolean;
  /** Фокус при открытии — на этом пункте, а не на первом (в подтверждении — «Отмена»). */
  autoFocus?: boolean;
  /** Пункт выбора одного из нескольких (категория): отмечен ли он. Нет — обычный пункт. */
  checked?: boolean;
  /** Значок справа (стрелка у пункта, открывающего список). */
  trailing?: ReactNode;
  /**
   * Пункт-«разделённая кнопка»: основное нажатие — `onSelect`, стрелка справа —
   * `split.onSelect` (например, выбрать другую модель для этого действия).
   */
  split?: { label: string; onSelect: () => void; hint?: string; disabled?: boolean };
  /**
   * Вариант виден и доступен с клавиатуры и экранному диктору (`aria-disabled`,
   * причина — в `detail`), но не выбирается: недоступная модель.
   */
  unavailable?: boolean;
  /** Вторая строка мелким шрифтом под подписью (куда уходит текст, почему недоступно). */
  detail?: string;
};

export function ItemMenu({ at, align = "start", label, items, note, anchor, onClose }: {
  /** Точка под указателем (контекстное меню). Нет — меню раскрывается под кнопкой `anchor`. */
  at?: { x: number; y: number } | null;
  /** У кнопки: "start" — левым краем к её левому краю, "end" — правым к правому (раскрытие влево). */
  align?: Align;
  label: string;
  items: MenuItem[];
  /** Пояснение над пунктами (подтверждение удаления). */
  note?: string;
  /** Кнопка, открывшая меню: нажатие на неё закрывает меню само (повторным кликом), а не как «снаружи». */
  anchor?: RefObject<HTMLElement | null>;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const noteId = useId();
  const x = at?.x;
  const y = at?.y;
  const point = useMemo(() => (x === undefined || y === undefined ? null : pointAnchor(x, y)), [x, y]);
  // Под указателем — без зазора; у кнопки — обычный зазор.
  const pos = useFloating(point ?? anchor ?? null, box, { align, gap: point ? 0 : 4 });

  // Пункты сменились (подтверждение удаления) — фокус снова на первом (или на
  // отмеченном `autoFocus`: в подтверждении это «Отмена», два Enter не удаляют).
  useEffect(() => {
    const el = box.current;
    (el?.querySelector<HTMLButtonElement>("[data-autofocus]:not(:disabled)")
      ?? el?.querySelector<HTMLButtonElement>("[role^=menuitem]:not(:disabled)"))?.focus();
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
    const all = [...(box.current?.querySelectorAll<HTMLButtonElement>("[role^=menuitem]:not(:disabled)") ?? [])];
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
      aria-describedby={note ? noteId : undefined} style={floatingStyle(pos)}>
      {note && <div className="item-menu__note" id={noteId}>{note}</div>}
      {items.map((item, i) => {
        const main = (
          <button type="button" role={item.checked === undefined ? "menuitem" : "menuitemradio"}
            aria-checked={item.checked} tabIndex={-1} title={item.hint} disabled={item.disabled}
            aria-disabled={item.unavailable || undefined}
            data-autofocus={item.autoFocus || undefined}
            className={`item-menu__item${item.danger ? " item-menu__item--danger" : ""}${
              item.unavailable ? " item-menu__item--unavailable" : ""}`}
            onClick={item.unavailable ? undefined : item.onSelect}>
            {item.icon && <span className="item-menu__icon" aria-hidden="true">{item.icon}</span>}
            {item.detail ? (
              <span className="item-menu__text">{item.label}<span className="item-menu__detail">{item.detail}</span></span>
            ) : item.label}
            {item.checked && <span className="item-menu__end" aria-hidden="true"><Icon as={Check} size="sm" /></span>}
            {item.trailing && <span className="item-menu__end item-menu__icon" aria-hidden="true">{item.trailing}</span>}
          </button>
        );
        return (
          <Fragment key={`${i}:${item.label}`}>
            {item.separator && <div className="item-menu__sep" role="separator" />}
            {item.split ? (
              <div className="item-menu__split">
                {main}
                <button type="button" role="menuitem" tabIndex={-1} aria-label={item.split.label}
                  title={item.split.hint ?? item.split.label} disabled={item.split.disabled ?? item.disabled}
                  className="item-menu__item item-menu__more" onClick={item.split.onSelect}>
                  <Icon as={ChevronRight} size="sm" />
                </button>
              </div>
            ) : main}
          </Fragment>
        );
      })}
    </div>
  );
}
