import { useId, useState, type ReactNode } from "react";
import { pickFolder } from "../../lib/shell";
import { Button } from "../../ui/Button";

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

/** Папка на диске: путь, «Выбрать папку…» (диалог оболочки) и «Очистить» (null). */
export function FolderRow({ label, hint, value, onChange }: {
  label: string; hint: string; value: string | null; onChange: (v: string | null) => void;
}) {
  const choose = async () => {
    const path = await pickFolder(value).catch(() => null);
    if (path) onChange(path);
  };
  return (
    <div role="group" aria-label={label}>
      <Row label={label} hint={hint}>
        {value ? <code className="path">{value}</code> : <span className="muted">не задана</span>}
        <Button onClick={() => void choose()}>Выбрать папку…</Button>
        <Button onClick={() => onChange(null)} disabled={!value}>Очистить</Button>
      </Row>
    </div>
  );
}

/** «?» с пояснением: по наведению, фокусу или нажатию; Esc закрывает. */
export function HelpTip({ label, title, children }: { label: string; title?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const id = useId();
  const shown = open || pinned;
  return (
    <span className="help" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" className="help__button" aria-label={label}
        aria-expanded={shown} aria-describedby={shown ? id : undefined}
        onClick={() => setPinned((v) => !v)} onFocus={() => setOpen(true)} onBlur={() => { setOpen(false); setPinned(false); }}
        onKeyDown={(e) => { if (e.key === "Escape") { setOpen(false); setPinned(false); } }}>
        ?
      </button>
      {shown && (
        <span role="tooltip" id={id} className="help__tip">
          {title && <span className="help__title">{title}</span>}
          {children}
        </span>
      )}
    </span>
  );
}

/** Тип сырых настроек: структуру определяет резидент, окно правит по секциям. */
export type Raw = Record<string, Record<string, any>>; // eslint-disable-line @typescript-eslint/no-explicit-any
export type SetFn = (group: string, key: string, value: unknown) => void;
