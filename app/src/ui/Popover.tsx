import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

const W = 260;

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
    const r = anchor.getBoundingClientRect();
    const left = Math.max(8, Math.min(r.left, window.innerWidth - W - 8));
    setPos({ left, top: r.bottom + 6 });
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
