import { useEffect, useRef, useState } from "react";
import {
  audioUrl, deleteAvatar, deletePerson, getPerson, getSample, mergePerson, putAvatar, renamePerson,
  type Endpoint,
} from "../../lib/api";
import { dayLabel, duration } from "../../lib/format";
import type { Person, PersonCard as PersonData } from "../../lib/types";
import { Button } from "../../ui/Button";
import { AvatarEditor, pastedImage } from "./AvatarEditor";

type Props = {
  endpoint: Endpoint;
  person: Person;
  others: Person[];
  version?: number;
  onAvatar: () => void;
  onRenamed: (to: string) => void;
  onRemoved: (next: string | null) => void;
  onOpenRecording: (id: string) => void;
};

const MAX_AVATAR = 10 * 1024 * 1024;
const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function PersonCard({
  endpoint, person, others, version, onAvatar, onRenamed, onRemoved, onOpenRecording,
}: Props) {
  const name = person.name;
  const [data, setData] = useState<PersonData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState(name);
  const [confirm, setConfirm] = useState<null | "delete" | { merge: string }>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const stopAt = useRef<number | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const cancelled = useRef(false);

  useEffect(() => root.current?.focus(), []);

  useEffect(() => {
    let live = true;
    getPerson(endpoint, name).then((d) => live && setData(d), (e) => live && setError(msg(e)));
    return () => { live = false; };
  }, [endpoint, name]);

  async function run(fn: () => Promise<void>) {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(msg(e));
    }
  }

  const upload = (blob: Blob) => run(async () => {
    if (blob.size > MAX_AVATAR) throw new Error("Файл больше 10 МБ");
    await putAvatar(endpoint, name, blob);
    onAvatar();
  });
  const reset = () => run(async () => {
    await deleteAvatar(endpoint, name);
    onAvatar();
  });
  const rename = () => {
    if (cancelled.current) { cancelled.current = false; setDraft(name); return; }
    const to = draft.trim();
    if (!to || to === name) { setDraft(name); return; }
    void run(async () => {
      await renamePerson(endpoint, name, to);
      onRenamed(to);
    });
  };
  const play = () => run(async () => {
    const s = await getSample(endpoint, name);
    const a = audio.current;
    if (!a) return;
    stopAt.current = s.end;
    a.src = `${audioUrl(endpoint, s.recording, s.track as "sys" | "mic" | "source")}#t=${s.start},${s.end}`;
    a.load?.();
    void a.play?.()?.catch?.(() => {});
  });
  const doConfirmed = () => {
    const c = confirm;
    setConfirm(null);
    if (!c) return;
    void run(async () => {
      if (c === "delete") {
        await deletePerson(endpoint, name);
        onRemoved(null);
      } else {
        await mergePerson(endpoint, name, c.merge);
        onRemoved(c.merge);
      }
    });
  };

  return (
    <div
      className="pcard"
      ref={root}
      tabIndex={0}
      onPaste={(e) => {
        const f = pastedImage(e);
        if (f) { e.preventDefault(); void upload(f); }
      }}
    >
      <div className="pcard__head">
        <AvatarEditor
          endpoint={endpoint}
          person={person}
          hasAvatar={person.has_avatar}
          version={version}
          onUpload={(b) => void upload(b)}
          onReset={() => void reset()}
          onError={setError}
        />
        <input
          className="pcard__name"
          aria-label="Имя"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={rename}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") { cancelled.current = true; e.currentTarget.blur(); }
          }}
        />
      </div>
      {error && <div className="card__error" role="alert">{error}</div>}

      <div className="pcard__row">
        <Button onClick={() => void play()}>▶ Прослушать образец</Button>
      </div>
      <audio
        ref={audio}
        className="pcard__audio"
        onTimeUpdate={(e) => {
          const a = e.currentTarget;
          if (stopAt.current !== null && a.currentTime >= stopAt.current) {
            a.pause();
            stopAt.current = null;
          }
        }}
      />

      <h3 className="pcard__h">Встречи</h3>
      <ul className="pcard__meetings">
        {data?.meetings.map((m) => (
          <li key={m.recording}>
            <button type="button" className="pcard__meeting" onClick={() => onOpenRecording(m.recording)}>
              <span>{m.title || m.recording}</span>
              <span className="muted">
                {m.started_at ? `${dayLabel(m.started_at)} · ` : ""}{duration(m.seconds)}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <div className="pcard__row">
        {others.length > 0 && (
          <select
            aria-label="Объединить с…"
            className="pcard__select"
            value=""
            onChange={(e) => e.target.value && setConfirm({ merge: e.target.value })}
          >
            <option value="">Объединить с…</option>
            {others.map((o) => <option key={o.name} value={o.name}>{o.name}</option>)}
          </select>
        )}
        <Button variant="danger" onClick={() => setConfirm("delete")}>Удалить голос</Button>
      </div>
      {confirm && (
        <div className="confirm" role="alertdialog">
          <span>
            {confirm === "delete"
              ? `Удалить голос «${name}»?`
              : `Объединить «${name}» с «${confirm.merge}»? «${name}» исчезнет.`}
          </span>
          <Button variant="danger" onClick={doConfirmed}>Да</Button>
          <Button onClick={() => setConfirm(null)}>Отмена</Button>
        </div>
      )}
    </div>
  );
}
