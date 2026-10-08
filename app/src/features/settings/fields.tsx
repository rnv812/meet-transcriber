/**
 * Поля разделов настроек поверх `Row`: текст, число со степпером, минуты
 * бегунком. Вынесены из SettingsPane, когда разделы разошлись по своим файлам (0.4).
 *
 * Все поля — Aurora `field field--sm` (32 px, как кнопки `btn--sm` в строке):
 * подпись, поле и действие стоят на одной линии.
 */

import { Minus, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { IconButton } from "../../ui/IconButton";
import { Slider } from "../../ui/Slider";
import { Row } from "./Section";

/** Классы текстового поля строки: `short` — 96 px, по умолчанию — 260, `wide` — во всю колонку, моноширинным. */
export function fieldClass({ short, wide, mono }: { short?: boolean; wide?: boolean; mono?: boolean } = {}): string {
  return ["field", "field--sm", short ? "input--short" : wide ? "input--wide" : "input--mid", mono ? "input--mono" : ""]
    .filter(Boolean).join(" ");
}

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
      <input id={id} type="text" className={fieldClass({ short, wide, mono: wide })}
        placeholder={placeholder} value={value} disabled={disabled} spellCheck={wide ? false : undefined}
        onChange={(e) => onChange(e.target.value)} />
    </Row>
  );
}

/**
 * Целое число: «−» · поле (Aurora `field--sm`, без системных стрелок) · «+» и
 * единица справа. Стрелки ↑/↓ в поле работают как у родного числового поля;
 * пустое поле — `null` (решает вызывающий).
 */
export function NumberField({ id, value, min, max, step = 1, unit, label, invalid, onChange }: {
  id: string; value: number | null; min?: number; max?: number; step?: number;
  /** Единица после поля: «секунд», «с». */
  unit?: string;
  /** Имя для кнопок «−»/«+» («Минимальная длительность звонка»). */
  label: string;
  invalid?: boolean;
  onChange: (v: number | null) => void;
}) {
  const clamp = (n: number) => Math.min(max ?? Infinity, Math.max(min ?? -Infinity, n));
  const base = value ?? min ?? 0;
  return (
    <span className="stepper">
      <IconButton icon={Minus} variant="secondary" label={`Меньше: ${label}`} tooltip="Меньше"
        disabled={min !== undefined && value !== null && value <= min}
        onClick={() => onChange(clamp(base - step))} />
      <input id={id} type="number" className="field field--sm stepper__input num" min={min} max={max} step={step}
        value={value ?? ""} aria-invalid={invalid || undefined}
        onChange={(e) => {
          if (e.target.value === "") { onChange(null); return; }
          const n = Number(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }} />
      <IconButton icon={Plus} variant="secondary" label={`Больше: ${label}`} tooltip="Больше"
        disabled={max !== undefined && value !== null && value >= max}
        onClick={() => onChange(clamp(base + step))} />
      {unit && <span className="unit">{unit}</span>}
    </span>
  );
}

export function SecondsRow({ id, label, hint, value, onChange }: {
  id: string; label: string; hint: string; value: number; onChange: (v: number) => void;
}) {
  return (
    <Row label={label} hint={hint} htmlFor={id}>
      <NumberField id={id} label={label} value={value} min={0} step={5} unit="секунд"
        onChange={(n) => onChange(Math.max(0, n ?? 0))} />
    </Row>
  );
}

/**
 * Целое число минут бегунком в пределах [min, max]: вне диапазона значение
 * не выставить, а текущее видно рядом («10 мин»).
 */
export function MinutesSlider({ id, label, hint, help, value, min, max, onChange }: {
  id: string; label: string; hint?: string; help?: ReactNode; value: number; min: number; max: number;
  onChange: (v: number) => void;
}) {
  return (
    <Row label={label} hint={hint} help={help} htmlFor={id}>
      <Slider id={id} min={min} max={max} value={Math.round(value)} width={SLIDER_WIDTH}
        format={(v) => `${v} мин`} onChange={onChange} />
    </Row>
  );
}

/** Ширина бегунка в строке настроек: одна у всех (минуты, порог узнавания). */
export const SLIDER_WIDTH = 280;
