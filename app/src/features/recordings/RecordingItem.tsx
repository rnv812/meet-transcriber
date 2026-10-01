import { dayLabel, duration } from "../../lib/format";
import type { RecStatus } from "../../lib/status";
import type { Recording } from "../../lib/types";

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
}: {
  rec: Recording;
  status: RecStatus;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const when = rec.started_at ? dayLabel(rec.started_at) : "";
  const badge = badgeOf(status);
  const meta = [when, rec.duration_s ? duration(rec.duration_s) : ""].filter(Boolean).join(" · ");
  return (
    <div
      role="option"
      tabIndex={0}
      aria-selected={selected}
      className="rec-item"
      onClick={() => onSelect(rec.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(rec.id);
        }
      }}
    >
      <div className="rec-item__title">{rec.title ?? (when || rec.id)}</div>
      <div className="rec-item__meta">
        <span className="muted num">{meta}</span>
        {badge && <span className={`badge${badge.tone ? ` badge--${badge.tone}` : ""}`}>{badge.text}</span>}
      </div>
    </div>
  );
}
