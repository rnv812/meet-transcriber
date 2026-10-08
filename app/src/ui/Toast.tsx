/**
 * Уведомление внизу окна: итог действия и, может быть, кнопка («Отменить»).
 *
 * Для экранного диктора — две области, которые есть в документе всегда
 * (`ToastAnnouncer`): `role="status"` (вежливо) и `role="alert"` (ошибки), в них —
 * только текст сообщения. Видимое уведомление с кнопками — вне этих областей:
 * кнопки не зачитываются как часть сообщения, а вставка готовой области с текстом
 * (её многие дикторы пропускают) не нужна. То же сообщение ещё раз — область
 * очищается и заполняется заново, и его снова слышно. Видимое уведомление —
 * группа с именем-текстом: перейдя на «Отменить», слышно, к чему она.
 *
 * Фокус уведомление не забирает. Пока на нём указатель или фокус — `onHold(true)`
 * (время не идёт), ушли — `onHold(false)`. Закрылось, пока фокус был в нём, —
 * фокус возвращается туда, откуда пришёл.
 */

import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "./Button";
import { IconButton } from "./IconButton";
import "./primitives.css";

/** Области для диктора: `n` — номер сообщения (новое — зачитать, даже если текст тот же). */
export function ToastAnnouncer({ n, text, error = false }: { n: number; text: string | null; error?: boolean }) {
  const [said, setSaid] = useState<{ text: string; error: boolean } | null>(null);
  useEffect(() => {
    setSaid(null);
    if (!text) return;
    const timer = setTimeout(() => setSaid({ text, error }), 30);
    return () => clearTimeout(timer);
  }, [n, text, error]);
  return (
    <>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{said && !said.error ? said.text : ""}</div>
      <div className="sr-only" role="alert" aria-atomic="true">{said?.error ? said.text : ""}</div>
    </>
  );
}

export function Toast({ text, error = false, action, onClose, onHold }: {
  text: string;
  error?: boolean;
  action?: { label: string; onClick: () => void };
  onClose: () => void;
  /** Указатель или фокус на уведомлении (true) — время не идёт; ушли (false). */
  onHold?: (on: boolean) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  /** Откуда пришёл фокус: туда он вернётся, если уведомление закроется с фокусом внутри. */
  const from = useRef<HTMLElement | null>(null);
  const hover = useRef(false);
  const focus = useRef(false);
  const hold = () => onHold?.(hover.current || focus.current);
  const holdRef = useRef(onHold);
  holdRef.current = onHold;

  useEffect(() => {
    const el = box.current;
    return () => {
      holdRef.current?.(false);
      const at = document.activeElement;
      const lost = !at || at === document.body || !at.isConnected || Boolean(el?.contains(at));
      if (lost && from.current?.isConnected) from.current.focus();
    };
  }, []);

  return (
    <div className="toasts">
      <div ref={box} className={`toast${error ? " toast--error" : ""}`} role="group" aria-label={text}
        onMouseEnter={() => { hover.current = true; hold(); }}
        onMouseLeave={() => { hover.current = false; hold(); }}
        onFocus={(e) => {
          const before = e.relatedTarget as HTMLElement | null;
          if (!focus.current && before && !box.current?.contains(before)) from.current = before;
          focus.current = true;
          hold();
        }}
        onBlur={(e) => {
          if (box.current?.contains(e.relatedTarget as Node | null)) return;
          focus.current = false;
          hold();
        }}>
        {/* Для диктора текст — в ToastAnnouncer и в имени группы; здесь он только виден. */}
        <span className="toast__text" aria-hidden="true">{text}</span>
        {action && <Button variant="ghost" size="xs" className="toast__action" onClick={action.onClick}>{action.label}</Button>}
        <IconButton icon={X} label="Закрыть уведомление" size="xs" className="toast__close" onClick={onClose} />
      </div>
    </div>
  );
}
