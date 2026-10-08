import type { ComponentPropsWithRef, MouseEvent } from "react";
import type { LucideIcon } from "lucide-react";
import { useAutoBusy } from "./Button";
import { Icon } from "./Icon";
import { useMergedRef, useTip } from "./Tip";
import "./button.css";

/**
 * Кнопка-значок на Atlas Aurora: 32 px (`sm`, по умолчанию), 28 px (`xs`,
 * плотный режим) или 40 px (`md` — рядом с полем `field--md`), значок 16 или
 * 14 px. `label` обязателен — это и доступное имя, и всплывающая подсказка
 * (облачко Aurora, ui/Tip — не системный `title`; `tipSide="right"` — справа).
 * `ghost` — без рамки (по умолчанию), `secondary` — с контуром, `danger` —
 * краснеет при наведении. `pressed` —
 * кнопка-переключатель (aria-pressed): включённая — заливка и контур.
 */
type Props = Omit<ComponentPropsWithRef<"button">, "onClick" | "children"> & {
  icon: LucideIcon;
  label: string;
  variant?: "ghost" | "secondary" | "danger";
  size?: "xs" | "sm" | "md";
  pressed?: boolean;
  busy?: boolean;
  /** Подсказка, если должна отличаться от `label` (например, причина недоступности):
   *  показывается облачком и читается диктором как описание кнопки. */
  tooltip?: string;
  /** Сторона облачка: сверху (по умолчанию) или справа. */
  tipSide?: "top" | "right";
  onClick?: (e: MouseEvent<HTMLButtonElement>) => unknown;
};

const VARIANT = { ghost: "btn--ghost", secondary: "btn--outline", danger: "btn--ghost btn--ghost-danger" } as const;

export function IconButton({
  icon, label, variant = "ghost", size = "sm", pressed, busy = false, tooltip, tipSide, className = "", onClick, ref, ...rest
}: Props) {
  const { shown, click } = useAutoBusy(onClick, busy);
  // Подсказка, повторяющая имя, диктору описанием не нужна — имя уже прочитано.
  const tip = useTip<HTMLButtonElement>(tooltip ?? label, { side: tipSide, describe: tooltip !== undefined && tooltip !== label });
  const merged = useMergedRef(ref, tip.ref);
  const cls = ["btn", "btn--icon", size === "md" ? "" : "btn--sm", VARIANT[variant], shown ? "btn--busy" : "", className]
    .filter(Boolean).join(" ");
  return (
    <>
      <button ref={merged} type="button" className={cls} data-density={size === "xs" ? "compact" : undefined}
        aria-label={label} {...tip.props} aria-pressed={pressed}
        aria-busy={shown || undefined} aria-disabled={shown && !rest.disabled ? true : undefined}
        onClick={click} {...rest}>
        {shown ? <span className="btn__spinner btn__spinner--static" aria-hidden="true" />
          : <Icon as={icon} size={size === "xs" ? "sm" : "md"} />}
      </button>
      {tip.node}
    </>
  );
}
