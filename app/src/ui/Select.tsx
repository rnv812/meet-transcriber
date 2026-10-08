/**
 * Выпадающий список Atlas Aurora вместо родного `<select>` (системная стрелка и
 * системный список Windows).
 *
 * Как пользоваться:
 *
 *   <Select aria-label="Агент" size="sm" width={170} value={provider}
 *     options={providers.map((p) => ({ value: p.id, label: p.label }))} onChange={setProvider} />
 *   <Row label="Устройство" htmlFor="asr-device">
 *     <Select id="asr-device" value={device} options={DEVICES} onChange={(v) => set("asr", "device", v)} />
 *   </Row>
 *   // Список-действие: значение вне списка — заглушка, выбор — сразу действие.
 *   <Select aria-label="Объединить с…" placeholder="Объединить с…" value=""
 *     options={others.map((o) => ({ value: o.name, label: o.name }))} onChange={(name) => setConfirm({ merge: name })} />
 *
 * Пункт: `{ value, label, detail?, disabled? }` — `detail` серым справа
 * («скачана», «сейчас: …»), недоступный не выбирается и пропускается стрелками.
 * `onChange` — только при другом значении (как у родного списка).
 *
 * Вид: кнопка `.select-btn select-btn--sm|--md` (32/40 px) со стрелкой,
 * список — `.menu.open` Aurora с галочкой `.check` у выбранного, порталом в
 * body (`position: fixed`, ui/floating: не обрезается прокруткой и стеклом,
 * переворачивается у края окна). Ширина — по содержимому, `width` — своя
 * (`"100%"` — на всю строку); список не уже кнопки.
 *
 * Доступность (ARIA «select-only combobox»): кнопка — `role="combobox"`
 * (`getByRole("combobox")`, как у родного `<select>`), список — `listbox`,
 * пункты — `option` с `aria-selected`; фокус всё время на кнопке, текущий пункт —
 * `aria-activedescendant`. Клавиатура: ↓/↑/Enter/пробел раскрывают; в списке
 * ↓↑, Home/End, PageUp/PageDown, ввод букв — к пункту на эти буквы; Enter и
 * пробел выбирают, Esc закрывает без изменений (и только список — не окно
 * вокруг), Tab закрывает. Имя — `aria-label`, `aria-labelledby` или `id` для
 * `<label htmlFor>`. `disabledReason` — подсказка (ui/Tip) и описание у
 * недоступного списка.
 */

import { ChevronDown, Check } from "lucide-react";
import {
  useCallback, useEffect, useId, useRef, useState,
  type CSSProperties, type KeyboardEvent, type Ref,
} from "react";
import { createPortal } from "react-dom";
import { floatingStyle, useFloating, type Align } from "./floating";
import { Icon } from "./Icon";
import { useMergedRef, useTip } from "./Tip";
import "./select.css";

export type SelectOption<T extends string = string> = {
  value: T;
  label: string;
  /** Пояснение серым справа в списке («скачана», «сейчас: …»); на кнопке не показывается. */
  detail?: string;
  disabled?: boolean;
};

export type SelectProps<T extends string = string> = {
  options: readonly SelectOption<T>[];
  /** Выбранное значение; не из списка (или null) — на кнопке `placeholder`. */
  value: T | "" | null | undefined;
  onChange: (value: T) => void;
  /** Текст на кнопке, когда значение не из списка. */
  placeholder?: string;
  /** `md` — 40 px (по умолчанию, рядом с `field--md` и `btn` md), `sm` — 32 px. */
  size?: "sm" | "md";
  /** Ширина кнопки: число — px, строка — как в CSS. По умолчанию — по содержимому. */
  width?: number | string;
  disabled?: boolean;
  /** Почему недоступен: подсказка при наведении и описание для диктора. */
  disabledReason?: string;
  /** Край списка у кнопки: `start` (по умолчанию) или `end` — у правого края окна. */
  align?: Align;
  id?: string;
  className?: string;
  ref?: Ref<HTMLButtonElement>;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
};

/** Пауза, после которой ввод букв начинается заново, мс. */
export const TYPEAHEAD_MS = 500;
const PAGE = 10;

export function Select<T extends string = string>({
  options, value, onChange, placeholder = "", size = "md", width, disabled = false, disabledReason, align = "start",
  id, className = "", ref, ...aria
}: SelectProps<T>) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [minWidth, setMinWidth] = useState<number | undefined>(undefined);
  const button = useRef<HTMLButtonElement | null>(null);
  const menu = useRef<HTMLUListElement>(null);
  const typed = useRef({ text: "", at: 0 });
  const autoId = useId();
  const buttonId = id ?? `${autoId}-button`;
  const listId = `${autoId}-list`;
  const optionId = (i: number) => `${autoId}-opt-${i}`;
  const tip = useTip<HTMLButtonElement>(disabled ? disabledReason : undefined);
  const setButton = useCallback((node: HTMLButtonElement | null) => { button.current = node; }, []);
  const merged = useMergedRef(ref, tip.ref, setButton);
  const pos = useFloating(open ? button : null, menu, { align });

  const selected = options.findIndex((o) => o.value === value);
  const enabled = (i: number) => i >= 0 && i < options.length && !options[i]!.disabled;

  /** Ближайший доступный пункт от `from` в направлении `dir` (включая сам `from`). */
  const seek = useCallback((from: number, dir: 1 | -1) => {
    for (let i = from; i >= 0 && i < options.length; i += dir) if (!options[i]!.disabled) return i;
    return -1;
  }, [options]);
  const first = () => seek(0, 1);
  const last = () => seek(options.length - 1, -1);

  const close = useCallback(() => {
    setOpen(false);
    typed.current = { text: "", at: 0 };
  }, []);

  const show = (at: number) => {
    if (disabled) return;
    setMinWidth(button.current?.getBoundingClientRect().width || undefined);
    setActive(at);
    setOpen(true);
  };
  const showAtSelected = () => show(enabled(selected) ? selected : first());

  const choose = (i: number) => {
    if (!enabled(i)) return;
    close();
    button.current?.focus({ preventScroll: true });
    if (i !== selected) onChange(options[i]!.value);
  };

  /** Ввод букв: к пункту, подпись которого начинается с набранного. */
  const typeahead = (ch: string, base: number) => {
    const now = Date.now();
    const prev = now - typed.current.at > TYPEAHEAD_MS ? "" : typed.current.text;
    const text = (prev + ch).toLowerCase();
    typed.current = { text, at: now };
    // Одна и та же буква подряд — по кругу между пунктами на неё.
    const same = [...text].every((c) => c === text[0]);
    const needle = same ? text[0]! : text;
    const start = text.length === 1 || same ? base + 1 : Math.max(base, 0);
    for (let k = 0; k < options.length; k++) {
      const i = (start + k + options.length) % options.length;
      if (enabled(i) && options[i]!.label.toLowerCase().startsWith(needle)) return i;
    }
    return -1;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    const printable = e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey;
    const typing = Date.now() - typed.current.at <= TYPEAHEAD_MS && typed.current.text !== "";
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        showAtSelected();
      } else if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        show(e.key === "Home" ? first() : last());
      } else if (printable) {
        e.preventDefault();
        const base = enabled(selected) ? selected : -1;
        const hit = typeahead(e.key, base);
        show(hit >= 0 ? hit : enabled(selected) ? selected : first());
      }
      return;
    }
    const move = (to: number) => { e.preventDefault(); if (to >= 0) setActive(to); };
    const next = seek(active + 1, 1);
    const prev = seek(active - 1, -1);
    switch (e.key) {
      case "ArrowDown": return move(next >= 0 ? next : active);
      case "ArrowUp":
        // Alt+↑ — выбрать и закрыть, как у родного списка.
        if (e.altKey) { e.preventDefault(); choose(active); return; }
        return move(prev >= 0 ? prev : active);
      case "Home": return move(first());
      case "End": return move(last());
      case "PageDown": return move(seek(Math.min(options.length - 1, active + PAGE), -1));
      case "PageUp": return move(seek(Math.max(0, active - PAGE), 1));
      case "Enter": e.preventDefault(); choose(active); return;
      case "Tab": close(); return;
      case " ":
        if (typing) break;
        e.preventDefault();
        choose(active);
        return;
      default: break;
    }
    if (printable) {
      e.preventDefault();
      const hit = typeahead(e.key, active);
      if (hit >= 0) setActive(hit);
    }
  };

  // Esc — только список: перехват на window раньше окна вокруг (Popover, диалог).
  // Нажатие вне кнопки и списка закрывает.
  useEffect(() => {
    if (!open) return;
    const key = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      e.preventDefault();
      close();
    };
    const down = (e: MouseEvent) => {
      const target = e.target as Node;
      if (button.current?.contains(target) || menu.current?.contains(target)) return;
      close();
    };
    window.addEventListener("keydown", key, true);
    document.addEventListener("mousedown", down);
    return () => {
      window.removeEventListener("keydown", key, true);
      document.removeEventListener("mousedown", down);
    };
  }, [open, close]);

  // Текущий пункт — в видимой части списка.
  useEffect(() => {
    if (!open || active < 0) return;
    document.getElementById(optionId(active))?.scrollIntoView?.({ block: "nearest" });
    // optionId строится от useId — постоянен, в зависимостях не нужен.
  }, [open, active]);

  const chosen = selected >= 0 ? options[selected] : undefined;
  const style: CSSProperties | undefined = width === undefined ? undefined : { width };
  const cls = ["select-btn", size === "sm" ? "select-btn--sm" : "select-btn--md", "selectbox", className]
    .filter(Boolean).join(" ");
  const describedBy = aria["aria-describedby"];

  return (
    <>
      <button ref={merged} type="button" id={buttonId} className={cls} style={style} disabled={disabled}
        role="combobox" aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined}
        aria-activedescendant={open && active >= 0 ? optionId(active) : undefined}
        aria-label={aria["aria-label"]} aria-labelledby={aria["aria-labelledby"]} aria-describedby={describedBy}
        {...tip.props}
        onClick={() => (open ? close() : showAtSelected())}
        onKeyDown={onKeyDown}
        // Пробел у кнопки нажимает её на отпускании (Firefox, user-event) — уже обработан в keydown.
        onKeyUp={(e) => { if (e.key === " ") e.preventDefault(); }}
        onBlur={(e) => {
          const to = e.relatedTarget as Node | null;
          if (open && !menu.current?.contains(to)) close();
        }}>
        {chosen
          ? <span className="selectbox__text">{chosen.label}</span>
          : <span className="selectbox__text selectbox__placeholder">{placeholder}</span>}
        <Icon as={ChevronDown} size={size === "sm" ? "sm" : "md"} className="ic" />
      </button>
      {tip.node}
      {open && createPortal(
        <ul ref={menu} id={listId} role="listbox"
          aria-label={aria["aria-label"]} aria-labelledby={aria["aria-label"] ? undefined : aria["aria-labelledby"] ?? buttonId}
          className={["menu", "open", "selectbox__menu", size === "sm" ? "selectbox__menu--sm" : ""].filter(Boolean).join(" ")}
          style={{ ...floatingStyle(pos), minWidth }}>
          {options.map((o, i) => (
            <li key={o.value} id={optionId(i)} role="option" aria-selected={i === selected}
              aria-disabled={o.disabled || undefined} className={i === active ? "active" : undefined}
              // Фокус остаётся на кнопке: нажатие на пункт его не забирает.
              onMouseDown={(e) => e.preventDefault()}
              onMouseMove={() => { if (!o.disabled && i !== active) setActive(i); }}
              onClick={() => choose(i)}>
              <Icon as={Check} className="check" />
              <span className="selectbox__label">{o.label}</span>
              {o.detail && <small>{o.detail}</small>}
            </li>
          ))}
        </ul>,
        document.body,
      )}
    </>
  );
}
