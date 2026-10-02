import type { ReactNode } from "react";

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty__title">{title}</div>
      {hint && <div>{hint}</div>}
      {action}
    </div>
  );
}

/** Резидента нет: оболочка перезапускает его, окно переподключится само. */
export function OfflineState() {
  return (
    <EmptyState
      title="Служба записи не запущена"
      hint="Приложение перезапускает её — подождите несколько секунд."
    />
  );
}
