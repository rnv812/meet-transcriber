/**
 * Бейдж «ИИ» у названия встречи, которое предложила модель (`title_source: "ai"`).
 * Нажатие — переименовать запись: название человека бейджа уже не имеет.
 * `by` — какая модель придумала название («Claude Code (sonnet)»), в подсказке.
 */

import type { MouseEvent } from "react";
import "./ai-badge.css";

export const AI_BADGE_HINT = "Название предложено ИИ — нажмите, чтобы изменить";

/** Подсказка бейджа: с моделью, если она известна. */
export const aiBadgeHint = (by?: string | null): string =>
  (by ? `Название предложено ИИ (${by}) — нажмите, чтобы изменить` : AI_BADGE_HINT);

export function AiBadge({ onClick, by }: { onClick?: () => void; by?: string | null }) {
  // Бейдж бывает внутри кнопки записи в списке: вложенная <button> недопустима,
  // поэтому это span, а с клавиатуры переименовывают по F2.
  const click = (e: MouseEvent) => {
    if (!onClick) return;
    e.preventDefault();
    e.stopPropagation();
    onClick();
  };
  // aria-label у span без роли экранные дикторы не читают — текст для них скрыт визуально.
  return (
    <span className="ai-badge" title={aiBadgeHint(by)} onClick={click}>
      <span aria-hidden="true">ИИ</span>
      <span className="ai-badge__sr">{aiBadgeHint(by)}</span>
    </span>
  );
}
