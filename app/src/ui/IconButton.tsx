import type { ComponentPropsWithRef, MouseEvent } from "react";
import type { LucideIcon } from "lucide-react";
import { useAutoBusy } from "./Button";
import { Icon } from "./Icon";

/**
 * Кнопка-значок: 28 px (`md`) или 24 px (`sm`), значок 16 или 14 px.
 * `label` обязателен — это и доступное имя, и всплывающая подсказка: значок
 * без подписи иначе не прочитать. `ghost` — без рамки (по умолчанию),
 * `secondary` — с рамкой, `danger` — краснеет при наведении. `pressed` —
 * кнопка-переключатель (aria-pressed), включённое состояние видно цветом.
 */
type Props = Omit<ComponentPropsWithRef<"button">, "onClick" | "children"> & {
  icon: LucideIcon;
  label: string;
  variant?: "ghost" | "secondary" | "danger";
  size?: "sm" | "md";
  pressed?: boolean;
  busy?: boolean;
  /** Подсказка, если должна отличаться от `label` (например, причина недоступности). */
  tooltip?: string;
  onClick?: (e: MouseEvent<HTMLButtonElement>) => unknown;
};

export function IconButton({
  icon, label, variant = "ghost", size = "md", pressed, busy = false, tooltip, className = "", onClick, ...rest
}: Props) {
  const { shown, click } = useAutoBusy(onClick, busy);
  const cls = [
    "icon-btn", variant !== "ghost" ? `icon-btn--${variant}` : "", size === "sm" ? "icon-btn--sm" : "",
    shown ? "icon-btn--busy" : "", className,
  ].filter(Boolean).join(" ");
  return (
    <button type="button" className={cls} aria-label={label} title={tooltip ?? label} aria-pressed={pressed}
      aria-busy={shown || undefined} aria-disabled={shown && !rest.disabled ? true : undefined}
      onClick={click} {...rest}>
      {shown ? <span className="btn__spinner btn__spinner--static" aria-hidden="true" />
        : <Icon as={icon} size={size === "sm" ? "sm" : "md"} />}
    </button>
  );
}
