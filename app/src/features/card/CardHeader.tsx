import { useRef, useState } from "react";
import type { Endpoint } from "../../lib/api";
import { dayLabel, duration } from "../../lib/format";
import { isUnnamed } from "../../lib/speakers";
import type { Recording } from "../../lib/types";
import { Avatar } from "../../ui/Avatar";
import type { PersonColor } from "./Turns";

export function CardHeader({
  rec, speakers, people, endpoint, onRename, onNameSpeaker,
}: {
  rec: Recording;
  speakers: string[];
  people: PersonColor[];
  endpoint: Endpoint;
  onRename: (title: string) => void;
  onNameSpeaker?: (label: string) => void;
}) {
  const when = rec.started_at ? dayLabel(rec.started_at) : "";
  const shown = rec.title ?? (when || rec.id);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const cancelled = useRef(false);

  const begin = () => { cancelled.current = false; setDraft(rec.title ?? ""); setEditing(true); };
  const finish = () => {
    if (cancelled.current) return;
    setEditing(false);
    const v = draft.trim();
    if (v && v !== (rec.title ?? "")) onRename(v);
  };
  const meta = [when, rec.duration_s ? duration(rec.duration_s) : ""].filter(Boolean).join(" · ");

  return (
    <header className="card__header">
      {editing ? (
        <input
          className="card__title-input"
          aria-label="Название записи"
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={finish}
          onKeyDown={(e) => {
            if (e.key === "Enter") finish();
            if (e.key === "Escape") { cancelled.current = true; setEditing(false); }
          }}
        />
      ) : (
        <h2 className="card__title" title="Нажмите, чтобы переименовать" onClick={begin}>{shown}</h2>
      )}
      <div className="card__meta muted num">{meta}</div>
      {speakers.length > 0 && (
        <div className="card__people">
          {speakers.map((name) => {
            const p = people.find((x) => x.name === name);
            const chip = (
              <>
                <Avatar name={name} color={p?.color} hasAvatar={p?.has_avatar} size={20} endpoint={endpoint} />
                <span>{name}</span>
              </>
            );
            return isUnnamed(name) ? (
              <button key={name} type="button" className="chip chip--unnamed"
                onClick={() => onNameSpeaker?.(name)}>{chip}</button>
            ) : (
              <span key={name} className="chip">{chip}</span>
            );
          })}
        </div>
      )}
    </header>
  );
}
