import { useRef, useState } from "react";
import { type Endpoint, getRecording, nameSpeakers, saveTranscript } from "../../lib/api";
import { Avatar } from "../../ui/Avatar";
import { Button } from "../../ui/Button";
import type { PersonColor } from "./Turns";

const fold = (s: string) => s.trim().toLowerCase().replace(/ё/g, "е");
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

type Option = { name: string; existing: boolean; person?: PersonColor };

/** «Кто это?»: имя спикера из базы или новое; по умолчанию голос запоминается. */
export function SpeakerPopover({ endpoint, recordingId, label, people, onApplied, onDone }: {
  endpoint: Endpoint;
  recordingId: string;
  label: string;
  people: PersonColor[];
  onApplied?: () => void;
  onDone: () => void;
}) {
  const [text, setText] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const inflight = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const typed = text.trim();
  const q = fold(text);
  const options: Option[] = [];
  if (q) {
    for (const p of people) if (fold(p.name).startsWith(q)) options.push({ name: p.name, existing: true, person: p });
    if (!people.some((p) => fold(p.name) === q)) options.push({ name: typed, existing: false });
  }
  const ei = options.findIndex((o) => o.existing && fold(o.name) === q);
  if (ei > 0) options.unshift(...options.splice(ei, 1));
  const choice = options[0]?.name;
  const exact = options.find((o) => o.existing && fold(o.name) === q)?.name;

  const apply = async (name: string) => {
    if (!name.trim() || inflight.current) return;
    inflight.current = true;
    setBusy(true);
    setError(null);
    try {
      if (remember) {
        const r = await nameSpeakers(endpoint, recordingId, { [label]: name });
        onApplied?.();
        if (r.voices_error) { setWarning(r.voices_error); return; }
      } else {
        const rec = await getRecording(endpoint, recordingId);
        const t = rec.transcript;
        if (!t) throw new Error("У записи нет расшифровки");
        const segments = t.segments.map((s) => (s.speaker === label ? { ...s, speaker: name } : s));
        await saveTranscript(endpoint, recordingId, { ...t, segments });
        onApplied?.();
      }
      onDone();
    } catch (e) {
      setError(errText(e));
      setTimeout(() => input.current?.focus(), 0);
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  };

  if (warning) {
    return (
      <div className="speaker-pop">
        <div className="speaker-pop__title">Имя применено</div>
        <div className="speaker-pop__warn" role="status">Голос не сохранён: {warning}</div>
        <div className="speaker-pop__actions"><Button onClick={onDone}>Закрыть</Button></div>
      </div>
    );
  }

  return (
    <div className="speaker-pop">
      <div className="speaker-pop__title">Кто это?</div>
      <input
        className="speaker-pop__input"
        aria-label="Кто это?"
        autoFocus
        ref={input}
        value={text}
        disabled={busy}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (choice) void apply(choice);
          }
        }}
      />
      <div className="speaker-pop__list">
        {options.map((o) => (
          <button key={`${o.existing}:${o.name}`} type="button" className="speaker-pop__sug"
            onClick={() => void apply(o.name)}>
            {o.existing ? (
              <>
                <Avatar name={o.name} color={o.person?.color} hasAvatar={o.person?.has_avatar} size={16}
                  endpoint={endpoint} />
                <span>{o.name} <span className="muted">— уже в базе</span></span>
              </>
            ) : (
              <span>{`＋ Новый человек «${o.name}»`}</span>
            )}
          </button>
        ))}
      </div>
      <label className="speaker-pop__remember">
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
        Запомнить голос — узнавать дальше
      </label>
      {error && <div className="card__error speaker-pop__error" role="alert">{error}</div>}
      <div className="speaker-pop__actions">
        <Button variant="primary" disabled={!typed || busy} onClick={() => void apply(exact ?? typed)}>
          Готово
        </Button>
      </div>
    </div>
  );
}
