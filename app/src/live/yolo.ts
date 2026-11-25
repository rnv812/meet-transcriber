/**
 * Режим YOLO встречи (0.6.2): ассистент действует без карточек согласия — в
 * том числе по услышанному на встрече — до конца встречи или до выключения.
 * По умолчанию выключен на каждой встрече (в настройки не пишется);
 * включается в окне ассистента с подтверждением. Тексты — общие для строки
 * сессии и меню «Ещё» панели.
 */

import type { ConfirmOptions } from "../ui/ConfirmDialog";
import type { AgentInfo } from "../lib/types";

export const YOLO_BADGE = "YOLO";
export const YOLO_ON_LABEL = "Включить YOLO";
export const YOLO_OFF_LABEL = "Выключить YOLO";
/** «Что я знаю» → «Действия:». */
export const YOLO_ACTIONS = "YOLO — без вопросов";
export const YOLO_TITLE = "YOLO включён: ассистент сам удаляет, отправляет и меняет без вопросов, в том числе по "
  + "услышанному на встрече, — до конца встречи. Щёлкните, чтобы выключить";
export const YOLO_OFFER = "YOLO: ассистент будет действовать без вопросов до конца встречи";

/** Вопрос перед включением: что именно ассистент станет делать сам. */
export const YOLO_CONFIRM: ConfirmOptions = {
  title: "Включить YOLO до конца встречи?",
  message: "Ассистент будет сам удалять, отправлять наружу и менять файлы, задачи и программы — без карточек "
    + "и вопросов, в том числе по услышанному на встрече, без вашей просьбы. Закрытые папки и пароли "
    + "останутся закрыты. Выключить можно в любой момент.",
  confirmLabel: YOLO_ON_LABEL,
  cancelLabel: "Не включать",
  danger: true,
};

/** YOLO можно переключать: включён (выключить можно всегда) или доступен. */
export function yoloSwitchable(agent: AgentInfo | null | undefined): boolean {
  return !!agent && (agent.yolo === true || agent.yolo_available === true);
}
