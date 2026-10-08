import { useEffect, useState, type ReactNode } from "react";
import "./primitives.css";

/**
 * Загрузка: индикатор и подпись — не сразу, а через `delay` мс. Быстрый
 * ответ не мигает индикатором, медленный не оставляет пустое место.
 */
export function Loading({ label = "Загрузка…", delay = 300, className = "" }: {
  label?: string; delay?: number; className?: string;
}) {
  const [shown, setShown] = useState(delay <= 0);
  useEffect(() => {
    if (delay <= 0) return;
    const t = window.setTimeout(() => setShown(true), delay);
    return () => window.clearTimeout(t);
  }, [delay]);
  return (
    <div className={`loading ${className}`.trim()} role="status" aria-live="polite" aria-busy="true">
      {shown && <><span className="btn__spinner btn__spinner--static" aria-hidden="true" /><span>{label}</span></>}
    </div>
  );
}

/** Серая заготовка на месте строки, чипа или кнопки, пока нет данных. */
export function Skeleton({ width, height = 12, className = "" }: { width?: number | string; height?: number; className?: string }) {
  return <span className={`skeleton sk ${className}`.trim()} aria-hidden="true" style={{ width, height }} />;
}

/**
 * Строка состояния фиксированной высоты: «Сохранено», ошибка, итог проверки.
 * Пустая строка занимает то же место — появление текста не сдвигает страницу.
 */
export function StatusSlot({ children, tone, className = "", lines = 1 }: {
  children?: ReactNode; tone?: "ok" | "error" | "muted"; className?: string; lines?: 1 | 2;
}) {
  return (
    <div className={`status-slot status-slot--${lines}${tone ? ` status-slot--${tone}` : ""} ${className}`.trim()}
      role="status" aria-live="polite">
      {children}
    </div>
  );
}
