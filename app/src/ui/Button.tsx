import type { ButtonHTMLAttributes } from "react";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "danger";
};

export function Button({ variant = "default", className = "", ...rest }: Props) {
  const mod = variant === "default" ? "" : ` btn--${variant}`;
  return <button type="button" className={`btn${mod} ${className}`.trim()} {...rest} />;
}
