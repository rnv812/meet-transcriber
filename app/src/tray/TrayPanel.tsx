import {
  AudioLines, Check, ChevronRight, Circle, CircleAlert, Clock3, LoaderCircle, Save, Square, Trash2, TriangleAlert, X,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import { fallbackText, liveOf } from "../app/RecordingBadge";
import { noProvider } from "../features/card/assistant";
import { ApiError, type Endpoint, liveStart, liveStop, recordingCommand } from "../lib/api";
import { clock, dayLabel, duration, errorText } from "../lib/format";
import {
  DISCARD_LABEL, KEEP_LABEL, discardConfirm, TEMP_BADGE, TEMP_END_CONFIRM, TEMP_LABEL, TEMP_NOTE,
} from "../lib/recordingStop";
import type { AssistantInfo, Recording, Snapshot } from "../lib/types";
import { AgentMark } from "../ui/AgentMark";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Icon } from "../ui/Icon";
import { IconButton } from "../ui/IconButton";
import { MeetMark } from "../ui/MeetMark";
import { Tip } from "../ui/Tip";
import { elapsedOf, phaseOf } from "./trayModel";

const TICK_MS = 1000;
const ERROR_MS = 6000;
export const NO_PROVIDER = "Подключите Claude Code, Codex или OpenCode в настройках";
const START_FAILED = "Не удалось запустить ассистента";
/** Подключение модели — раздел «Модели ИИ» настроек. */
const MODELS_SECTION = "models";

/** Куда ведут ссылки панели: окно Meet, его запись или раздел настроек. */
export type OpenTarget = { recording?: string; section?: string };

/**
 * Панель записи под значком в строке меню macOS.
 *
 * Действия — те же вызовы резидента, что у кнопки записи в окне
 * (`RecordingBadge`) и пунктов меню значка: `/recording/start|stop`,
 * `/live/start|stop`. Ответ команды — новый снимок, он применяется сразу.
 * Пауза не предусмотрена.
 *
 * «Остановить без сохранения» (запись резидента) и конец временной встречи
 * спрашивают — вопрос встаёт в панель вместо кнопок (окно панели маленькое,
 * модальное окно в нём не поместилось бы); фокус на «Продолжить запись» /
 * «Продолжить», Esc — тоже она (и панель при этом не прячется). Спрятанная
 * панель запись не трогает: удаляет только кнопка «Удалить запись».
 *
 * Вид — Atlas Aurora: главная кнопка — `primary`, остановка — `danger`,
 * остальное — контур и «призрак»; тема и палитра — от окна (useAppearance).
 */
export function TrayPanel({
  endpoint, snapshot, snapshotAt, online, recent, justStopped, assistant, visible = true,
  onSnapshot, onOpen,
}: {
  endpoint: Endpoint | null;
  snapshot: Snapshot | null;
  /** Date.now() прихода снимка: от него тикает таймер. */
  snapshotAt: number;
  /** null — ещё не знаем (ищем резидента). */
  online: boolean | null;
  /** Последняя сохранённая запись. */
  recent: Recording | null;
  /** `recent` только что остановили — крупное «Открыть запись». */
  justStopped: boolean;
  /** Кто ответит ассистенту; null — не знаем (кнопка доступна). */
  assistant: AssistantInfo | null;
  /** Панель на экране: таймер тикает только тогда. */
  visible?: boolean;
  onSnapshot: (s: Snapshot) => void;
  onOpen: (target: OpenTarget) => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState<string | null>(null);
  const [needsProvider, setNeedsProvider] = useState(false);
  const [pending, setPending] = useState(false);
  const [asking, setAsking] = useState<"discard" | "temp-end" | null>(null);
  const phase = phaseOf(snapshot, online);
  const recordingNow = phase.kind === "recording";
  // Запись кончилась (или началась другая) — вопрос про неё больше не к месту.
  useEffect(() => { if (!recordingNow) setAsking(null); }, [recordingNow]);
  const ticking = visible && phase.kind === "recording";

  useEffect(() => { setNow(Date.now()); }, [snapshotAt]);
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [ticking]);
  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), ERROR_MS);
    return () => clearTimeout(t);
  }, [error]);

  const act = (call: () => Promise<void>) => {
    if (!endpoint || !snapshot || pending) return;
    setError(null);
    setNeedsProvider(false);
    setPending(true);
    call()
      .catch((cause) => {
        if (cause instanceof ApiError && cause.status === 409) setNeedsProvider(true);
        else setError(errorText(cause));
      })
      .finally(() => setPending(false));
  };
  const record = (command: "start" | "stop" | "cancel" | "keep") => act(async () => {
    onSnapshot(await recordingCommand(endpoint!, command));
  });
  const startLive = (temporary = false) => act(async () => {
    const result = await (temporary ? liveStart(endpoint!, { temporary: true }) : liveStart(endpoint!));
    if (!result.ok) setError(result.error || START_FAILED);
    onSnapshot({ ...snapshot!, live: liveOf(result) });
  });
  const stopLive = () => act(async () => {
    const result = await liveStop(endpoint!);
    onSnapshot({ ...snapshot!, live: liveOf(result) });
  });
  const blocked = noProvider(assistant) || needsProvider;

  let hero: ReactNode;
  let showRecent = false;
  if (phase.kind === "offline" || phase.kind === "connecting" || !snapshot) {
    hero = (
      <>
        <Status tone="quiet">{phase.kind === "connecting" ? "Подключаюсь к службе записи…" : "Служба записи не запущена"}</Status>
        {phase.kind === "offline" && <p className="tp__note">Откройте Meet — там видно, что с ней, и её можно перезапустить.</p>}
      </>
    );
  } else if (phase.kind === "recording") {
    const seconds = elapsedOf(snapshot, snapshotAt, now);
    const title = snapshot.title?.trim() || null;
    const fallbacks = snapshot.devices_fallback ?? [];
    const sysAudio = phase.liveOnly ? null : snapshot.system_audio_missing?.notice;
    hero = (
      <>
        <Status tone="rec">
          {phase.temporary ? "Идёт временная встреча" : "Идёт запись"}
          {phase.auto && <span className="badge badge--plain">авто</span>}
        </Status>
        <div className="tp__timer" role="timer" aria-live="off"
          aria-label={seconds === null ? "Время записи неизвестно" : `Идёт ${clock(seconds)}`}>
          {seconds === null ? "—:—" : clock(seconds)}
        </div>
        {title && <div className="tp__title">{title}</div>}
        {phase.assistant && (
          <span className="badge badge--plain tp__badge">
            <AgentMark state={phase.assistant === "on" ? "listen" : "wait"} size={12} />
            {phase.assistant === "on" ? "с ассистентом"
              : phase.assistant === "starting" ? "ассистент запускается…" : "ассистент выключается…"}
          </span>
        )}
        {phase.temporary && (
          <Tip content={TEMP_NOTE}>
            <span className="badge badge--stale badge--plain tp__badge">
              <Icon as={Clock3} size="sm" />
              {TEMP_BADGE}
            </span>
          </Tip>
        )}
        {[...fallbacks.map(fallbackText), ...(sysAudio ? [sysAudio] : [])].map((text) => (
          <p key={text} className="callout callout--warn tp__callout">
            <Icon as={TriangleAlert} size="sm" className="ic" />
            <span>{text}</span>
          </p>
        ))}
        {asking ? (
          <ConfirmDialog inline className="tp__confirm"
            {...(asking === "discard"
              ? discardConfirm(snapshot.forget_gaps)
              : TEMP_END_CONFIRM)}
            onCancel={() => setAsking(null)}
            onConfirm={() => {
              const what = asking;
              setAsking(null);
              record(what === "discard" ? "cancel" : "stop");
            }} />
        ) : (
          <div className="tp__actions">
            <Button variant="danger" size="md" className="tp__wide" disabled={pending}
              onClick={() => (phase.liveOnly ? stopLive() : phase.temporary ? setAsking("temp-end") : record("stop"))}>
              <Icon as={Square} className="tp__glyph-fill" />
              {phase.temporary ? "Закончить" : "Остановить"}
            </Button>
            {phase.temporary && (
              <Button variant="secondary" className="tp__wide" icon={Save} disabled={pending}
                onClick={() => record("keep")}>
                {KEEP_LABEL}
              </Button>
            )}
            {!phase.liveOnly && !phase.temporary && (
              <Button variant="ghost" className="tp__wide tp__discard" icon={Trash2} disabled={pending}
                onClick={() => setAsking("discard")}>
                {DISCARD_LABEL}…
              </Button>
            )}
          </div>
        )}
      </>
    );
  } else if (phase.kind === "live-starting") {
    hero = (
      <>
        <Status tone="busy">Ассистент запускается…</Status>
        <p className="tp__note">Запись уже идёт — ассистент загружает модель.</p>
        <div className="tp__actions">
          <Button variant="danger" size="md" className="tp__wide" disabled={pending} onClick={stopLive}>
            <Icon as={Square} className="tp__glyph-fill" />
            Остановить
          </Button>
        </div>
      </>
    );
  } else if (phase.kind === "saving") {
    hero = (
      <>
        <Status tone="busy">Сохраняю запись…</Status>
        <p className="tp__note">Ассистент дописывает встречу. Окно можно закрыть.</p>
        <div className="tp__actions">
          <Button variant="danger" size="md" className="tp__wide" disabled>
            <Icon as={Square} className="tp__glyph-fill" />
            Остановить
          </Button>
        </div>
      </>
    );
  } else if (justStopped && recent) {
    hero = (
      <>
        <Status tone="done">Запись сохранена</Status>
        {recent.duration_s != null && (
          <div className="tp__timer tp__timer--done">{clock(recent.duration_s)}</div>
        )}
        <div className="tp__title">{recordingName(recent)}</div>
        <div className="tp__actions">
          <Button variant="primary" size="md" className="tp__wide" icon={AudioLines}
            onClick={() => onOpen({ recording: recent.id })}>
            Открыть запись
          </Button>
          <Button variant="secondary" className="tp__wide" disabled={pending} onClick={() => record("start")}>
            <Icon as={Circle} className="tp__glyph-rec" />
            Начать новую запись
          </Button>
        </div>
      </>
    );
  } else {
    showRecent = !!recent;
    hero = (
      <>
        <Status tone="quiet">
          {snapshot.auto_record?.enabled ? "Автозапись включена" : "Запись не идёт"}
        </Status>
        <div className="tp__actions">
          <Button variant="primary" size="md" className="tp__wide" disabled={pending} onClick={() => record("start")}>
            <Icon as={Circle} className="tp__glyph-fill" />
            Начать запись
          </Button>
          <Button variant="secondary" className="tp__wide" disabled={pending || blocked}
            aria-describedby={blocked ? "tp-provider" : undefined} onClick={() => startLive()}>
            <AgentMark size={16} />
            С ассистентом
          </Button>
          <Tip content={`Временная встреча ${TEMP_NOTE}`}>
            <Button variant="ghost" className="tp__wide" icon={Clock3} disabled={pending || blocked}
              aria-describedby={blocked ? "tp-provider" : undefined} onClick={() => startLive(true)}>
              {TEMP_LABEL}
            </Button>
          </Tip>
        </div>
        {blocked && (
          <p id="tp-provider" className="tp__note">
            {NO_PROVIDER}.{" "}
            <Button variant="link" onClick={() => onOpen({ section: MODELS_SECTION })}>Открыть настройки</Button>
          </p>
        )}
      </>
    );
  }

  return (
    <div className="tp__content">
      {error && (
        <div className="callout callout--err tp__callout tp__callout--top" role="alert">
          <Icon as={CircleAlert} size="sm" className="ic" />
          <span className="tp__callout-text">{error}</span>
          <IconButton icon={X} label="Скрыть ошибку" size="xs" onClick={() => setError(null)} />
        </div>
      )}
      <section className="tp__hero">{hero}</section>
      {showRecent && recent && (
        <section className="tp__recent" aria-label="Последняя запись">
          <div className="tp__label">Последняя запись</div>
          <button type="button" className="tp__recent-row" onClick={() => onOpen({ recording: recent.id })}
            aria-label={`Открыть запись «${recordingName(recent)}»`}>
            <span className="tp__recent-glyph"><Icon as={AudioLines} size="sm" /></span>
            <span className="tp__recent-text">
              <span className="tp__recent-title">{recordingName(recent)}</span>
              <span className="tp__recent-meta">
                {recent.started_at && <span>{dayLabel(recent.started_at)}</span>}
                {recent.duration_s != null && <span className="tp__num">{duration(recent.duration_s)}</span>}
              </span>
            </span>
            <Icon as={ChevronRight} size="sm" className="tp__recent-go" />
          </button>
        </section>
      )}
      <footer className="tp__foot">
        <Button variant="ghost" className="tp__wide tp__link" onClick={() => onOpen({})}>
          <MeetMark size={14} />
          Открыть Meet
        </Button>
      </footer>
    </div>
  );
}

/** Название записи, а без него — когда она сделана. */
export function recordingName(recording: Recording): string {
  const title = recording.title?.trim();
  if (title) return title;
  return recording.started_at ? `Запись — ${dayLabel(recording.started_at)}` : recording.id;
}

function Status({ tone, children }: { tone: "rec" | "busy" | "done" | "quiet"; children: ReactNode }) {
  return (
    <div className={`tp__status tp__status--${tone}`} role="status">
      {tone === "rec" && <span className="tp__dot" aria-hidden="true" />}
      {tone === "busy" && <Icon as={LoaderCircle} size="sm" className="tp__spin" />}
      {tone === "done" && <Icon as={Check} size="sm" className="tp__glyph-ok" />}
      {tone === "quiet" && <span className="tp__ring" aria-hidden="true" />}
      <span className="tp__status-text">{children}</span>
    </div>
  );
}
