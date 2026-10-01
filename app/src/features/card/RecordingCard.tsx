import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError, cancelJob, deleteRecording, exportRecording, getDiagnostics, getRecording, getSettings,
  kbExport, patchRecording, transcribe, type Endpoint,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { agentKillRecording, inTauri, openFolder, saveText } from "../../lib/shell";
import { mergeTurns, speakersOf, type Turn } from "../../lib/speakers";
import { activeJobOf, failedRetranscribe, isLiveRecording, statusOf } from "../../lib/status";
import type { Job, KbExport, Recording, Segment, Snapshot, Transcript } from "../../lib/types";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";
import { AudioPlayer, type AudioPlayerHandle } from "./AudioPlayer";
import { CardActions } from "./CardActions";
import { CardTabs } from "./CardTabs";
import { CardHeader } from "./CardHeader";
import { LiveCard } from "./LiveCard";
import { SpeakersPanel } from "./speakers/SpeakersPanel";
import { TranscriptView, type FindRequest } from "./TranscriptView";
import { useTurnEdit } from "./TurnEdit";
import type { PersonColor } from "./Turns";
import "./card.css";

type Loaded = Recording & { transcript: Transcript | null };

const NO_PEOPLE: PersonColor[] = [];
const NO_SEGMENTS: Segment[] = [];
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
  onOpenSettings, find, refreshKey = 0,
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
  /** Открыть с запросом в поиске по расшифровке (из поиска по записям). */
  find?: FindRequest | null;
  /** Растёт, когда запись изменили снаружи (переименовали в списке): перечитать. */
  refreshKey?: number;
}) {
  const [rec, setRec] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const player = useRef<AudioPlayerHandle>(null);
  const cardEl = useRef<HTMLElement>(null);
  /** Панель «Спикеры»: открыта ли, к какой строке перейти; `mounted` — уже открывали (правки живут скрытыми). */
  const [panel, setPanel] = useState<{ open: boolean; mounted: boolean; focus: { label: string; n: number } | null }>(
    { open: false, mounted: false, focus: null });
  /** «Показать все реплики» из панели: свой запрос к поиску по расшифровке. */
  const [ownFind, setOwnFind] = useState<FindRequest | null>(null);

  /** Папка для встреч в базе знаний (`export.meetings_dir`): нет — нет и кнопки «В базу знаний». */
  const [meetingsDir, setMeetingsDir] = useState<string | null>(null);
  /** Как подписан владелец микрофона (настройка) — «Это я» в меню реплики. */
  const [owner, setOwner] = useState("Вы");
  /** Куда выгружено нажатием «В базу знаний» (для этой записи) и что не перезаписано. */
  const [kbDone, setKbDone] = useState<KbExport | null>(null);
  /** Дорожка плеера не загрузилась: реплики не перематывают, внизу — «Аудио недоступно». */
  const [audioFailed, setAudioFailed] = useState(false);
  const current = useRef({ endpoint, id });
  current.current = { endpoint, id };

  useEffect(() => {
    let live = true;
    getSettings(endpoint).then((s) => {
      if (!live) return;
      const dir = (s.export as { meetings_dir?: unknown } | undefined)?.meetings_dir;
      setMeetingsDir(typeof dir === "string" && dir ? dir : null);
      const name = (s.recording as { speaker_name?: unknown } | undefined)?.speaker_name;
      if (typeof name === "string" && name.trim()) setOwner(name.trim());
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

  // Реплика перематывает общий плеер: он играет обе стороны звонка сразу,
  // поэтому дорожку по имени спикера выбирать не нужно.
  const play = useCallback((t: Turn) => player.current?.seek(t.start, true), []);
  const audioAvailable = useCallback((ok: boolean) => setAudioFailed(!ok), []);

  // Состояние задач этой записи: при смене (очередь, готово) карточку надо перечитать.
  const jobSig = useMemo(
    () => jobs.filter((j) => rec && norm(j.folder) === norm(rec.path)).map((j) => `${j.id}:${j.state}`).join(","),
    [jobs, rec],
  );

  useEffect(() => {
    setRec(null); setError(null); setMissing(false); setKbDone(null); setAudioFailed(false);
    setPanel({ open: false, mounted: false, focus: null }); setOwnFind(null);
    void load();
  }, [load]);
  // Просьба из поиска по записям важнее прежней своей.
  useEffect(() => { setOwnFind(null); }, [find]);
  const lastRefresh = useRef(refreshKey);
  useEffect(() => {
    if (refreshKey !== lastRefresh.current) { lastRefresh.current = refreshKey; void load(); }
  }, [refreshKey, load]);
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
  const openSpeakers = useCallback((label?: string) => setPanel((p) => ({
    open: true, mounted: true, focus: label ? { label, n: (p.focus?.n ?? 0) + 1 } : p.focus,
  })), []);
  const nameSpeaker = useCallback((label: string) => openSpeakers(label), [openSpeakers]);
  const closeSpeakers = useCallback(() => setPanel((p) => ({ ...p, open: false })), []);
  const playPhrase = useCallback((start: number, until: number) => player.current?.seek(start, true, until), []);
  const showTurns = useCallback((label: string) => setOwnFind((f) => ({
    q: `спикер:"${label}"`, t: null, n: Math.max(f?.n ?? 0, find?.n ?? 0) + 1,
  })), [find]);
  const speakersChanged = useCallback(() => {
    void load(); onChanged?.(); onPeopleChanged?.();
  }, [load, onChanged, onPeopleChanged]);
  const shownFind = ownFind ?? find;
  const colors = useMemo(() => new Map(people.map((p) => [p.name, p.color])), [people]);
  const turnEdit = useTurnEdit({
    endpoint, id, turns, segments: segments ?? NO_SEGMENTS, people, owner, avatarVersion,
    onOpenPanel: nameSpeaker, onChanged: speakersChanged,
  });

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
  const playable = hasAudio && !audioFailed;

  const rename = (title: string | null) => act(async () => {
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
    if (typeof dir !== "string" || !dir) throw new Error("Папка данных службы записи неизвестна");
    await openFolder(logsDir(dir));
  });
  const logsButton = inTauri() ? <Button onClick={openLogs} disabled={busy}>Открыть журнал</Button> : null;
  const cancelButton = active ? <Button onClick={doCancel} disabled={busy}>Отменить</Button> : null;
  const retranscribeFailed = status.kind === "ready" ? failedRetranscribe(rec, jobs) : null;
  const doDelete = () => act(async () => {
    // Плеер отпускает файл до запроса: резидент не удалит открытый playback.opus.
    player.current?.release();
    // Агент во вкладке «Агент» работает в папке записи — Windows не удалит её, пока он жив.
    await agentKillRecording(id);
    await deleteRecording(endpoint, id);
    onDeleted?.();
  });
  const doExport = (format: string) => act(async () => {
    const { filename, content } = await exportRecording(endpoint, id, format);
    await saveText(filename, content);
  });
  // Неудача остаётся и в meta.json записи (`kb_export.error`): перечитываем карточку в любом случае.
  const doKbExport = () => act(async () => {
    setKbDone(null);
    try {
      setKbDone(await kbExport(endpoint, id));
    } finally {
      await load();
    }
  });
  const kbError = rec.kb_export?.error;

  let body;
  switch (status.kind) {
    case "ready":
      body = (
        <CardTabs endpoint={endpoint} id={id} folder={rec.path} jobs={jobs} onOpenSettings={onOpenSettings}
          showTranscript={shownFind?.n}
          transcript={turns.length ? (
            <TranscriptView turns={turns} colors={colors} playable={playable} onPlay={play}
              onNameSpeaker={nameSpeaker} onSpeaker={turnEdit.onSpeaker} selected={turnEdit.selected}
              onSelect={turnEdit.onSelect} toolbar={turnEdit.bar} find={shownFind} />
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
    <section className={`card${panel.open && status.kind === "ready" ? " card--with-spk" : ""}`} ref={cardEl}>
      <CardHeader rec={rec} durationS={rec.duration_s ?? spokenUntil} speakers={speakers} people={people}
        endpoint={endpoint} avatarVersion={avatarVersion} onRename={rename} onNameSpeaker={nameSpeaker}
        onOpenSpeakers={status.kind === "ready" ? () => openSpeakers() : undefined} speakersOpen={panel.open} />
      <CardActions
        canExport={status.kind === "ready"}
        canRetranscribe={status.kind === "ready"}
        busy={busy}
        onExport={doExport}
        onKbExport={meetingsDir && status.kind === "ready" ? doKbExport : undefined}
        onOpenFolder={() => void openFolder(rec.path)}
        onRetranscribe={doTranscribe}
        onDelete={doDelete}
      />
      {error && <div className="card__error" role="alert">{error}</div>}
      {kbDone && (
        <div className="card__banner card__banner--ok" role="status" aria-label="Выгрузка в базу знаний">
          <span>
            Выгружено: <code className="card__path">{kbDone.path}</code>
            {(kbDone.kept ?? []).map((name) => (
              <span key={name} className="card__kept">{name} изменён вручную — не перезаписан</span>
            ))}
            {(kbDone.notes ?? []).map((line) => (
              <span key={line} className="card__kept">{line}</span>
            ))}
          </span>
          {inTauri() && <Button onClick={() => act(() => openFolder(kbDone.path))}>Открыть папку</Button>}
        </div>
      )}
      {!kbDone && !error && kbError && (
        <div className="card__banner" role="status">
          <span>Не удалось выгрузить в базу знаний: {kbError}</span>
        </div>
      )}
      {retranscribeFailed && (
        <div className="card__banner" role="status">
          <span>
            Перерасшифровка не удалась: {retranscribeFailed.error || "без подробностей"}
            <span className="muted"> · показана прежняя расшифровка</span>
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
      {status.kind === "ready" && turnEdit.menu}
      {panel.mounted && status.kind === "ready" && (
        <SpeakersPanel endpoint={endpoint} recordingId={id} people={people} avatarVersion={avatarVersion}
          open={panel.open} focus={panel.focus} version={rec.transcript} playable={playable} cardRef={cardEl} jobs={jobs}
          onClose={closeSpeakers} onPlay={playPhrase} onShowTurns={showTurns} onChanged={speakersChanged} />
      )}
      {status.kind !== "recording" && (hasAudio ? (
        <AudioPlayer key={id} ref={player} endpoint={endpoint} id={id} durationHint={rec.duration_s ?? spokenUntil}
          onAvailable={audioAvailable} />
      ) : (
        <div className="player player--off" role="status"><span className="muted">Аудио недоступно</span></div>
      ))}
    </section>
  );
}
