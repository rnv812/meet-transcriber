import { clock, dayLabel, duration } from "../../lib/format";
import type { RecStatus } from "../../lib/status";
import type { LibraryItem } from "../../lib/types";
import { Highlight } from "../../ui/Highlight";

/** Текст и вид бейджа; у готовой записи бейджа нет. */
export function badgeOf(st: RecStatus): { text: string; tone: "run" | "err" | "" } | null {
  switch (st.kind) {
    case "recording":
      return { text: "Идёт запись", tone: "err" };
    case "queued":
      return { text: "В очереди", tone: "" };
    case "running": {
      const pct = st.total ? ` ${Math.round(((st.done ?? 0) / st.total) * 100)}%` : "…";
      return { text: `${st.label}${pct}`, tone: "run" };
    }
    case "failed":
      return { text: "Ошибка", tone: "err" };
    case "untranscribed":
      return { text: "Не расшифровано", tone: "" };
    case "ready":
      return null;
  }
}

export function RecordingItem({
  rec,
  status,
  selected,
  onSelect,
  onOpenHit,
}: {
  rec: LibraryItem;
  status: RecStatus;
  selected: boolean;
  onSelect: (id: string) => void;
  /** Фрагмент из поиска: открыть запись на этой реплике. */
  onOpenHit?: (id: string, t: number) => void;
}) {
  const when = rec.started_at ? dayLabel(rec.started_at) : "";
  const badge = badgeOf(status);
  const meta = [when, rec.duration_s ? duration(rec.duration_s) : ""].filter(Boolean).join(" · ");
  const title = rec.title ?? (when || rec.id);
  const hits = rec.hits ?? [];
  const more = (rec.total ?? 0) - hits.length;
  return (
    <li className={`rec-item${selected ? " rec-item--selected" : ""}`}>
      <button type="button" className="rec-item__main" aria-current={selected ? "true" : undefined}
        onClick={() => onSelect(rec.id)}>
        <span className="rec-item__title">{title}</span>
        <span className="rec-item__meta">
          <span className="muted num">{meta}</span>
          {badge && <span className={`badge${badge.tone ? ` badge--${badge.tone}` : ""}`}>{badge.text}</span>}
        </span>
      </button>
      {hits.length > 0 && (
        <ul className="rec-hits" aria-label={`Найдено в записи «${title}»`}>
          {hits.map((h, i) => (
            <li key={i}>
              <button type="button" className="rec-hit" onClick={() => onOpenHit?.(rec.id, h.t)}>
                <span className="rec-hit__time num">{clock(h.t)}</span>
                <span className="rec-hit__text">
                  <span className="rec-hit__who">{h.speaker}: </span>
                  <Highlight text={h.snippet} ranges={h.ranges} />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {more > 0 && <div className="rec-hits__more muted">Ещё совпадений: {more}</div>}
    </li>
  );
}
