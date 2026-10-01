import "./recordings.css";
import type { Resident } from "../../state/useResident";
import type { Library } from "../../state/useLibrary";
import { statusOf } from "../../lib/status";
import { EmptyState } from "../../ui/EmptyState";
import { ImportZone } from "./ImportZone";
import { RecordingItem } from "./RecordingItem";
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
};

export function RecordingsList({ selected, onSelect, library, resident, q, onQ, onOpenHit }: Props) {
  const snapshot = resident.snapshot ?? null;
  return (
    <div className="rec-list">
      <ImportZone endpoint={resident.endpoint ?? null} onImported={() => void library.refresh?.()} />
      <SearchBox value={q} onChange={onQ} />
      {library.error && <div className="import__error">{library.error}</div>}
      <ul aria-label="Записи" className="rec-list__items">
        {library.items.map((rec) => (
          <RecordingItem
            key={rec.id}
            rec={rec}
            status={statusOf(rec, library.jobs, snapshot)}
            selected={rec.id === selected}
            onSelect={onSelect}
            onOpenHit={onOpenHit}
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
