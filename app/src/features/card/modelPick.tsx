/**
 * Выбор модели у действий карточки (0.3.4): основное нажатие — модель по
 * умолчанию, стрелка — список включённых моделей с именами и тем, куда уходит
 * текст встречи. Недоступная модель видна, но выбрать её нельзя — с причиной.
 * Выбирать не из чего (включена одна модель или резидент старый) — обычная кнопка.
 */

import { useRef, useState } from "react";
import { ChevronDown, Cloud, Laptop } from "lucide-react";
import { choiceHint } from "../../lib/llm";
import type { ModelChoice } from "../../lib/types";
import { Button, type ButtonVariant } from "../../ui/Button";
import { ItemMenu, type MenuItem } from "../recordings/ItemMenu";

const ICON = { size: 16, strokeWidth: 1.75, "aria-hidden": true } as const;

/** Пункты меню «какой моделью»: по одному на включённую модель. */
export function modelMenuItems(choices: ModelChoice[], onPick: (provider: string) => void): MenuItem[] {
  return choices.map((c) => ({
    label: c.default ? `${c.label} — по умолчанию` : c.label,
    icon: c.local ? <Laptop {...ICON} /> : <Cloud {...ICON} />,
    detail: choiceHint(c),
    hint: choiceHint(c),
    // Недоступная видна и доступна с клавиатуры (причина читается), но не выбирается.
    unavailable: !c.available,
    autoFocus: c.default && c.available,
    onSelect: () => onPick(c.provider),
  }));
}

/** Подпись стрелки: «Сделать итоги: выбрать модель». */
export const pickLabel = (action: string) => `${action}: выбрать модель`;

/**
 * Кнопка действия модели с выбором модели: основное нажатие — `onRun()`
 * (модель по умолчанию), стрелка — меню включённых моделей, выбор —
 * `onRun(provider)`. `disabled` запирает основное нажатие (`reason` — почему,
 * в подсказке), `pickDisabled` — стрелку: модель по умолчанию может быть
 * недоступна, а другая включённая — нет.
 */
export function ModelSplitButton({ label, choices, onRun, disabled, pickDisabled = disabled, reason, variant }: {
  label: string;
  choices: ModelChoice[];
  onRun: (provider?: string) => void;
  disabled?: boolean;
  pickDisabled?: boolean;
  reason?: string | null;
  variant?: ButtonVariant;
}) {
  const arrow = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  if (choices.length < 2) {
    return <Button variant={variant} onClick={() => onRun()} disabled={disabled} title={reason ?? undefined}>{label}</Button>;
  }
  const close = () => { setOpen(false); arrow.current?.focus(); };
  return (
    <span className="split-btn">
      <Button variant={variant} className="split-btn__main" onClick={() => onRun()} disabled={disabled}
        title={reason ?? undefined}>{label}</Button>
      <Button ref={arrow} variant={variant} className="split-btn__more" aria-label={pickLabel(label)}
        title="Выбрать модель для этого действия" aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen((o) => !o)} disabled={pickDisabled}>
        <ChevronDown {...ICON} size={14} />
      </Button>
      {open && (
        <ItemMenu anchor={arrow} label={`${label} — какой моделью`} align="end"
          items={modelMenuItems(choices, (p) => { setOpen(false); onRun(p); })} onClose={close} />
      )}
    </span>
  );
}
