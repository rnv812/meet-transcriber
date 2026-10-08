/**
 * Бегунок Atlas Aurora вместо системного `input[type=range]` (синий бегунок Windows).
 *
 * Как пользоваться:
 *
 *   <Slider aria-label="Порог узнавания голоса" min={50} max={95} value={pct}
 *     format={(v) => `${v}%`} onChange={setPct} />
 *   <Row label="Ждать возвращения" htmlFor="tray-wait">
 *     <Slider id="tray-wait" min={1} max={30} value={m} format={(v) => `${v} мин`} onChange={setM} />
 *   </Row>
 *   <Slider size="sm" aria-label="Громкость" min={0} max={1} step={0.05} value={vol}
 *     format={(v) => `${Math.round(v * 100)}%`} showValue={false} onChange={setVol} />
 *
 * Внутри — родной `input[type=range]` с `appearance: none`: клавиатура (стрелки,
 * Home/End, PageUp/PageDown) и роль `slider` для диктора — системные. Вид — по
 * макету: дорожка 4 px `--data-empty`, заливка до значения `--primary`, бегунок
 * 18 px (`sm` — 12 px) `--primary` с кольцом цвета фона. Значение справа —
 * Geist Mono `--text-sm`, место под самое длинное значение (строка не прыгает);
 * для диктора оно — `aria-valuetext` (из `format`), подпись справа скрыта.
 *
 * Имя — `aria-label`, `aria-labelledby` или `id` для `<label htmlFor>`. Значение
 * вне [min, max] показывается прижатым к границе.
 */

import type { CSSProperties } from "react";
import "./slider.css";

export type SliderProps = {
  value: number;
  min: number;
  max: number;
  /** Шаг (по умолчанию 1). */
  step?: number;
  onChange: (value: number) => void;
  /** Текст значения: подпись справа и `aria-valuetext` («70%», «10 мин»). Без него — число. */
  format?: (value: number) => string;
  /** Подпись значения справа (по умолчанию есть). */
  showValue?: boolean;
  /** `md` — бегунок 18 px (по умолчанию), `sm` — 12 px (плеер, тесные строки). */
  size?: "sm" | "md";
  disabled?: boolean;
  id?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
  /** Ширина дорожки: число — px, строка — как в CSS. По умолчанию — по месту (flex). */
  width?: number | string;
  className?: string;
};

export function Slider({
  value, min, max, step = 1, onChange, format, showValue = true, size = "md", disabled, id,
  width, className = "", ...aria
}: SliderProps) {
  const shown = Math.min(max, Math.max(min, value));
  const text = (v: number) => (format ? format(v) : String(v));
  const fill = max > min ? ((shown - min) / (max - min)) * 100 : 0;
  // Место под подпись — по самому длинному из крайних значений.
  const slot = Math.max(text(min).length, text(max).length);
  const style = { "--slider-fill": `${fill}%`, ...(width === undefined ? {} : { width }) } as CSSProperties;
  return (
    <span className={["slider", size === "sm" ? "slider--sm" : "", className].filter(Boolean).join(" ")} style={style}>
      <input type="range" className="slider__input" id={id} min={min} max={max} step={step} value={shown}
        disabled={disabled} aria-valuetext={text(shown)} {...aria}
        onChange={(e) => onChange(Number(e.target.value))} />
      {showValue && (
        <span className="slider__value num" aria-hidden="true" style={{ minWidth: `${slot}ch` }}>{text(shown)}</span>
      )}
    </span>
  );
}
