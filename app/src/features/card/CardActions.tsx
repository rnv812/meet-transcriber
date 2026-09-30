import { useState } from "react";
import { inTauri } from "../../lib/shell";
import { Button } from "../../ui/Button";

const FORMATS = ["md", "txt", "srt"] as const;

export function CardActions({
  canExport, canRetranscribe, busy, onExport, onOpenFolder, onRetranscribe, onDelete,
}: {
  canExport: boolean;
  canRetranscribe: boolean;
  busy: boolean;
  onExport: (format: string) => void;
  onOpenFolder: () => void;
  onRetranscribe: () => void;
  onDelete: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const [confirm, setConfirm] = useState(false);

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
      {inTauri() && <Button onClick={onOpenFolder}>Открыть папку</Button>}
      {canRetranscribe && <Button onClick={onRetranscribe} disabled={busy}>Перерасшифровать</Button>}
      {confirm ? (
        <span className="confirm">
          <span>Удалить запись и транскрипт? Это необратимо.</span>
          <Button variant="danger" onClick={() => { setConfirm(false); onDelete(); }}>Удалить</Button>
          <Button onClick={() => setConfirm(false)}>Отмена</Button>
        </span>
      ) : (
        <Button variant="danger" onClick={() => setConfirm(true)}>Удалить</Button>
      )}
    </div>
  );
}
