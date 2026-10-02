/**
 * ✦ «Спросить агента»: маленькая кнопка со звёздочками у реплики, пункта
 * итогов, подсказки ассистента. Подпись — в aria-label и подсказке.
 */

import type { ComponentPropsWithoutRef } from "react";
import { Sparkles } from "lucide-react";
import "./ask-agent.css";

type Props = Omit<ComponentPropsWithoutRef<"button">, "aria-label" | "children"> & {
  /** Имя кнопки для экранного диктора (можно с текстом пункта). */
  label: string;
};

export function AskAgentButton({ label, title = label, className = "", ...rest }: Props) {
  return (
    <button type="button" className={`ask-agent ${className}`.trim()} aria-label={label} title={title} {...rest}>
      <Sparkles size={14} strokeWidth={1.75} aria-hidden="true" />
    </button>
  );
}
