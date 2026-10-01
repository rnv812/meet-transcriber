import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError, cancelJob, deleteRecording, exportRecording, getDiagnostics, getRecording, getSettings,
  patchRecording, transcribe, type Endpoint,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { inTauri, openFolder, saveText } from "../../lib/shell";
import { mergeTurns, speakersOf, type Turn } from "../../lib/speakers";
import { activeJobOf, failedRetranscribe, isLiveRecording, statusOf } from "../../lib/status";
import type { Job, Recording, Snapshot, Transcript } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Popover } from "../../ui/Popover";
import { EmptyState } from "../../ui/EmptyState";
import { AudioPlayer, type AudioPlayerHandle, type Track } from "./AudioPlayer";
import { CardActions } from "./CardActions";
import { CardTabs } from "./CardTabs";
import { CardHeader } from "./CardHeader";
import { LiveCard } from "./LiveCard";
import { SpeakerPopover } from "./SpeakerPopover";
import { Turns, type PersonColor } from "./Turns";
import "./card.css";

type Loaded = Recording & { transcript: Transcript | null };

const NO_PEOPLE: PersonColor[] = [];
/** Одна ссылка на «задач нет»: новая ссылка `jobs` для вкладок — это обновление списка. */
const NO_JOBS: Job[] = [];
/** `<data_dir>/logs` с разделителем, каким пишет путь сам резидент. */
function logsDir(dataDir: string): string {
  const sep = dataDir.includes("\\") ? "\\" : "/";
  return `${dataDir.replace(/[\\/]+$/, "")}${sep}logs`;
}
const norm = (p: string) => p.replace(/\\/g, "/").toLowerCase();

export function RecordingCard({
  id, endpoint, jobs = NO_JOBS, snapshot = null, people = NO_PEOPLE, avatarVersion, onDeleted, onChanged, onPeopleChanged,
  onOpenSettings,
}: {
  id: string;
  endpoint: Endpoint;
  jobs?: Job[];
  snapshot?: Snapshot | null;
  people?: PersonColor[];
  avatarVersion?: Record<string, number>;
  onDeleted?: () => void;
  onChanged?: () => void;
  onPeopleChanged?: () => void;
  /** Перейти в настройки; `section` — раздел, например "assistant". */
  onOpenSettings?: (section: string) => void;
}) {
  const [rec, setRec] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
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
      setMissing(false);
    } catch (e) {
      if (stale()) return;
      // 404 — запись удалили, а ссылка на неё осталась (уведомление, ?recording=).
      if (e instanceof ApiError && e.status === 404) setMissing(true);
      else setError(errorText(e));
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

  useEffect(() => { setRec(null); setError(null); setMissing(false); void load(); }, [load]);
  const lastSig = useRef(jobSig);
  useEffect(() => {
    if (jobSig !== lastSig.current) { lastSig.current = jobSig; void load(); }
  }, [jobSig, load]);

  const segments = rec?.transcript?.segments;
  // Импорт не знает длительность заранее: по транскрипту она известна точно.
  const spokenUntil = useMemo(
    () => (segments?.length ? segments.reduce((m, x) => Math.max(m, x.end), 0) : null), [segments]);
  const turns = useMemo(() => mergeTurns(segments ?? []), [segments]);
  const speakers = useMemo(() => speakersOf(segments ?? []), [segments]);
  const nameSpeaker = useCallback((label: string, anchor: HTMLElement) => setNaming({ label, anchor }), []);
  const closeNaming = useCallback(() => setNaming(null), []);
  const colors = useMemo(() => new Map(people.map((p) => [p.name, p.color])), [people]);

  tracksRef.current = rec?.tracks ?? {};

  if (!rec) {
    if (missing) return <EmptyState title="Запись не найдена" hint="Возможно, её удалили. Выберите другую в списке." />;
    return error ? <div className="card__error" role="alert">{error}</div> : <EmptyState title="Загрузка…" />;
  }

  const status = statusOf(rec, jobs, snapshot);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };

  const hasAudio = Object.keys(rec.tracks).length > 0;

  const rename = (title: string) => act(async () => {
    const updated = await patchRecording(endpoint, id, { title });
    setRec((cur) => (cur ? { ...cur, ...updated, transcript: cur.transcript } : cur));
    onChanged?.();
  });
  const doTranscribe = () => act(async () => { await transcribe(endpoint, id); onChanged?.(); await load(); });
  const active = activeJobOf(rec, jobs);
  const doCancel = () => act(async () => {
    if (!active) return;
    await cancelJob(endpoint, active.id);
    onChanged?.();
    await load();
  });
  const openLogs = () => act(async () => {
    const diag = await getDiagnostics(endpoint, 1);
    const dir = (diag.paths as { data_dir?: unknown } | undefined)?.data_dir;
    if (typeof dir !== "string" || !dir) throw new Error("Папка данных резидента неизвестна");
    await openFolder(logsDir(dir));
  });
  const logsButton = inTauri() ? <Button onClick={openLogs} disabled={busy}>Открыть журнал</Button> : null;
  const cancelButton = active ? <Button onClick={doCancel} disabled={busy}>Отменить</Button> : null;
  const retranscribeFailed = status.kind === "ready" ? failedRetranscribe(rec, jobs) : null;
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
      body = (
        <CardTabs endpoint={endpoint} id={id} folder={rec.path} jobs={jobs} onOpenSettings={onOpenSettings}
          transcript={turns.length ? (
            <Turns turns={turns} colors={colors} playable={hasAudio} onPlay={play} onNameSpeaker={nameSpeaker} />
          ) : <EmptyState title="В записи нет речи" />} />
      );
      break;
    case "untranscribed":
      body = <EmptyState title="Запись не расшифрована"
        action={<Button variant="primary" onClick={doTranscribe} disabled={busy}>Расшифровать</Button>} />;
      break;
    case "queued":
      body = <EmptyState title="В очереди на расшифровку" action={cancelButton} />;
      break;
    case "running": {
      const pct = status.total ? Math.round(((status.done ?? 0) / status.total) * 100) : null;
      body = (
        <div className="card__progress">
          <div>{status.label}{pct !== null ? ` ${pct}%` : "…"}</div>
          <div className="progress"><div className="progress__bar" style={{ width: `${pct ?? 100}%` }} /></div>
          {cancelButton && <div>{cancelButton}</div>}
        </div>
      );
      break;
    }
    case "failed":
      body = (
        <div className="card__failed">
          <div className="card__error">{status.error || "Расшифровка не удалась"}</div>
          <div className="card__row">
            <Button variant="primary" onClick={doTranscribe} disabled={busy}>Повторить</Button>
            {logsButton}
          </div>
        </div>
      );
      break;
    case "recording":
      body = snapshot?.live && isLiveRecording(rec, snapshot)
        ? <LiveCard endpoint={endpoint} live={snapshot.live} />
        : <EmptyState title="Идёт запись…" />;
      break;
  }

  return (
    <section className="card">
      <CardHeader rec={rec} durationS={rec.duration_s ?? spokenUntil} speakers={speakers} people={people}
        endpoint={endpoint} avatarVersion={avatarVersion} onRename={rename} onNameSpeaker={nameSpeaker} />
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
      {retranscribeFailed && (
        <div className="card__banner" role="status">
          <span>
            Перерасшифровка не удалась: {retranscribeFailed.error || "без подробностей"}
            <span className="muted"> · показан прежний транскрипт</span>
          </span>
          <span className="card__row">
            <Button onClick={doTranscribe} disabled={busy}>Повторить</Button>
            {logsButton}
          </span>
        </div>
      )}
      {status.kind === "ready" && rec.diarization?.startsWith("skipped_") && (
        // Без токена HF (или без доступа к модели) расшифровка идёт одним потоком.
        <div className="card__banner" role="status">
          <span>Без разделения на спикеров — настройте Hugging Face</span>
          {onOpenSettings && <Button onClick={() => onOpenSettings("engine")}>Настроить</Button>}
        </div>
      )}
      <div className="card__body">{body}</div>
      {naming && (
        <Popover anchor={naming.anchor} onClose={closeNaming} label="Кто это?">
          <SpeakerPopover
            endpoint={endpoint} recordingId={id} label={naming.label}
            people={people} avatarVersion={avatarVersion}
            onApplied={() => { void load(); onChanged?.(); onPeopleChanged?.(); }}
            onDone={closeNaming}
          />
        </Popover>
      )}
      {hasAudio && <AudioPlayer ref={player} endpoint={endpoint} id={id} />}
    </section>
  );
}
