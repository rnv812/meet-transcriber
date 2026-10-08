/**
 * ✦ «Спросить агента»: маленькая кнопка со звёздочками у реплики, пункта
 * итогов, подсказки ассистента. Подпись — в aria-label и подсказке.
 */

import type { ComponentPropsWithoutRef } from "react";
import { AgentMark } from "./AgentMark";
import "./ask-agent.css";

type Props = Omit<ComponentPropsWithoutRef<"button">, "aria-label" | "children"> & {
  /** Имя кнопки для экранного диктора (можно с текстом пункта). */
  label: string;
};

export function AskAgentButton({ label, title = label, className = "", ...rest }: Props) {
  return (
    <button type="button" className={`ask-agent ${className}`.trim()} aria-label={label} title={title} {...rest}>
      <AgentMark size={16} />
    </button>
  );
}
