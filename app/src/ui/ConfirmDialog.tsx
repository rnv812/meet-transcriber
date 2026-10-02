/**
 * Подтверждение разрушающего действия — одно на всё окно.
 *
 * Правила: заголовок — вопрос о действии, текст — что именно пропадёт; фокус
 * сразу на безопасной кнопке («Отмена»), Esc — тоже отмена, Enter на
 * безопасной кнопке ничего не ломает. Кнопка действия — красная (`danger`),
 * если действие необратимо. `alt` — третий вариант («Не сохранять»).
 *
 * `inline` — блок в потоке страницы (у строки списка, в меню); без него —
 * модальное окно поверх затемнения: Tab не уходит из окна (остальная страница
 * на это время `inert`), щелчок по затемнению — отмена. После закрытия фокус
 * возвращается в `returnFocus` (кнопка, открывшая меню) или туда, где был.
 */

import {
  useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { Button } from "./Button";
import "./primitives.css";

export type ConfirmOptions = {
  title: string;
  message?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** Красная кнопка действия (по умолчанию — да: подтверждают разрушающее). */
  danger?: boolean;
  /** Третья кнопка между отменой и действием. */
  alt?: { label: string; danger?: boolean; onClick: () => unknown };
};

type Props = ConfirmOptions & {
  onConfirm: () => unknown;
  onCancel: () => void;
  busy?: boolean;
  inline?: boolean;
  className?: string;
  /** Куда вернуть фокус после закрытия (подтверждение открыто из меню, которое уже закрыто). */
  returnFocus?: RefObject<HTMLElement | null>;
};

const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

export function ConfirmDialog({
  title, message, confirmLabel, cancelLabel = "Отмена", danger = true, alt, onConfirm, onCancel, busy = false,
  inline = false, className = "", returnFocus,
}: Props) {
  const backdrop = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const safe = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const textId = useId();
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;

  const returnRef = useRef(returnFocus);
  returnRef.current = returnFocus;

  // Фокус — на безопасной кнопке; у модального окна остальная страница на это
  // время `inert` (ни Tab, ни щелчок, ни чтение). После закрытия — сначала снять
  // `inert`, потом вернуть фокус: браузер не ставит фокус внутрь inert, и
  // обратный порядок оставил бы фокус на <body>. Поэтому — одним эффектом.
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    safe.current?.focus();
    const own = backdrop.current;
    const marked = inline ? []
      : [...document.body.children].filter((el) => el !== own && !el.hasAttribute("inert"));
    marked.forEach((el) => el.setAttribute("inert", ""));
    return () => {
      marked.forEach((el) => el.removeAttribute("inert"));
      const target = returnRef.current?.current ?? before;
      if (target && target !== document.body && document.contains(target)) target.focus();
    };
  }, [inline]);

  // Модальное окно ловит Esc, где бы ни был фокус.
  useEffect(() => {
    if (inline) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancelRef.current(); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [inline]);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape" && inline) {
      e.preventDefault();
      e.stopPropagation();
      onCancel();
      return;
    }
    if (e.key !== "Tab" || inline || !box.current) return;
    const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (items.length === 0) return;
    const first = items[0]!;
    const last = items[items.length - 1]!;
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  const dialog = (
    <div ref={box} role="alertdialog" aria-modal={inline ? undefined : true} aria-labelledby={titleId}
      tabIndex={-1}
      aria-describedby={message ? textId : undefined} onKeyDown={onKey}
      className={`confirm${inline ? " confirm--inline" : ""} ${className}`.trim()}>
      <div className="confirm__title" id={titleId}>{title}</div>
      {message && <div className="confirm__text" id={textId}>{message}</div>}
      <div className="confirm__actions">
        <Button ref={safe} size={inline ? "sm" : "md"} onClick={onCancel}>{cancelLabel}</Button>
        {alt && (
          <Button size={inline ? "sm" : "md"} variant={alt.danger ? "danger" : "default"} disabled={busy}
            onClick={alt.onClick}>{alt.label}</Button>
        )}
        <Button size={inline ? "sm" : "md"} variant={danger ? "danger" : "primary"} busy={busy}
          onClick={onConfirm}>{confirmLabel}</Button>
      </div>
    </div>
  );
  if (inline) return dialog;
  return createPortal(
    <div ref={backdrop} className="confirm-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      {dialog}
    </div>,
    document.body,
  );
}

/**
 * Подтверждение из обработчика: `if (await confirm({...})) удалить()`.
 * `node` нужно отрисовать в компоненте (модальное окно уходит в body).
 */
export function useConfirm(): [ReactNode, (opts: ConfirmOptions) => Promise<boolean>] {
  const [asked, setAsked] = useState<(ConfirmOptions & { resolve: (ok: boolean) => void }) | null>(null);
  const confirm = useCallback((opts: ConfirmOptions) => new Promise<boolean>((resolve) => {
    setAsked({ ...opts, resolve });
  }), []);
  const close = (ok: boolean) => {
    asked?.resolve(ok);
    setAsked(null);
  };
  const node = asked
    ? <ConfirmDialog {...asked} onConfirm={() => close(true)} onCancel={() => close(false)} />
    : null;
  return [node, confirm];
}
