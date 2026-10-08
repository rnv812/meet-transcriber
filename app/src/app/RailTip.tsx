import type { ReactNode } from "react";

/**
 * Подсказка справа от кнопки рейки (Aurora `.tip-wrap` + `.tooltip`): всплывает
 * при наведении и фокусе. По умолчанию повторяет имя кнопки и для экранного
 * диктора скрыта (имя уже прочитано). С `id` — это описание кнопки
 * (`aria-describedby` на ней): «Идёт запись · мм:сс», с какого устройства пишем.
 */
export function RailTip({ tip, id, children }: { tip: ReactNode; id?: string; children: ReactNode }) {
  return (
    <span className="tip-wrap rail__tip">
      {children}
      <span className="tooltip" id={id} role={id ? "tooltip" : undefined} aria-hidden={id ? undefined : true}>{tip}</span>
    </span>
  );
}
