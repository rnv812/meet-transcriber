/**
 * «Остановить без сохранения» и «Временная встреча с ассистентом»: подписи и
 * вопросы — одни на окно, плавающую панель ассистента и панель строки меню
 * (у меню значка в оболочке — те же тексты, `tray.rs`).
 *
 * Оба вопроса — `ConfirmDialog`: фокус сразу на безопасной кнопке («Продолжить запись» /
 * «Продолжить»), Esc — тоже она. Безопасная кнопка одна везде — в окне, панели
 * ассистента, панели строки меню и меню значка.
 */

import type { ConfirmOptions } from "../ui/ConfirmDialog";

export const DISCARD_LABEL = "Остановить без сохранения";
export const DISCARD_CONFIRM: ConfirmOptions = {
  title: "Остановить без сохранения?",
  message: "Запись и всё, что с ней связано, будут удалены без возможности восстановления.",
  confirmLabel: "Удалить запись",
  cancelLabel: "Продолжить запись",
  danger: true,
};

/**
 * Вопрос «Остановить без сохранения?» для этой записи: если историю ассистента в
 * каком-то CLI удалить нечем (`snapshot.forget_gaps` — агент вкладки «Агент» без
 * известного id), так и сказано.
 */
export function discardConfirm(gaps: readonly string[] | null | undefined): ConfirmOptions {
  if (!gaps?.length) return DISCARD_CONFIRM;
  const notes = gaps.map((name) => `История ассистента в ${name} останется в самом ${name}.`);
  return { ...DISCARD_CONFIRM, message: [DISCARD_CONFIRM.message, ...notes].join(" ") };
}

export const TEMP_LABEL = "Временная встреча с ассистентом";
export const TEMP_NOTE = "не сохранится: ни записи, ни расшифровки — только разговор с ассистентом";
export const TEMP_BADGE = "Временная — не сохранится";
export const KEEP_LABEL = "Сохранить как обычную встречу";
export const TEMP_STOP_LABEL = "Закончить временную встречу";
/** Лёгкий вопрос: встреча и так временная, «Закончить» — не красная. */
export const TEMP_END_CONFIRM: ConfirmOptions = {
  title: "Временная встреча закончится и будет удалена.",
  confirmLabel: "Закончить",
  cancelLabel: "Продолжить",
  danger: false,
};

/** Запись остановлена без сохранения или временная встреча удалена — не «сохранена». */
export const notSaved = (reason: string | null | undefined) => reason === "discarded" || reason === "temporary";
