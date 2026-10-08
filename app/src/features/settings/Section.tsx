import { createContext, useContext, useId, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { pickFolder } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { Disclosure } from "../../ui/Disclosure";
import { radioKeys, Segmented as SegmentedControl } from "../../ui/Segmented";
import { Tip } from "../../ui/Tip";

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

export function Switch({ label, hint, help, value, disabled, onChange }: {
  label: string; hint?: ReactNode; help?: ReactNode; value: boolean;
  /** Сейчас не переключить (строка приглушена, причина — в `hint`). */
  disabled?: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className={`srow${disabled ? " srow--disabled" : ""}`}>
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
          className="switch" disabled={disabled}
          onClick={() => onChange(!value)}
        />
      </div>
    </div>
  );
}

/**
 * Варианты столбиком (длинные подписи, вариант с полем под ним): радио Aurora
 * `.rd` в `.check-row`. Короткое перечисление в 2–3 слова — `Segmented`.
 * `stack` — варианты под подписью, иначе справа в строку.
 */
export function Radio<T extends string>({ label, hint, help, value, options, disabled, stack, onChange, children }: {
  label: string; hint?: ReactNode; help?: ReactNode; value: T;
  options: { value: T; label: ReactNode }[];
  /** Выбор сейчас недоступен (строка приглушена, причина — рядом). */
  disabled?: boolean;
  stack?: boolean;
  onChange: (v: T) => void;
  /** Под вариантами (поле своего значения). */
  children?: ReactNode;
}) {
  const name = useId();
  return (
    <Row label={label} hint={hint} help={help} disabled={disabled} stack={stack}>
      <div role="radiogroup" aria-label={label} aria-disabled={disabled || undefined}
        className={`radios${stack ? " radios--column" : ""}`}>
        {options.map((o) => (
          <label key={o.value} className="check-row radios__item">
            <input type="radio" className="rd" name={name} checked={value === o.value} disabled={disabled}
              onChange={() => onChange(o.value)} />
            {o.label}
          </label>
        ))}
      </div>
      {children}
    </Row>
  );
}

/** Стрелки в группе радио-кнопок — общие с сегментами (`ui/Segmented`). */
export { radioKeys };

/**
 * Короткое перечисление (2–3 варианта в слово-два) справа в строке: общие
 * сегменты `ui/Segmented` (`sm`) — те же, что «Как часто писать» и «Профиль»
 * в панели встречи. `title` варианта — пояснение диктору.
 */
export function Segmented<T extends string>({ label, hint, help, value, options, disabled, onChange }: {
  label: string; hint?: ReactNode; help?: ReactNode; value: T;
  options: readonly { value: T; label: string; title?: string }[];
  disabled?: boolean;
  onChange: (v: T) => void;
}) {
  return (
    <Row label={label} hint={hint} help={help} disabled={disabled}>
      <SegmentedControl label={label} value={value} disabled={disabled} onChange={onChange}
        options={options.map((o) => ({ value: o.value, label: o.label, description: o.title }))} />
    </Row>
  );
}

/**
 * Путь в строке настроек: в одну строку, длинный обрезается многоточием в
 * начале (видна сама папка), целиком — в подсказке Aurora (ui/Tip).
 */
export function PathText({ path }: { path: string }) {
  return (
    <Tip content={path} describe={false}>
      <code className="path path--clip" dir="rtl">
        <bdi dir="ltr">{path}</bdi>
      </code>
    </Tip>
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

/**
 * Подгруппа раздела настроек (0.4): карточка Atlas Aurora с заголовком и
 * строками `Row`/`Switch`/`Radio`. Без `title` — карточка без заголовка (раздел
 * из одной подгруппы).
 */
export function SettingsCard({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="card scard">
      {title && <h3 className="type-card-title scard__title">{title}</h3>}
      {children}
    </section>
  );
}

/**
 * Просьба раскрыть «Тонкую настройку» (поиск по настройкам нашёл строку в ней):
 * номер просьбы, 0 — просьбы нет. Каждый новый номер раскрывает блок ещё раз.
 */
export const RevealFineTuning = createContext(0);

/**
 * «Тонкая настройка» (0.4): свёрнутый блок внизу раздела для редкого. Раскрыт
 * сразу, если `defaultOpen` (например, в нём уже есть заданное значение), и
 * по просьбе поиска (`RevealFineTuning`). `pinned` — не сворачивается (в нём
 * ошибка, из-за которой «Сохранить» недоступно: её нельзя спрятать).
 */
export function FineTuning({ defaultOpen = false, pinned = false, children }: {
  defaultOpen?: boolean; pinned?: boolean; children: ReactNode;
}) {
  const reveal = useContext(RevealFineTuning);
  const [open, setOpen] = useState(defaultOpen);
  const [seen, setSeen] = useState(0);
  // Новая просьба раскрыть — до отрисовки, чтобы поиск сразу нашёл строку.
  if (reveal !== 0 && reveal !== seen) {
    setSeen(reveal);
    setOpen(true);
  }
  return (
    <section className="card scard sfine">
      <Disclosure title="Тонкая настройка" open={open || pinned} onToggle={(x) => setOpen(x || pinned)}>{children}</Disclosure>
    </section>
  );
}

/**
 * Место в шапке раздела под заголовком (SettingsPane) для «см. также» всего
 * раздела: `SeeAlso head` выносится туда порталом. Нет места (раздел отрисован
 * отдельно, в тестах) — строка остаётся на месте.
 */
export const SeeAlsoSlot = createContext<HTMLElement | null>(null);

/**
 * Строка-ссылка на другой раздел: «… — в разделе «Спикеры»». `head` — про весь
 * раздел: в шапку под заголовок; без него — на месте (внутри карточки).
 */
export function SeeAlso({ head = false, children }: { head?: boolean; children: ReactNode }) {
  const slot = useContext(SeeAlsoSlot);
  if (head && slot) return createPortal(<span className="see-also">{children}</span>, slot);
  return <p className="muted sdesc see-also">{children}</p>;
}

/** Тип сырых настроек: структуру определяет резидент, окно правит по секциям. */
export type Raw = Record<string, Record<string, any>>; // eslint-disable-line @typescript-eslint/no-explicit-any
export type SetFn = (group: string, key: string, value: unknown) => void;
