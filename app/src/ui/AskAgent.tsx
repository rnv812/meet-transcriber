/**
 * ✦ «Спросить агента»: маленькая кнопка со звёздочками у реплики, пункта
 * итогов, подсказки ассистента. Подпись — в aria-label и подсказке (облачко
 * Aurora, ui/Tip — не системный `title`); `title` — текст подсказки, если он
 * отличается от подписи (клавиша, короче).
 */

import type { ComponentPropsWithoutRef } from "react";
import { AgentMark } from "./AgentMark";
import { useTip } from "./Tip";
import "./ask-agent.css";

type Props = Omit<ComponentPropsWithoutRef<"button">, "aria-label" | "children"> & {
  /** Имя кнопки для экранного диктора (можно с текстом пункта). */
  label: string;
};

export function AskAgentButton({ label, title = label, className = "", ...rest }: Props) {
  // Подсказка, повторяющая имя, диктору описанием не нужна.
  const tip = useTip<HTMLButtonElement>(title, { describe: title !== label });
  return (
    <>
      <button ref={tip.ref} type="button" className={`ask-agent ${className}`.trim()} aria-label={label} {...tip.props}
        {...rest}>
        <AgentMark size={16} />
      </button>
      {tip.node}
    </>
  );
}
