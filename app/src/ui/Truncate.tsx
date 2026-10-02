import { createElement, useRef, type ReactNode } from "react";
import "./primitives.css";

/**
 * Текст в одну строку с многоточием. Подсказка с полным текстом появляется
 * только когда текст действительно обрезан: проверка — при наведении и фокусе
 * (дешевле, чем следить за размерами всё время).
 */
export function Truncate({ text, children, as = "span", className = "", hint }: {
  /** Полный текст для подсказки; по умолчанию — сам `children`, если это строка. */
  text?: string;
  children?: ReactNode;
  as?: "span" | "div" | "code";
  className?: string;
  /** Подсказка, когда текст виден целиком (например, «Двойной щелчок — переименовать»). */
  hint?: string;
}) {
  const el = useRef<HTMLElement>(null);
  const full = text ?? (typeof children === "string" ? children : "");
  const check = () => {
    const node = el.current;
    if (!node) return;
    const clipped = node.scrollWidth > node.clientWidth + 1;
    const tip = clipped ? (hint ? `${full}\n${hint}` : full) : hint ?? "";
    if (tip) node.title = tip;
    else node.removeAttribute("title");
  };
  return createElement(as, {
    ref: el, className: `truncate ${className}`.trim(), onMouseEnter: check, onFocus: check,
  }, children ?? text);
}
