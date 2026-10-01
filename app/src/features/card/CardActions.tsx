import { useState } from "react";
import { inTauri } from "../../lib/shell";
import { Button } from "../../ui/Button";

const FORMATS = ["md", "txt", "srt"] as const;

export function CardActions({
  canExport, canRetranscribe, busy, onExport, onKbExport, onOpenFolder, onRetranscribe, onRediarize, onDelete,
}: {
  canExport: boolean;
  canRetranscribe: boolean;
  busy: boolean;
  onExport: (format: string) => void;
  /** «В базу знаний»; нет — кнопки нет (папка для встреч не задана или нечего выгружать). */
  onKbExport?: () => void;
  onOpenFolder: () => void;
  onRetranscribe: () => void;
  /** «Переразделить на спикеров…» (только разделение, без распознавания); нет — кнопки нет. */
  onRediarize?: () => void;
  onDelete: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [confirmRe, setConfirmRe] = useState(false);

  return (
    <div className="card__actions">
      {canExport && (
        <span className="menu-wrap">
          <Button onClick={() => setMenu((v) => !v)} aria-haspopup="menu" aria-expanded={menu}>Экспорт ▾</Button>
          {menu && (
            <div className="menu" role="menu">
              {FORMATS.map((f) => (
                <button key={f} type="button" role="menuitem" className="menu__item"
                  onClick={() => { setMenu(false); onExport(f); }}>{f}</button>
              ))}
            </div>
          )}
        </span>
      )}
      {onKbExport && <Button onClick={onKbExport} disabled={busy}>В базу знаний</Button>}
      {inTauri() && <Button onClick={onOpenFolder}>Открыть папку</Button>}
      {onRediarize && (
        <Button onClick={onRediarize} disabled={busy}
          title="Заново определить, кто говорит, не распознавая речь повторно">Переразделить на спикеров…</Button>
      )}
      {canRetranscribe && (confirmRe ? (
        <span className="confirm">
          <span>Расшифровка будет создана заново: ручные правки и имена, не сохранённые в базе голосов, будут потеряны. Продолжить?</span>
          <Button variant="primary" disabled={busy}
            onClick={() => { setConfirmRe(false); onRetranscribe(); }}>Перерасшифровать</Button>
          <Button onClick={() => setConfirmRe(false)}>Отмена</Button>
        </span>
      ) : (
        <Button onClick={() => setConfirmRe(true)} disabled={busy}>Перерасшифровать</Button>
      ))}
      {confirm ? (
        <span className="confirm">
          <span>Удалить запись и расшифровку? Это действие нельзя отменить.</span>
          <Button variant="danger" onClick={() => { setConfirm(false); onDelete(); }}>Удалить</Button>
          <Button onClick={() => setConfirm(false)}>Отмена</Button>
        </span>
      ) : (
        <Button variant="danger" onClick={() => setConfirm(true)}>Удалить</Button>
      )}
    </div>
  );
}
