import { useRef, useState } from "react";
import { TITLE_MAX, type Endpoint } from "../../lib/api";
import { dayLabel, duration, plural } from "../../lib/format";
import { isUnnamed } from "../../lib/speakers";
import type { MergeInfo, Recording } from "../../lib/types";
import { Avatar } from "../../ui/Avatar";
import type { PersonColor } from "./Turns";

/** Подпись объединённой встречи: из скольких записей и что стало с исходными. */
function MergeNote({ info }: { info: MergeInfo }) {
  const parts = `Объединена из ${info.parts} ${plural(info.parts, "записи", "записей", "записей")}`;
  const step = info.state === "pending" ? "собирается звук"
    : info.state === "done" && info.deleted ? "исходные записи удалены" : "";
  return (
    <div className="card__merge muted">
      <div>{[parts, step].filter(Boolean).join(" · ")}</div>
      {info.state === "done" && info.kb_left.length > 0 && (
        <div>
          Прежние папки частей в базе знаний не изменены — удалите их, если они больше не нужны:{" "}
          {info.kb_left.map((p) => <code key={p} className="path">{p}</code>)}
        </div>
      )}
    </div>
  );
}

export function CardHeader({
  rec, durationS = rec.duration_s, speakers, people, endpoint, avatarVersion, onRename, onNameSpeaker,
}: {
  rec: Recording;
  /** Длительность для подписи: у импорта без неё — конец последней реплики. */
  durationS?: number | null;
  speakers: string[];
  people: PersonColor[];
  endpoint: Endpoint;
  avatarVersion?: Record<string, number>;
  /** Новое название; null — вернуть автоматическое. */
  onRename: (title: string | null) => void;
  onNameSpeaker?: (label: string, anchor: HTMLElement) => void;
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
    const v = draft.trim().slice(0, TITLE_MAX);
    if (v !== (rec.title ?? "")) onRename(v || null);
  };
  const meta = [when, durationS ? duration(durationS) : ""].filter(Boolean).join(" · ");

  return (
    <header className="card__header">
      {editing ? (
        <input
          className="card__title-input"
          aria-label="Название записи"
          autoFocus
          maxLength={TITLE_MAX}
          placeholder={when || "Название"}
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
      {rec.merge && <MergeNote info={rec.merge} />}
      {speakers.length > 0 && (
        <div className="card__people">
          {speakers.map((name) => {
            const p = people.find((x) => x.name === name);
            const chip = (
              <>
                <Avatar name={name} color={p?.color} hasAvatar={p?.has_avatar} version={avatarVersion?.[name]} size={20} endpoint={endpoint} />
                <span>{name}</span>
              </>
            );
            // Узнанное автоматически имя тоже бывает ошибочным: исправить можно любое.
            return (
              <button key={name} type="button" className={isUnnamed(name) ? "chip chip--unnamed" : "chip"}
                title="Кто это?" onClick={(e) => onNameSpeaker?.(name, e.currentTarget)}>{chip}</button>
            );
          })}
        </div>
      )}
    </header>
  );
}
