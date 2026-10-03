import { useId, type ReactNode } from "react";
import { pickFolder } from "../../lib/shell";
import { Button } from "../../ui/Button";

/**
 * Строка настроек: слева подпись с пояснением (переносится, сжимается),
 * справа элемент управления (не шире 420 px). Тесно — элемент уходит под
 * подпись, а не наезжает на неё. `stack` — элемент всегда под подписью, во
 * всю ширину (списки вариантов, длинные поля: пути, адреса, команды, id
 * моделей). `help` — «?» после подписи.
 */
export function Row({ label, hint, help, htmlFor, stack, disabled, children }: {
  label: string; hint?: ReactNode; help?: ReactNode; htmlFor?: string; stack?: boolean;
  /** Строка зависит от выключенного переключателя: приглушена, пояснение — в `hint`. */
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={`srow${stack ? " srow--stack" : ""}${disabled ? " srow--disabled" : ""}`}>
      <div className="srow__text">
        <span className="srow__head">
          <label className="srow__label" htmlFor={htmlFor}>{label}</label>
          {help}
        </span>
        {hint && <span className="srow__hint">{hint}</span>}
      </div>
      <div className="srow__control">{children}</div>
    </div>
  );
}

export function Switch({ label, hint, help, value, onChange }: {
  label: string; hint?: ReactNode; help?: ReactNode; value: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="srow">
      <div className="srow__text">
        <span className="srow__head">
          <span className="srow__label">{label}</span>
          {help}
        </span>
        {hint && <span className="srow__hint">{hint}</span>}
      </div>
      <div className="srow__control">
        <button
          type="button" role="switch" aria-checked={value} aria-label={label}
          className={`switch${value ? " switch--on" : ""}`}
          onClick={() => onChange(!value)}
        >
          <span className="switch__knob" />
        </button>
      </div>
    </div>
  );
}

export function Radio<T extends string>({ label, hint, help, value, options, disabled, onChange }: {
  label: string; hint?: ReactNode; help?: ReactNode; value: T;
  options: { value: T; label: string }[];
  /** Выбор сейчас недоступен (строка приглушена, причина — рядом). */
  disabled?: boolean;
  onChange: (v: T) => void;
}) {
  const name = useId();
  return (
    <Row label={label} hint={hint} help={help} disabled={disabled}>
      <div role="radiogroup" aria-label={label} aria-disabled={disabled || undefined} className="radios">
        {options.map((o) => (
          <label key={o.value} className="radios__item">
            <input type="radio" name={name} checked={value === o.value} disabled={disabled}
              onChange={() => onChange(o.value)} />
            {o.label}
          </label>
        ))}
      </div>
    </Row>
  );
}

/**
 * Путь в строке настроек: в одну строку, длинный обрезается многоточием в
 * начале (видна сама папка), целиком — во всплывающей подсказке.
 */
export function PathText({ path }: { path: string }) {
  return (
    <code className="path path--clip" title={path} dir="rtl">
      <bdi dir="ltr">{path}</bdi>
    </code>
  );
}

/** Папка на диске: путь, «Выбрать папку…» (диалог оболочки) и «Очистить» (null). */
export function FolderRow({ label, hint, help, value, onChange }: {
  label: string; hint: ReactNode; help?: ReactNode; value: string | null; onChange: (v: string | null) => void;
}) {
  const choose = async () => {
    const path = await pickFolder(value).catch(() => null);
    if (path) onChange(path);
  };
  return (
    <div role="group" aria-label={label}>
      <Row label={label} hint={hint} help={help}>
        <span className="folder">
          {value ? <PathText path={value} /> : <span className="muted folder__empty">Не задана</span>}
          <Button onClick={() => void choose()}>Выбрать папку…</Button>
          <Button onClick={() => onChange(null)} disabled={!value}>Очистить</Button>
        </span>
      </Row>
    </div>
  );
}

/** Тип сырых настроек: структуру определяет резидент, окно правит по секциям. */
export type Raw = Record<string, Record<string, any>>; // eslint-disable-line @typescript-eslint/no-explicit-any
export type SetFn = (group: string, key: string, value: unknown) => void;
