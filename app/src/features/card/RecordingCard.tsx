import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  deleteRecording, exportRecording, getRecording, getSettings, patchRecording, transcribe,
  type Endpoint,
} from "../../lib/api";
import { openFolder, saveText } from "../../lib/shell";
import { mergeTurns, speakersOf, type Turn } from "../../lib/speakers";
import { statusOf } from "../../lib/status";
import type { Job, Recording, Snapshot, Transcript } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Popover } from "../../ui/Popover";
import { EmptyState } from "../../ui/EmptyState";
import { AudioPlayer, type AudioPlayerHandle, type Track } from "./AudioPlayer";
import { CardActions } from "./CardActions";
import { CardHeader } from "./CardHeader";
import { SpeakerPopover } from "./SpeakerPopover";
import { Turns, type PersonColor } from "./Turns";
import "./card.css";

type Loaded = Recording & { transcript: Transcript | null };

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const NO_PEOPLE: PersonColor[] = [];
const norm = (p: string) => p.replace(/\\/g, "/").toLowerCase();

export function RecordingCard({
  id, endpoint, jobs = [], snapshot = null, people = NO_PEOPLE, onDeleted, onChanged, onPeopleChanged,
}: {
  id: string;
  endpoint: Endpoint;
  jobs?: Job[];
  snapshot?: Snapshot | null;
  people?: PersonColor[];
  onDeleted?: () => void;
  onChanged?: () => void;
  onPeopleChanged?: () => void;
}) {
  const [rec, setRec] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [naming, setNaming] = useState<{ label: string; anchor: HTMLElement } | null>(null);
  const player = useRef<AudioPlayerHandle>(null);

  const [owner, setOwner] = useState("Вы");
  const current = useRef({ endpoint, id });
  current.current = { endpoint, id };
  const tracksRef = useRef<Record<string, string>>({});

  useEffect(() => {
    let live = true;
    getSettings(endpoint).then((s) => {
      const name = (s.recording as { speaker_name?: unknown } | undefined)?.speaker_name;
      if (live && typeof name === "string" && name) setOwner(name);
    }).catch(() => {});
    return () => { live = false; };
  }, [endpoint]);

  const load = useCallback(async () => {
    const stale = () => current.current.id !== id || current.current.endpoint !== endpoint;
    try {
      const data = await getRecording(endpoint, id);
      if (stale()) return;
      setRec(data);
      setError(null);
    } catch (e) {
      if (!stale()) setError(errText(e));
    }
  }, [endpoint, id]);

  const play = useCallback((t: Turn) => {
    const tr = tracksRef.current;
    const track: Track = tr.mic && t.speaker === owner ? "mic"
      : tr.source ? "source" : tr.sys ? "sys" : "mic";
    player.current?.play(track, t.start);
  }, [owner]);

  // Состояние задач этой записи: при смене (очередь, готово) карточку надо перечитать.
  const jobSig = useMemo(
    () => jobs.filter((j) => rec && norm(j.folder) === norm(rec.path)).map((j) => `${j.id}:${j.state}`).join(","),
    [jobs, rec],
  );

  useEffect(() => { setRec(null); setError(null); void load(); }, [load]);
  const lastSig = useRef(jobSig);
  useEffect(() => {
    if (jobSig !== lastSig.current) { lastSig.current = jobSig; void load(); }
  }, [jobSig, load]);

  const segments = rec?.transcript?.segments;
  const turns = useMemo(() => mergeTurns(segments ?? []), [segments]);
  const speakers = useMemo(() => speakersOf(segments ?? []), [segments]);
  const nameSpeaker = useCallback((label: string, anchor: HTMLElement) => setNaming({ label, anchor }), []);
  const closeNaming = useCallback(() => setNaming(null), []);
  const colors = useMemo(() => new Map(people.map((p) => [p.name, p.color])), [people]);

  tracksRef.current = rec?.tracks ?? {};

  if (!rec) {
    return error ? <div className="card__error" role="alert">{error}</div> : <EmptyState title="Загрузка…" />;
  }

  const status = statusOf(rec, jobs, snapshot);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  };

  const hasAudio = Object.keys(rec.tracks).length > 0;

  const rename = (title: string) => act(async () => {
    const updated = await patchRecording(endpoint, id, { title });
    setRec((cur) => (cur ? { ...cur, ...updated, transcript: cur.transcript } : cur));
    onChanged?.();
  });
  const doTranscribe = () => act(async () => { await transcribe(endpoint, id); onChanged?.(); await load(); });
  const doDelete = () => act(async () => {
    await deleteRecording(endpoint, id);
    onDeleted?.();
  });
  const doExport = (format: string) => act(async () => {
    const { filename, content } = await exportRecording(endpoint, id, format);
    await saveText(filename, content);
  });

  let body;
  switch (status.kind) {
    case "ready":
      body = turns.length ? (
        <Turns turns={turns} colors={colors} playable={hasAudio} onPlay={play} onNameSpeaker={nameSpeaker} />
      ) : <EmptyState title="В записи нет речи" />;
      break;
    case "untranscribed":
      body = <EmptyState title="Запись не расшифрована"
        action={<Button variant="primary" onClick={doTranscribe} disabled={busy}>Расшифровать</Button>} />;
      break;
    case "queued":
      body = <EmptyState title="В очереди на расшифровку" />;
      break;
    case "running": {
      const pct = status.total ? Math.round(((status.done ?? 0) / status.total) * 100) : null;
      body = (
        <div className="card__progress">
          <div>{status.label}{pct !== null ? ` ${pct}%` : "…"}</div>
          <div className="progress"><div className="progress__bar" style={{ width: `${pct ?? 100}%` }} /></div>
        </div>
      );
      break;
    }
    case "importing":
      body = <EmptyState title="Копирование…" />;
      break;
    case "failed":
      body = (
        <div className="card__failed">
          <div className="card__error">{status.error || "Расшифровка не удалась"}</div>
          <Button variant="primary" onClick={doTranscribe} disabled={busy}>Повторить</Button>
        </div>
      );
      break;
    case "recording":
      body = <EmptyState title="Идёт запись…" />;
      break;
  }

  return (
    <section className="card">
      <CardHeader rec={rec} speakers={speakers} people={people} endpoint={endpoint}
        onRename={rename} onNameSpeaker={nameSpeaker} />
      <CardActions
        canExport={status.kind === "ready"}
        canRetranscribe={status.kind === "ready"}
        busy={busy}
        onExport={doExport}
        onOpenFolder={() => void openFolder(rec.path)}
        onRetranscribe={doTranscribe}
        onDelete={doDelete}
      />
      {error && <div className="card__error" role="alert">{error}</div>}
      <div className="card__body">{body}</div>
      {naming && (
        <Popover anchor={naming.anchor} onClose={closeNaming} label="Кто это?">
          <SpeakerPopover
            endpoint={endpoint} recordingId={id} label={naming.label}
            people={people}
            onApplied={() => { void load(); onChanged?.(); onPeopleChanged?.(); }}
            onDone={closeNaming}
          />
        </Popover>
      )}
      {hasAudio && <AudioPlayer ref={player} endpoint={endpoint} id={id} />}
    </section>
  );
}
