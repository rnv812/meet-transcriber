import { useEffect, useRef, useState, type ComponentPropsWithRef, type MouseEvent } from "react";
import type { LucideIcon } from "lucide-react";
import { Icon } from "./Icon";

/**
 * Кнопка окна. Иерархия: `primary` — главное действие экрана (одно),
 * `secondary` (он же `default`) — обычное, `ghost` — без рамки, в панелях
 * инструментов, `danger` — разрушающее, `link` — действие в строке текста.
 * Размеры: `md` 28 px, `sm` 24 px.
 *
 * `busy` — идёт действие: поверх подписи крутится индикатор, ширина кнопки не
 * меняется, повторное нажатие не срабатывает. Если `onClick` вернул промис,
 * кнопка сама занята до его завершения — нажатие видно сразу, а не когда
 * придёт ответ. Фокус при этом остаётся на кнопке: `disabled` его бы сбросил.
 *
 * `ref` в React 19 — обычный проп: доходит до <button> вместе с остальными.
 */
export type ButtonVariant = "default" | "secondary" | "primary" | "ghost" | "danger" | "link";
type Props = Omit<ComponentPropsWithRef<"button">, "onClick"> & {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  busy?: boolean;
  /** Значок перед подписью (lucide). */
  icon?: LucideIcon;
  onClick?: (e: MouseEvent<HTMLButtonElement>) => unknown;
};

const isThenable = (x: unknown): x is PromiseLike<unknown> =>
  !!x && typeof (x as { then?: unknown }).then === "function";

/** Занятость от промиса `onClick`: общая для Button и IconButton. */
export function useAutoBusy(onClick: ((e: MouseEvent<HTMLButtonElement>) => unknown) | undefined, busy: boolean) {
  const [auto, setAuto] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const shown = busy || auto;
  const click = (e: MouseEvent<HTMLButtonElement>) => {
    if (shown) { e.preventDefault(); return; }
    const out = onClick?.(e);
    if (isThenable(out)) {
      setAuto(true);
      const done = () => { if (alive.current) setAuto(false); };
      out.then(done, done);
    }
  };
  return { shown, click };
}

export function Button({
  variant = "default", size = "md", busy = false, icon, className = "", children, onClick, ...rest
}: Props) {
  const { shown, click } = useAutoBusy(onClick, busy);
  const v = variant === "default" ? "secondary" : variant;
  const cls = [
    "btn", v !== "secondary" ? `btn--${v}` : "", size === "sm" ? "btn--sm" : "", shown ? "btn--busy" : "", className,
  ].filter(Boolean).join(" ");
  return (
    <button type="button" className={cls} aria-busy={shown || undefined}
      aria-disabled={shown && !rest.disabled ? true : undefined} onClick={click} {...rest}>
      <span className="btn__label">
        {icon && <Icon as={icon} size={size === "sm" ? "sm" : "md"} />}
        {children}
      </span>
      {shown && <span className="btn__spinner" aria-hidden="true" />}
    </button>
  );
}
