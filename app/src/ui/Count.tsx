import type { ReactNode } from "react";
import "./count.css";

/**
 * Счётчик (0.5): одно правило на всё окно. `neutral` — просто число (сколько
 * выбрано фильтров, сколько встреч); `new` — новое или непрочитанное (цвет
 * акцента); `alert` — ошибки и «нужно решение» (красный). Цвет палитры не
 * делает обычное число похожим на ошибку: акцент — только у нового.
 */
export type CountTone = "neutral" | "new" | "alert";

export function Count({ value, tone = "neutral", label, className = "" }: {
  value: ReactNode;
  tone?: CountTone;
  /** Имя для диктора (иначе счётчик скрыт от него — число дублирует подпись рядом). */
  label?: string;
  className?: string;
}) {
  return (
    <span className={`count count--${tone} num ${className}`.trim()}
      {...(label ? { role: "status", "aria-label": label } : { "aria-hidden": true })}>
      {value}
    </span>
  );
}
