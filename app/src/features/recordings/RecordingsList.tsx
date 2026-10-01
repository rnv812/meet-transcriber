import "./recordings.css";
import { useMemo, useState } from "react";
import type { Resident } from "../../state/useResident";
import type { Library } from "../../state/useLibrary";
import { deleteRecording, kbExport, patchRecording } from "../../lib/api";
import { errorText } from "../../lib/format";
import { inTauri, openFolder } from "../../lib/shell";
import { statusOf } from "../../lib/status";
import { EmptyState } from "../../ui/EmptyState";
import { ImportZone } from "./ImportZone";
import { RecordingItem, type ItemActions } from "./RecordingItem";
import { SearchBox } from "./SearchBox";

type Props = {
  selected: string | null;
  onSelect: (id: string) => void;
  library: Library;
  resident: Pick<Resident, "endpoint" | "snapshot">;
  /** Строка поиска живёт в App и уходит в useLibrary (задержка — там). */
  q: string;
  onQ: (q: string) => void;
  /** Фрагмент из поиска: открыть запись на этой реплике. */
  onOpenHit?: (id: string, t: number) => void;
  /** Запись изменили из списка (название, выгрузка): открытая карточка перечитывается. */
  onChanged?: (id: string) => void;
  /** Перед удалением: открытую карточку закрыть — её плеер держит файл записи. */
  onDeleting?: (id: string) => void;
};

/** Итог действия из меню: строка над списком, закрывается «×». */
type Notice = { text: string; error: boolean };

export function RecordingsList({
  selected, onSelect, library, resident, q, onQ, onOpenHit, onChanged, onDeleting,
}: Props) {
  const snapshot = resident.snapshot ?? null;
  const endpoint = resident.endpoint ?? null;
  const meetingsDir = snapshot?.meetings_dir ?? null;
  const [notice, setNotice] = useState<Notice | null>(null);

  const actions = useMemo<ItemActions | undefined>(() => {
    if (!endpoint) return undefined;
    const run = async (fn: () => Promise<string | null>) => {
      setNotice(null);
      try {
        const text = await fn();
        if (text) setNotice({ text, error: false });
      } catch (cause) {
        setNotice({ text: errorText(cause), error: true });
      }
    };
    return {
      onRename: (id, title) => run(async () => {
        await patchRecording(endpoint, id, { title });
        onChanged?.(id);
        await library.refresh();
        return null;
      }),
      onOpenFolder: inTauri() ? (rec) => void run(async () => { await openFolder(rec.path); return null; }) : undefined,
      onKbExport: meetingsDir ? (id) => void run(async () => {
        const done = await kbExport(endpoint, id);
        onChanged?.(id);
        return `Выгружено в базу знаний: ${done.path}`;
      }) : undefined,
      onDelete: (id) => void run(async () => {
        onDeleting?.(id);
        // Карточка закрывается в этом же кадре: её плеер отпускает файл до запроса.
        await new Promise((resolve) => setTimeout(resolve, 0));
        await deleteRecording(endpoint, id);
        await library.refresh();
        return null;
      }),
    };
  }, [endpoint, meetingsDir, library, onChanged, onDeleting]);

  return (
    <div className="rec-list">
      <ImportZone endpoint={endpoint} onImported={() => void library.refresh?.()} />
      <SearchBox value={q} onChange={onQ} />
      {library.error && <div className="import__error">{library.error}</div>}
      {notice && (
        <div className={`rec-notice${notice.error ? " rec-notice--error" : ""}`} role={notice.error ? "alert" : "status"}>
          <span className="rec-notice__text">{notice.text}</span>
          <button type="button" className="import__close" aria-label="Скрыть сообщение" onClick={() => setNotice(null)}>×</button>
        </div>
      )}
      <ul aria-label="Записи" className="rec-list__items">
        {library.items.map((rec) => (
          <RecordingItem
            key={rec.id}
            rec={rec}
            status={statusOf(rec, library.jobs, snapshot)}
            selected={rec.id === selected}
            onSelect={onSelect}
            onOpenHit={onOpenHit}
            actions={actions}
          />
        ))}
      </ul>
      {library.items.length === 0 && !library.loading && resident.endpoint && (
        q ? <EmptyState title="Ничего не найдено" />
          : <EmptyState title="Записей пока нет" hint="Нажмите «Начать запись» или перетащите файл" />
      )}
    </div>
  );
}
