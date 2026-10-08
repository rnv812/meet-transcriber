import { useId, type CSSProperties } from "react";

/** Контур звезды агента — тот же, что в знаке Atlas (дизайн-система, AgentStar). */
const STAR = "M12 3L13.61 7.5C14.1 8.84 15.16 9.9 16.5 10.39L21 12L16.5 13.61C15.16 14.1 14.1 15.16 13.61 16.5L12 21L10.39 16.5C9.9 15.16 8.84 14.1 7.5 13.61L3 12L7.5 10.39C8.84 9.9 9.9 8.84 10.39 7.5Z";

export type AgentState = "rest" | "listen" | "search" | "write" | "wait";

/**
 * Звезда агента (Atlas Aurora, .agent-mark): в покое монохромная, в работе —
 * градиент сияния палитры (--agent-g1…3) с анимацией состояния. Градиент свой
 * у каждого экземпляра: правило дизайн-системы ссылается на общий #agent-grad,
 * которого в окне нет, поэтому обводка/заливка задаются inline-стилем.
 * Без `label` знак декоративный (aria-hidden).
 */
export function AgentMark({ state = "rest", size = 16, label }: { state?: AgentState; size?: number; label?: string }) {
  const grad = `agent-grad-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const active = state === "search" || state === "write" || state === "wait";
  const paint: CSSProperties | undefined = active
    ? { stroke: `url(#${grad})`, fill: state === "write" ? `url(#${grad})` : undefined }
    : undefined;
  return (
    <span className={`agent-mark${size > 32 ? " agent-mark--lg" : ""}`} data-state={state}
      style={{ "--size": `${size}px` } as CSSProperties}
      role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <svg viewBox="0 0 24 24" style={paint}>
        <defs>
          <linearGradient id={grad} x1="3" y1="3" x2="21" y2="21" gradientUnits="userSpaceOnUse">
            <stop offset="0" style={{ stopColor: "var(--agent-g1)" }} />
            <stop offset="0.5" style={{ stopColor: "var(--agent-g2)" }} />
            <stop offset="1" style={{ stopColor: "var(--agent-g3)" }} />
          </linearGradient>
        </defs>
        <path d={STAR} />
      </svg>
    </span>
  );
}
