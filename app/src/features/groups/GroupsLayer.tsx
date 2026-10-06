/**
 * Слой групп поверх окна: окно названия группы, подтверждение «Убрать из
 * встреч», уведомление с «Отменить» и «тень» перетаскиваемого.
 */

import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { Toast, ToastAnnouncer } from "../../ui/Toast";
import { plural } from "../../lib/format";
import { ghostText } from "./drag";
import { GroupDialog } from "./GroupDialog";
import { useDragView } from "./GroupsNav";
import type { GroupsUi } from "./useGroupsUi";
import "./groups.css";

export function GroupsLayer({ ui }: { ui: GroupsUi }) {
  const toast = ui.toast;
  return (
    <>
      {ui.dialog && (
        <GroupDialog key={ui.dialog.mode + ("id" in ui.dialog ? ui.dialog.id : "")} state={ui.dialog}
          groups={ui.groups} onSubmit={ui.submitDialog} onClose={ui.closeDialog} />
      )}
      {ui.clearAsk && (
        <ConfirmDialog title="Убрать группу из встреч?" confirmLabel="Убрать"
          message={`«Группа без названия» уйдёт из ${ui.clearAsk.count} ${plural(ui.clearAsk.count, "встречи", "встреч",
            "встреч")}: они останутся в списке без группы. Это действие нельзя отменить.`}
          onCancel={() => ui.answerClear(false)} onConfirm={() => ui.answerClear(true)} />
      )}
      {/* Области для диктора — всегда в документе; видимое уведомление — отдельно. */}
      <ToastAnnouncer n={toast?.n ?? 0} text={toast?.text ?? null} error={toast?.error} />
      {toast && (
        <Toast key={toast.n} text={toast.text} error={toast.error} onClose={ui.closeToast} onHold={ui.holdToast}
          action={toast.undo ? {
            label: "Отменить",
            onClick: () => { const undo = toast.undo!; ui.closeToast(); void undo(); },
          } : undefined} />
      )}
      <DragGhost ui={ui} />
    </>
  );
}

/** Тень у указателя: «3 встречи», название встречи или имя группы. */
function DragGhost({ ui }: { ui: GroupsUi }) {
  const view = useDragView(ui);
  if (!view) return null;
  return (
    <div className={`drag-ghost${view.target ? " drag-ghost--over" : ""}`} aria-hidden="true"
      style={{ left: view.x + 16, top: view.y + 14 }}>
      {ghostText(view.payload)}
    </div>
  );
}
