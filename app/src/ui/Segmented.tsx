/**
 * Короткое перечисление (2–3 варианта в слово-два) — сегменты Atlas Aurora
 * `.tabs` с ролями радио: `radiogroup` и `radio`, выбранный — `aria-checked`
 * (у вкладок Aurora — `aria-selected`, поэтому вид выбранного — в
 * segmented.css). В порядке обхода — только выбранный; стрелки ←/↑ и →/↓
 * двигают выбор, Home/End — к крайним; фокус идёт за выбором.
 *
 * Размеры: `sm` — `.tabs--sm` (строки настроек, шапка сессии ассистента),
 * `md` — обычные `.tabs`. `disabled` — все варианты недоступны, клавиши не
 * работают.
 *
 * Имя группы — `label` (`aria-label`) или `labelledBy` (id видимой подписи).
 * У варианта: `description` — пояснение диктору (`aria-description`), `tip` —
 * всплывающая подсказка Aurora (`Tip`; она же описание диктору).
 *
 * Используется в настройках (`features/settings/Section` → `Segmented` в
 * строке) и в шапке сессии ассистента (`live/SessionBar`: частота, профиль).
 */

import type { KeyboardEvent, ReactNode } from "react";

import { Tip } from "./Tip";
import "./segmented.css";

export type SegmentedOption<T extends string> = {
  value: T;
  label: ReactNode;
  /** Пояснение диктору, без всплывающей подсказки. */
  description?: string;
  /** Всплывающая подсказка (Tip). */
  tip?: ReactNode;
};

export type SegmentedSize = "sm" | "md";

/**
 * Стрелки в группе радио-кнопок (сегменты, образцы палитры): ←/↑ и →/↓ —
 * соседний вариант, Home/End — крайние; выбор сразу и фокус на нём. Кнопки
 * группы (`[role=radio]`) — по порядку `values`.
 */
export function radioKeys<T extends string>(values: readonly T[], value: T, onChange: (v: T) => void) {
  return (e: KeyboardEvent<HTMLElement>) => {
    const at = values.indexOf(value);
    const last = values.length - 1;
    const next = e.key === "ArrowRight" || e.key === "ArrowDown" ? Math.min(last, at + 1)
      : e.key === "ArrowLeft" || e.key === "ArrowUp" ? Math.max(0, at - 1)
        : e.key === "Home" ? 0 : e.key === "End" ? last : -1;
    if (next < 0) return;
    e.preventDefault();
    if (next === at) return;
    onChange(values[next]!);
    e.currentTarget.querySelectorAll<HTMLButtonElement>("[role=radio]")[next]?.focus();
  };
}

export function Segmented<T extends string>({
  value, options, onChange, size = "sm", disabled = false, label, labelledBy, className,
}: {
  value: T;
  options: readonly SegmentedOption<T>[];
  onChange: (v: T) => void;
  size?: SegmentedSize;
  disabled?: boolean;
  /** Имя группы (`aria-label`), если видимой подписи нет. */
  label?: string;
  /** id видимой подписи группы (`aria-labelledby`). */
  labelledBy?: string;
  className?: string;
}) {
  const values = options.map((o) => o.value);
  // Значения нет среди вариантов — в обход попадает первый.
  const focusable = values.includes(value) ? value : values[0];
  const classes = ["tabs", size === "sm" ? "tabs--sm" : "", "segmented", className ?? ""].filter(Boolean).join(" ");
  return (
    <div role="radiogroup" aria-label={labelledBy ? undefined : label} aria-labelledby={labelledBy}
      aria-disabled={disabled || undefined} className={classes}
      onKeyDown={disabled ? undefined : radioKeys(values, value, onChange)}>
      {options.map((o) => {
        const button = (
          <button key={o.value} type="button" role="radio" aria-checked={value === o.value}
            tabIndex={o.value === focusable ? 0 : -1} disabled={disabled} aria-description={o.description}
            onClick={() => { if (o.value !== value) onChange(o.value); }}>
            {o.label}
          </button>
        );
        return o.tip ? <Tip key={o.value} content={o.tip}>{button}</Tip> : button;
      })}
    </div>
  );
}
