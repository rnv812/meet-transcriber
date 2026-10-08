/**
 * Страница «Идёт запись» во вкладке «Запись» карточки (макет MeetApp, «идёт
 * запись»): обычная запись — своя или автозапись — без ассистента; запись с
 * ассистентом показывает `LiveCard`.
 *
 * Сверху — постоянное состояние: «Идёт запись · автозапись / вручную»,
 * большой таймер (`elapsed_s` из снимка, между снимками часы идут сами, как у
 * кнопки в рейке), «Началась в чч:мм». Временная встреча — пометка «не
 * сохранится»; ассистент, которого позвали, пока запускается — его этап.
 *
 * Предупреждения записи (мало места, подмена устройства, системный звук
 * macOS) — выносками; в рейке они же значками. Уровни микрофона и собеседников —
 * из `snapshot.levels`: пик дорожки с прошлого опроса, доля 0…1
 * (`recorder.take_level`), не децибелы — поэтому без подписей в дБ.
 *
 * Действия — те же, что у кнопки записи в рейке и её меню: «Остановить и
 * сохранить», «Остановить без сохранения…» (тот же вопрос, lib/recordingStop),
 * у временной — «Закончить временную встречу» и «Сохранить как обычную
 * встречу». Карточка «Позвать ассистента» — тот же `/live/attach` (профиль —
 * по умолчанию из настроек), только пока ассистента нет.
 */

import { useEffect, useId, useState } from "react";

import { ATTACH_FAILED, fallbackText, liveOf, LOW_DISK_GB, startingText } from "../../app/RecordingBadge";
import { type Endpoint, liveAttach, recordingCommand } from "../../lib/api";
import { clock, errorText } from "../../lib/format";
import {
  DISCARD_LABEL, discardConfirm, KEEP_LABEL, TEMP_BADGE, TEMP_END_CONFIRM, TEMP_NOTE, TEMP_STOP_LABEL,
} from "../../lib/recordingStop";
import { openScreenRecordingSettings } from "../../lib/shell";
import type { Snapshot } from "../../lib/types";
import { AgentMark } from "../../ui/AgentMark";
import { Button } from "../../ui/Button";
import { useConfirm } from "../../ui/ConfirmDialog";
import { Callout } from "./Callout";
import "./recording-now.css";

const TICK_MS = 1000;
/** Сколько последних отсчётов уровня видно полосками. */
export const LEVEL_HISTORY = 48;
const SOURCE: Record<string, string> = { auto: "автозапись", manual: "вручную", live: "с ассистентом" };

/** Уровень дорожки 0…1: ключи — имена файлов дорожек («mic.opus», «sys.opus»). */
export function trackLevel(levels: Record<string, number> | undefined, track: "mic" | "sys"): number {
  let peak = 0;
  for (const [name, v] of Object.entries(levels ?? {})) {
    if (name.startsWith(track) && Number.isFinite(v)) peak = Math.max(peak, v);
  }
  return Math.min(1, Math.max(0, peak));
}

const pad = (n: number) => String(n).padStart(2, "0");
/** «21:05» из начала записи (локальное время meta.json) или из прошедшего времени. */
function startedText(startedAt: string | null | undefined, startMs: number): string {
  const parsed = startedAt ? new Date(startedAt) : null;
  const at = parsed && !Number.isNaN(parsed.getTime()) ? parsed : new Date(startMs);
  return `${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

const zeros = () => Array.from({ length: LEVEL_HISTORY }, () => 0);

/** Полоски последних отсчётов уровня обеих дорожек; новый отсчёт — новый объект `levels`. */
function useLevelHistory(levels: Record<string, number> | undefined) {
  const [hist, setHist] = useState(() => ({ mic: zeros(), sys: zeros() }));
  useEffect(() => {
    setHist((h) => ({
      mic: [...h.mic.slice(1), trackLevel(levels, "mic")],
      sys: [...h.sys.slice(1), trackLevel(levels, "sys")],
    }));
  }, [levels]);
  return hist;
}

function LevelRow({ name, note, warn, track, value, history }: {
  name: string; note?: string | null; warn?: boolean; track: "mic" | "sys"; value: number; history: number[];
}) {
  const id = useId();
  const pct = Math.round(value * 100);
  return (
    <div className="rec-now__level">
      <span className="rec-now__level-name">
        <b id={id}>{name}</b>
        {note && <span className={`rec-now__level-note${warn ? " rec-now__level-note--warn" : ""}`}>{note}</span>}
      </span>
      <div className={`rec-now__bars rec-now__bars--${track}`} role="meter" aria-labelledby={id}
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-valuetext={`${pct} %`}>
        {history.map((v, i) => (
          <i key={i} style={{ height: `${Math.max(6, Math.round(v * 100))}%` }} />
        ))}
      </div>
      <span className="rec-now__level-value num" aria-hidden="true">{pct} %</span>
    </div>
  );
}

export function RecordingNow({ endpoint, snapshot, startedAt, autoTranscribe = true, noModel = null, onSnapshot }: {
  endpoint: Endpoint;
  snapshot: Snapshot;
  /**
   * Ответ команды — новый снимок (как у кнопки в рейке): страница не ждёт опроса, и
   * «Включить ассистента» не нажать второй раз, пока снимок старый.
   */
  onSnapshot?: (s: Snapshot) => void;
  /** Начало записи из meta.json (`started_at`); нет — по прошедшему времени. */
  startedAt?: string | null;
  /** `recording.auto_transcribe`: после остановки запись сразу встанет в расшифровку. */
  autoTranscribe?: boolean;
  /** Почему ассистента не позвать (нет модели); null — можно. */
  noModel?: string | null;
}) {
  const [confirmNode, confirm] = useConfirm();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const hintId = useId();
  const inviteId = useId();
  // Часы: прошедшее из снимка плюс время с его прихода.
  const elapsedS = snapshot.elapsed_s;
  const [seenAt, setSeenAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = Date.now();
    setSeenAt(t);
    setNow(t);
  }, [elapsedS]);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, []);
  const levels = useLevelHistory(snapshot.levels);

  const live = snapshot.live;
  // Временная встреча идёт вне библиотеки, и карточки у неё нет: ветки для неё — на всякий случай,
  // чтобы страница не обещала сохранить то, что удалится (те же подписи, что у кнопки в рейке).
  const temporary = !!snapshot.temporary;
  // Ассистент, которого позвали в эту запись: запускается, слушает или выключается.
  const attached = !!live?.attached && (!!live.active || !!live.starting || !!live.stopping);
  const warming = attached && !!live?.active && live.ready === false;
  const assistantNote = !attached ? null : live?.stopping ? "Ассистент выключается…"
    : live?.starting || warming ? startingText(live) : null;

  const shownS = elapsedS + Math.max(0, now - seenAt) / 1000;
  const source = snapshot.source ? SOURCE[snapshot.source] : null;
  const head = ["Идёт запись", source].filter(Boolean).join(" · ");
  const begun = `Началась в ${startedText(startedAt, seenAt - elapsedS * 1000)}.`;
  const after = temporary ? `Временная встреча ${TEMP_NOTE}.`
    : autoTranscribe ? "После остановки встреча сразу расшифруется." : null;

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };
  const run = (cmd: "stop" | "cancel" | "keep") => act(async () => {
    // Сначала команда: `onSnapshot?.(await …)` без слушателя не вызвал бы её вовсе.
    const result = await recordingCommand(endpoint, cmd);
    onSnapshot?.(result);
  });
  const discard = async () => {
    if (await confirm(discardConfirm(snapshot.forget_gaps))) await run("cancel");
  };
  const endTemporary = async () => {
    if (await confirm(TEMP_END_CONFIRM)) await run("stop");
  };
  const attach = () => act(async () => {
    const result = await liveAttach(endpoint);
    if (!result.ok) setError(result.error || ATTACH_FAILED);
    onSnapshot?.({ ...snapshot, live: liveOf(result) });
  });
  const openSettings = () => act(() => openScreenRecordingSettings());

  const sys = snapshot.system_audio_missing ?? null;
  const lowDisk = snapshot.disk_free_gb !== null && snapshot.disk_free_gb < LOW_DISK_GB;

  return (
    <section className="rec-now" aria-label="Идёт запись">
      <div className="rec-now__head">
        <p className="rec-now__state">
          <span className="live-dot" aria-hidden="true" />
          <span>{head}</span>
          {temporary && <span className="badge badge--stale">{TEMP_BADGE}</span>}
        </p>
        <p className="rec-now__clock num" role="timer" aria-label="Время записи">{clock(shownS)}</p>
        <p className="rec-now__note">{after ? `${begun} ${after}` : begun}</p>
        {assistantNote && <p className="rec-now__note" role="status">{assistantNote}</p>}
      </div>

      {(lowDisk || sys || (snapshot.devices_fallback?.length ?? 0) > 0) && (
        <div className="rec-now__warnings">
          {sys && (
            <Callout tone="warn" label={sys.notice}
              actions={sys.permission && (
                <Button onClick={openSettings} disabled={busy}>Открыть настройки «Запись экрана»</Button>
              )}>
              {sys.notice}
            </Callout>
          )}
          {lowDisk && (
            <Callout tone="warn" label={`Мало места: ${snapshot.disk_free_gb} ГБ`}>
              Мало места: {snapshot.disk_free_gb} ГБ — запись может оборваться, освободите место на диске записей
            </Callout>
          )}
          {(snapshot.devices_fallback ?? []).map((f) => (
            <Callout key={f.kind} tone="warn" label={fallbackText(f)}>
              {fallbackText(f)}{f.device && <span className="rec-now__muted"> · Запись идёт с «{f.device}»</span>}
            </Callout>
          ))}
        </div>
      )}

      <div className="card rec-now__levels">
        <LevelRow name="Микрофон" track="mic" value={trackLevel(snapshot.levels, "mic")} history={levels.mic} />
        <LevelRow name="Собеседники" track="sys" value={trackLevel(snapshot.levels, "sys")} history={levels.sys}
          note={sys ? "не записывается" : null} warn={!!sys} />
      </div>

      <div className="rec-now__actions">
        {temporary ? (
          <>
            <Button variant="primary" size="lg" disabled={busy} onClick={() => void endTemporary()}>{TEMP_STOP_LABEL}</Button>
            <Button size="lg" disabled={busy} onClick={() => void run("keep")}>{KEEP_LABEL}</Button>
          </>
        ) : (
          <>
            <Button variant="primary" size="lg" disabled={busy} onClick={() => void run("stop")}>Остановить и сохранить</Button>
            <Button variant="danger" size="lg" disabled={busy} onClick={() => void discard()}>{DISCARD_LABEL}…</Button>
          </>
        )}
      </div>
      {error && <p className="rec-now__error" role="alert">{error}</p>}

      {!attached && !temporary && (
        <section className="card aurora-wash rec-now__invite" aria-labelledby={inviteId}>
          <AgentMark size={20} />
          <span className="rec-now__invite-text">
            <b id={inviteId}>Позвать ассистента</b>
            <span>Догонит уже сказанное и дальше будет писать вам в чат. Запись не прервётся.</span>
            {noModel && <span id={hintId} className="rec-now__muted">{noModel}</span>}
          </span>
          <Button variant="aurora" disabled={!!noModel || busy} aria-describedby={noModel ? hintId : undefined}
            onClick={() => void attach()}>Включить ассистента</Button>
        </section>
      )}
      {confirmNode}
    </section>
  );
}
