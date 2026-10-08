import { useEffect, useRef, type ReactNode } from "react";
import { floatingStyle, useFloating, type Align } from "./floating";
import "./popover.css";

const W = 260;

/**
 * Всплывающее окно у элемента-якоря; закрывается по Esc и клику снаружи.
 *
 * `anchorToggles` — якорь — кнопка, которая сама открывает и закрывает окно:
 * нажатие на неё окно «снаружи» не закрывает (иначе mousedown закрыл бы его, а
 * следующий click открыл снова). Без флага якорь — обычное «снаружи»: у
 * некоторых окон якорь — вся расшифровка, и щелчок по ней должен закрывать.
 */
export function Popover({ anchor, onClose, children, label, width = W, anchorToggles = false, align = "start" }: {
  anchor: HTMLElement;
  onClose: () => void;
  children: ReactNode;
  label: string;
  /** Ширина окна, px. */
  width?: number;
  anchorToggles?: boolean;
  /** "end" — правым краем к правому краю якоря (кнопка у правого края: окно раскрывается влево). */
  align?: Align;
}) {
  const box = useRef<HTMLDivElement>(null);
  // Положение — общее правило (ui/floating): в пределах окна, с переворотом и пересчётом.
  const pos = useFloating(anchor, box, { align, width });

  useEffect(() => {
    const down = (e: MouseEvent) => {
      const target = e.target as Node;
      if (anchorToggles && anchor.contains(target)) return;
      if (box.current && !box.current.contains(target)) onClose();
    };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [onClose, anchor, anchorToggles]);

  return (
    <div ref={box} className="popover glass glass--dense" role="dialog" aria-label={label}
      style={{ ...floatingStyle(pos), width }}>
      {children}
    </div>
  );
}
