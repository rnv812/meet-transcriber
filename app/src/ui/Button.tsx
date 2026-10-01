import type { ComponentPropsWithRef } from "react";

/** `ref` в React 19 — обычный проп: доходит до <button> вместе с остальными. */
type Props = ComponentPropsWithRef<"button"> & {
  variant?: "default" | "primary" | "danger";
};

export function Button({ variant = "default", className = "", ...rest }: Props) {
  const mod = variant === "default" ? "" : ` btn--${variant}`;
  return <button type="button" className={`btn${mod} ${className}`.trim()} {...rest} />;
}
