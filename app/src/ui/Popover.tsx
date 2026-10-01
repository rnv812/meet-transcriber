import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

const W = 260;
const GAP = 6;
const MARGIN = 8;

type Rect = { left: number; top: number; bottom: number };
type Size = { width: number; height: number };

/**
 * Где показать окно у якоря: под ним, а если снизу не влезает — над ним.
 * Не влезает ни там, ни там — прижимаем к краю окна, чтобы верх был виден.
 */
export function placePopover(anchor: Rect, box: Size, view: Size): { left: number; top: number } {
  const left = Math.max(MARGIN, Math.min(anchor.left, view.width - box.width - MARGIN));
  const below = anchor.bottom + GAP;
  const above = anchor.top - GAP - box.height;
  let top: number;
  if (below + box.height <= view.height - MARGIN) top = below;
  else if (above >= MARGIN) top = above;
  else top = Math.max(MARGIN, Math.min(below, view.height - box.height - MARGIN));
  return { left, top };
}

/** Всплывающее окно у элемента-якоря; закрывается по Esc и клику снаружи. */
export function Popover({ anchor, onClose, children, label }: {
  anchor: HTMLElement;
  onClose: () => void;
  children: ReactNode;
  label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: 0, top: 0 });

  useLayoutEffect(() => {
    // Высота от позиции не зависит: меряем уже отрисованное окно до того, как его увидят.
    const height = box.current?.getBoundingClientRect().height ?? 0;
    setPos(placePopover(anchor.getBoundingClientRect(), { width: W, height },
      { width: window.innerWidth, height: window.innerHeight }));
  }, [anchor]);

  useEffect(() => {
    const down = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) onClose();
    };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [onClose]);

  return (
    <div ref={box} className="popover" role="dialog" aria-label={label}
      style={{ left: pos.left, top: pos.top, width: W }}>
      {children}
    </div>
  );
}
