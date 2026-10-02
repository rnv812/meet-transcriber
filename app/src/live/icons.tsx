/** Значки панели ассистента: тонкие, в цвет текста. */

const base = {
  viewBox: "0 0 16 16", width: 14, height: 14, "aria-hidden": true, fill: "none",
  stroke: "currentColor", strokeWidth: 1.4, strokeLinecap: "round", strokeLinejoin: "round",
} as const;

export function PinIcon() {
  return <svg {...base}><path d="M6 2.5h4M7 2.5v4L4.5 9h7L9 6.5v-4M8 9v4.5" /></svg>;
}

export function CloseIcon() {
  return <svg {...base}><path d="M4 4l8 8M12 4l-8 8" /></svg>;
}

export function CopyIcon() {
  return <svg {...base}><rect x="5.5" y="5.5" width="8" height="8" rx="1.5" /><path d="M3.5 10.5h-.5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1h6.5a1 1 0 0 1 1 1v.5" /></svg>;
}

/** «Не отвлекать»: колокольчик, перечёркнутый, когда включено. */
export function QuietIcon({ on }: { on: boolean }) {
  return (
    <svg {...base}>
      <path d="M4 11.5h8l-1-1.5V7a3 3 0 0 0-6 0v3l-1 1.5zM6.8 13.5h2.4" />
      {on && <path d="M2.5 2.5l11 11" />}
    </svg>
  );
}

/** «Развернуть» (стрелка вниз) / «Свернуть» (стрелка вверх). */
export function ExpandIcon({ open }: { open: boolean }) {
  return open
    ? <svg {...base}><path d="M4 10l4-4 4 4" /></svg>
    : <svg {...base}><path d="M4 6l4 4 4-4" /></svg>;
}

export function StopIcon() {
  return <svg {...base}><rect x="4" y="4" width="8" height="8" rx="1.5" /></svg>;
}

export function MaximizeIcon({ maximized }: { maximized: boolean }) {
  return maximized
    ? <svg {...base}><path d="M2.5 9.5h4v4M13.5 6.5h-4v-4M6.5 9.5l-4.5 4.5M9.5 6.5L14 2" /></svg>
    : <svg {...base}><path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5L9 7M2.5 13.5L7 9" /></svg>;
}
