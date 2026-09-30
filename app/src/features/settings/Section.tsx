import { useId, type ReactNode } from "react";

export function Row({ label, hint, htmlFor, children }: {
  label: string; hint?: string; htmlFor?: string; children: ReactNode;
}) {
  return (
    <div className="srow">
      <div className="srow__text">
        <label className="srow__label" htmlFor={htmlFor}>{label}</label>
        {hint && <span className="srow__hint">{hint}</span>}
      </div>
      <div className="srow__control">{children}</div>
    </div>
  );
}

export function Switch({ label, hint, value, onChange }: {
  label: string; hint?: string; value: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="srow">
      <div className="srow__text">
        <span className="srow__label">{label}</span>
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

export function Radio<T extends string>({ label, hint, value, options, onChange }: {
  label: string; hint?: string; value: T;
  options: { value: T; label: string }[]; onChange: (v: T) => void;
}) {
  const name = useId();
  return (
    <Row label={label} hint={hint}>
      <div role="radiogroup" aria-label={label} className="radios">
        {options.map((o) => (
          <label key={o.value} className="radios__item">
            <input type="radio" name={name} checked={value === o.value} onChange={() => onChange(o.value)} />
            {o.label}
          </label>
        ))}
      </div>
    </Row>
  );
}

/** Тип сырых настроек: структуру определяет резидент, окно правит по секциям. */
export type Raw = Record<string, Record<string, any>>; // eslint-disable-line @typescript-eslint/no-explicit-any
export type SetFn = (group: string, key: string, value: unknown) => void;
