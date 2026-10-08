/**
 * Поля разделов настроек поверх `Row`: текст, секунды, минуты ползунком.
 * Вынесены из SettingsPane, когда разделы разошлись по своим файлам (0.4).
 */

import type { ReactNode } from "react";
import { Row } from "./Section";

/**
 * Ширина поля: `short` — короткое значение (имя, код языка), по умолчанию —
 * средняя, `wide` — пути, адреса, команды, id моделей: под подписью во всю
 * ширину колонки, моноширинным шрифтом.
 */
export function TextRow({ id, label, hint, help, value, placeholder, short, wide, disabled, onChange }: {
  id: string; label: string; hint?: string; help?: ReactNode; value: string; placeholder?: string; short?: boolean;
  wide?: boolean; disabled?: boolean; onChange: (v: string) => void;
}) {
  return (
    <Row label={label} hint={hint} help={help} htmlFor={id} stack={wide} disabled={disabled}>
      <input id={id} type="text" className={short ? "input--short" : wide ? "input--wide" : undefined}
        placeholder={placeholder} value={value} disabled={disabled} spellCheck={wide ? false : undefined}
        onChange={(e) => onChange(e.target.value)} />
    </Row>
  );
}

export function SecondsRow({ id, label, hint, value, onChange }: {
  id: string; label: string; hint: string; value: number; onChange: (v: number) => void;
}) {
  return (
    <Row label={label} hint={hint} htmlFor={id}>
      <span className="with-unit">
        <input id={id} type="number" min={0} className="num" value={value}
          onChange={(e) => { const n = Number(e.target.value); if (Number.isFinite(n)) onChange(Math.max(0, n)); }} />
        <span className="unit unit--slot">секунд</span>
      </span>
    </Row>
  );
}

/**
 * Целое число минут ползунком в пределах [min, max]: вне диапазона значение
 * не выставить, а текущее видно рядом («10 мин»).
 */
export function MinutesSlider({ id, label, hint, help, value, min, max, onChange }: {
  id: string; label: string; hint?: string; help?: ReactNode; value: number; min: number; max: number;
  onChange: (v: number) => void;
}) {
  const shown = Math.min(max, Math.max(min, Math.round(value)));
  return (
    <Row label={label} hint={hint} help={help} htmlFor={id}>
      <span className="with-unit">
        <input id={id} type="range" min={min} max={max} step={1} value={shown} aria-valuetext={`${shown} мин`}
          onChange={(e) => onChange(Number(e.target.value))} />
        <span className="unit unit--slot num">{shown} мин</span>
      </span>
    </Row>
  );
}
