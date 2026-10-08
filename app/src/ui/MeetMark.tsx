import { useId } from "react";

/**
 * Знак Meet (0.4, вариант B «Диск с сердцевиной»): диск сияния текущей палитры
 * (--wave-* из data-aurora) с вырезанной сердцевиной — сквозь неё виден фон.
 * На малых размерах (16–24) без зерна. Значок приложения и трея — растры
 * генератора scripts/make_app_icons.py (этап 6), всегда фиолетовые.
 */
export function MeetMark({ size = 22, label }: { size?: number; label?: string }) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  return (
    <svg className="meet-mark" width={size} height={size} viewBox="0 0 24 24"
      role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <defs>
        <radialGradient id={`${id}-glow`} cx="32%" cy="26%" r="78%">
          <stop offset="0" style={{ stopColor: "var(--wave-6)" }} />
          <stop offset="0.35" style={{ stopColor: "var(--wave-5)" }} />
          <stop offset="0.7" style={{ stopColor: "var(--wave-4)" }} />
          <stop offset="1" style={{ stopColor: "var(--wave-2)" }} />
        </radialGradient>
        <mask id={`${id}-core`}>
          <rect width="24" height="24" fill="white" />
          <circle cx="12" cy="12" r="2.6" fill="black" />
        </mask>
      </defs>
      <circle cx="12" cy="12" r="11" fill={`url(#${id}-glow)`} mask={`url(#${id}-core)`} />
    </svg>
  );
}
