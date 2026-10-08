/**
 * Просмотр картинки поверх окна (0.5): превью вложения в чате раскрывается во
 * весь экран; щелчок мимо картинки или Esc — закрыть. «Открыть файл» и
 * «Показать в папке» — когда у картинки есть файл на диске (передаёт вызывающий).
 * Фокус — на «Закрыть», при закрытии возвращается туда, откуда открыли; Tab не
 * уходит из просмотра.
 */

import { ExternalLink, FolderOpen, X } from "lucide-react";
import { type KeyboardEvent, type MouseEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { errorText } from "../lib/format";
import { Button } from "./Button";
import { IconButton } from "./IconButton";
import "./lightbox.css";

export function Lightbox({ src, name, onClose, onOpen, onReveal }: {
  src: string;
  /** Имя картинки: подпись, имя диалога и alt. */
  name: string;
  onClose: () => void;
  /** «Открыть файл» — программой по умолчанию; нет — кнопки нет. */
  onOpen?: () => Promise<void>;
  /** «Показать в папке»; нет — кнопки нет. */
  onReveal?: () => Promise<void>;
}) {
  const close = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const back = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      if (back && back !== document.body && document.contains(back)) back.focus();
    };
  }, [onClose]);

  /** Tab ходит по кнопкам просмотра по кругу. */
  const trap = (e: KeyboardEvent) => {
    if (e.key !== "Tab") return;
    const all = [...(box.current?.querySelectorAll<HTMLElement>("button:not(:disabled)") ?? [])];
    if (!all.length) return;
    const first = all[0]!;
    const last = all[all.length - 1]!;
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  // Мимо картинки и панели кнопок — закрыть.
  const outside = (e: MouseEvent) => { if (e.target === e.currentTarget) onClose(); };
  const run = (action?: () => Promise<void>) => () => {
    setError(null);
    action?.().catch((e) => setError(errorText(e)));
  };

  return createPortal(
    <div ref={box} className="backdrop lightbox" role="dialog" aria-modal="true" aria-label={name} onClick={outside} onKeyDown={trap}>
      <img className="lightbox__img" src={src} alt={name} />
      <div className="lightbox__bar">
        <span className="lightbox__name">{name}</span>
        {error && <span className="lightbox__error" role="alert">{error}</span>}
        {onOpen && <Button size="sm" variant="secondary" icon={ExternalLink} onClick={run(onOpen)}>Открыть файл</Button>}
        {onReveal && <Button size="sm" variant="secondary" icon={FolderOpen} onClick={run(onReveal)}>Показать в папке</Button>}
        <IconButton ref={close} icon={X} label="Закрыть" onClick={onClose} />
      </div>
    </div>,
    document.body,
  );
}
