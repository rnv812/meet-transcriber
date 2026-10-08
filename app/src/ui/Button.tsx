import { useEffect, useRef, useState, type ComponentPropsWithRef, type MouseEvent } from "react";
import type { LucideIcon } from "lucide-react";
import { Icon } from "./Icon";
import "./button.css";

/**
 * Кнопка окна на Atlas Aurora. Иерархия: `primary` — главное действие экрана
 * (одно), `aurora` — действие ИИ, `tonal` — второе акцентное рядом с главной,
 * `secondary` (он же `default`) — обычное (контур), `ghost` — без рамки, в
 * панелях, `danger` — разрушающее, `mono`/`deep` — сильное или выбранное без
 * цвета, `link` — действие в строке текста.
 * Размеры: `xs` 28 (плотный режим), `sm` 32 (по умолчанию), `md` 40, `lg` 48.
 * `flat` — главная/ИИ без внешнего свечения (тесные места).
 *
 * `busy` — идёт действие: поверх подписи крутится индикатор, ширина кнопки не
 * меняется, повторное нажатие не срабатывает. Если `onClick` вернул промис,
 * кнопка сама занята до его завершения — нажатие видно сразу, а не когда
 * придёт ответ. Фокус при этом остаётся на кнопке: `disabled` его бы сбросил.
 *
 * `ref` в React 19 — обычный проп: доходит до <button> вместе с остальными.
 */
export type ButtonVariant = "default" | "secondary" | "primary" | "ghost" | "danger" | "link"
  | "aurora" | "tonal" | "mono" | "deep";
export type ButtonSize = "xs" | "sm" | "md" | "lg";
type Props = Omit<ComponentPropsWithRef<"button">, "onClick"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  flat?: boolean;
  busy?: boolean;
  /** Значок перед подписью (lucide). */
  icon?: LucideIcon;
  onClick?: (e: MouseEvent<HTMLButtonElement>) => unknown;
};

const VARIANT: Record<Exclude<ButtonVariant, "default">, string> = {
  secondary: "btn--outline", primary: "btn--primary", ghost: "btn--ghost", danger: "btn--danger",
  link: "btn--link", aurora: "btn--aurora", tonal: "btn--tonal", mono: "btn--mono", deep: "btn--deep",
};
/** Размер → класс Aurora; `xs` — тот же малый, но в плотном режиме (28). */
export const SIZE_CLASS: Record<ButtonSize, string> = { xs: "btn--sm", sm: "btn--sm", md: "", lg: "btn--lg" };

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
  variant = "default", size = "sm", flat = false, busy = false, icon, className = "", children, onClick, ...rest
}: Props) {
  const { shown, click } = useAutoBusy(onClick, busy);
  const v = variant === "default" ? "secondary" : variant;
  const cls = [
    "btn", VARIANT[v], v === "link" ? "" : SIZE_CLASS[size], flat ? "btn--flat" : "", shown ? "btn--busy" : "", className,
  ].filter(Boolean).join(" ");
  return (
    <button type="button" className={cls} data-density={size === "xs" ? "compact" : undefined}
      aria-busy={shown || undefined} aria-disabled={shown && !rest.disabled ? true : undefined}
      onClick={click} {...rest}>
      <span className="btn__label">
        {icon && <Icon as={icon} size={size === "xs" ? "sm" : "md"} />}
        {children}
      </span>
      {shown && <span className="btn__spinner" aria-hidden="true" />}
    </button>
  );
}
