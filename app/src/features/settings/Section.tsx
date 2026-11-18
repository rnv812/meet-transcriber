import { createContext, Fragment, useContext, useId, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { pickFolder } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { Disclosure } from "../../ui/Disclosure";
import { radioKeys, Segmented as SegmentedControl } from "../../ui/Segmented";
import { Tip } from "../../ui/Tip";
import type { SectionId } from "./settingsIndex";

/**
 * Подписи строк текущего раздела, чьё значение в черновике отличается от
 * умолчания этого пользователя (SettingsPane по `keys` индекса и ответу
 * резидента). Пусто — меток нет.
 */
export const ChangedLabels = createContext<ReadonlySet<string>>(new Set());

/**
 * Слово «изменено» после подписи строки, если опция не по умолчанию: текстом
 * на токене приглушённого текста — его читает экранный диктор, это не только цвет.
 */
export function ChangedMark({ label }: { label: string }) {
  const changed = useContext(ChangedLabels);
  return changed.has(label) ? <span className="srow__changed">изменено</span> : null;
}

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
          <ChangedMark label={label} />
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
          <ChangedMark label={label} />
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
 * из одной подгруппы). `block` — якорь блока (`data-block`): к нему прокручивает
 * переход по прежнему id раздела (SettingsPane, `LEGACY_SECTION`).
 */
export function SettingsCard({ title, block, children }: { title?: string; block?: string; children: ReactNode }) {
  return (
    <section className="card scard" data-block={block}>
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

/**
 * Опция, к которой ведёт ссылка: подпись строки (как в индексе поиска),
 * раздел (по умолчанию — открытый) и `scope` — селектор блока, внутри
 * которого искать строку, если подпись в разделе не одна (строки
 * «Видеокарта» и «Процессор» в «Распознавании»: `[data-device="cpu"]`).
 */
export type OptionTarget = { to: string; section?: SectionId; scope?: string };

/**
 * Перейти к опции: SettingsPane открывает раздел, раскрывает «Тонкую
 * настройку» и прокручивает к строке — так же, как выбор в поиске. Нет
 * (раздел отрисован отдельно, в тестах) — части строки «Как сейчас» без ссылок.
 */
export const OpenOption = createContext<((target: OptionTarget) => void) | null>(null);

/** Часть строки «Как сейчас настроено»: текст и опция, к которой он ведёт. */
export type NowPart = OptionTarget & { text: string };

/**
 * «Как сейчас настроено» (0.6.2): одна строка о сохранённых настройках
 * раздела (не о черновике), каждая часть — ссылка к своей опции.
 */
export function NowLine({ parts }: { parts: NowPart[] }) {
  const open = useContext(OpenOption);
  if (parts.length === 0) return null;
  return (
    <div role="group" aria-label="Как сейчас настроено" className="snow">
      <span className="snow__lead">Сейчас настроено:</span>{" "}
      {parts.map((p, i) => (
        <Fragment key={`${p.to}-${i}`}>
          {i > 0 && <span aria-hidden="true"> · </span>}
          {open ? <Button variant="link" onClick={() => open(p)}>{p.text}</Button> : <span>{p.text}</span>}
        </Fragment>
      ))}
    </div>
  );
}

/** Модель, которую придётся скачать под подобранные значения. */
export type FitDownload = { id: string; title: string; size_gb: number; why: string };

/** Что подобрано: для какой машины, какие правки (текстом) и что скачать (null — неизвестно). */
export type FitPlan = { title: string; changes: string[]; downloads: FitDownload[] | null };

/** Гигабайты по-русски: «0,45 ГБ», «3,1 ГБ». */
export const gbText = (gb: number): string => `${String(Number(gb.toFixed(2))).replace(".", ",")} ГБ`;

/**
 * «Подобрать под этот компьютер» (0.6.2): кнопка кладёт значения `plan` в
 * черновик (`onApply`) и показывает, что изменилось и сколько придётся
 * скачать; пишет обычное «Сохранить». Пока сведения о машине загружаются —
 * недоступна; правок нет (черновик уже такой) — «Уже подобрано».
 */
export function FitPanel({ state, plan, downloadNote, onApply }: {
  state: "loading" | "unavailable" | "ready";
  /** Подбор для этой машины; `changes` — отличия от черновика. */
  plan: FitPlan | null;
  /** Где скачать модели (ссылка на блок). */
  downloadNote?: ReactNode;
  onApply: () => void;
}) {
  // Что показали после нажатия: список правок на тот момент (после него черновик уже совпадает).
  const [shown, setShown] = useState<FitPlan | null>(null);
  const ready = state === "ready" && plan !== null;
  const matched = ready && plan.changes.length === 0;
  const visible = matched ? shown : null;
  const apply = () => {
    if (!plan) return;
    setShown(plan);
    onApply();
  };
  const total = visible?.downloads?.reduce((sum, d) => sum + d.size_gb, 0) ?? 0;
  return (
    <div className="fit">
      <div className="fit__bar">
        <Button onClick={apply} disabled={!ready || matched}>{matched ? "Уже подобрано" : "Подобрать под этот компьютер"}</Button>
        {state === "loading" && <span className="fit__note">Узнаю, что это за компьютер…</span>}
        {state === "unavailable" && <span className="fit__note">Сведения о компьютере недоступны</span>}
      </div>
      <div aria-live="polite">
        {visible && (
          <div className="fit__result">
            <p className="fit__title">{visible.title}</p>
            <ul className="fit__list" aria-label="Что изменится">
              {visible.changes.map((c) => <li key={c}>{c}</li>)}
            </ul>
            {visible.downloads === null ? (
              <p className="fit__text">Сколько скачать — неизвестно: список моделей не загрузился.</p>
            ) : visible.downloads.length === 0 ? (
              <p className="fit__text">Скачивать ничего не нужно.</p>
            ) : (
              <>
                <ul className="fit__list" aria-label="Что скачать">
                  {visible.downloads.map((d) => <li key={d.id}>{d.title} — {gbText(d.size_gb)} ({d.why})</li>)}
                </ul>
                <p className="fit__text">Всего {gbText(total)}.{downloadNote && <> {downloadNote}</>}</p>
              </>
            )}
            <p className="fit__text">Применится после «Сохранить».</p>
          </div>
        )}
      </div>
    </div>
  );
}

/** Тип сырых настроек: структуру определяет резидент, окно правит по секциям. */
export type Raw = Record<string, Record<string, any>>; // eslint-disable-line @typescript-eslint/no-explicit-any
export type SetFn = (group: string, key: string, value: unknown) => void;
