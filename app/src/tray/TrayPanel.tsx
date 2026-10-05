import { AudioLines, Check, ChevronRight, Circle, LoaderCircle, Sparkles, Square, X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import { fallbackText, liveOf } from "../app/RecordingBadge";
import { noProvider } from "../features/card/assistant";
import { ApiError, type Endpoint, liveStart, liveStop, recordingCommand } from "../lib/api";
import { clock, dayLabel, duration, errorText } from "../lib/format";
import type { AssistantInfo, Recording, Snapshot } from "../lib/types";
import { Icon } from "../ui/Icon";
import { elapsedOf, phaseOf } from "./trayModel";

const TICK_MS = 1000;
const ERROR_MS = 6000;
export const NO_PROVIDER = "Подключите Claude Code или Codex в настройках";
const START_FAILED = "Не удалось запустить ассистента";
const ASSISTANT_SECTION = "assistant";

/** Куда ведут ссылки панели: окно Meet, его запись или раздел настроек. */
export type OpenTarget = { recording?: string; section?: string };

/**
 * Панель записи под значком в строке меню macOS.
 *
 * Действия — те же вызовы резидента, что у кнопки записи в окне
 * (`RecordingBadge`) и пунктов меню значка: `/recording/start|stop`,
 * `/live/start|stop`. Ответ команды — новый снимок, он применяется сразу.
 * Пауза не предусмотрена.
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
  const phase = phaseOf(snapshot, online);
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
  const record = (command: "start" | "stop") => act(async () => {
    onSnapshot(await recordingCommand(endpoint!, command));
  });
  const startLive = () => act(async () => {
    const result = await liveStart(endpoint!);
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
          Идёт запись
          {phase.auto && <span className="tp__tag">авто</span>}
        </Status>
        <div className="tp__timer tp__num" role="timer" aria-live="off"
          aria-label={seconds === null ? "Время записи неизвестно" : `Идёт ${clock(seconds)}`}>
          {seconds === null ? "—:—" : clock(seconds)}
        </div>
        {title && <div className="tp__title">{title}</div>}
        {phase.assistant && (
          <span className={`tp__chip${phase.assistant === "on" ? "" : " tp__chip--muted"}`}>
            <Icon as={Sparkles} size="sm" />
            {phase.assistant === "on" ? "с ассистентом"
              : phase.assistant === "starting" ? "ассистент запускается…" : "ассистент выключается…"}
          </span>
        )}
        {[...fallbacks.map(fallbackText), ...(sysAudio ? [sysAudio] : [])].map((text) => (
          <p key={text} className="tp__warn">{text}</p>
        ))}
        <button type="button" className="tp__primary tp__primary--stop" disabled={pending}
          onClick={() => (phase.liveOnly ? stopLive() : record("stop"))}>
          <Icon as={Square} size="sm" className="tp__glyph-fill" />
          Остановить
        </button>
      </>
    );
  } else if (phase.kind === "live-starting") {
    hero = (
      <>
        <Status tone="busy">Ассистент запускается…</Status>
        <p className="tp__note">Запись уже идёт — ассистент загружает модель.</p>
        <button type="button" className="tp__primary tp__primary--stop" disabled={pending} onClick={stopLive}>
          <Icon as={Square} size="sm" className="tp__glyph-fill" />
          Остановить
        </button>
      </>
    );
  } else if (phase.kind === "saving") {
    hero = (
      <>
        <Status tone="busy">Сохраняю запись…</Status>
        <p className="tp__note">Ассистент дописывает встречу. Окно можно закрыть.</p>
        <button type="button" className="tp__primary tp__primary--stop" disabled>
          <Icon as={Square} size="sm" className="tp__glyph-fill" />
          Остановить
        </button>
      </>
    );
  } else if (justStopped && recent) {
    hero = (
      <>
        <Status tone="done">Запись сохранена</Status>
        {recent.duration_s != null && (
          <div className="tp__timer tp__timer--done tp__num">{clock(recent.duration_s)}</div>
        )}
        <div className="tp__title">{recordingName(recent)}</div>
        <button type="button" className="tp__primary" onClick={() => onOpen({ recording: recent.id })}>
          <Icon as={AudioLines} size="sm" />
          Открыть запись
        </button>
        <button type="button" className="tp__secondary" disabled={pending} onClick={() => record("start")}>
          <Icon as={Circle} size="sm" className="tp__glyph-rec" />
          Начать новую запись
        </button>
      </>
    );
  } else {
    showRecent = !!recent;
    hero = (
      <>
        <Status tone="quiet">
          {snapshot.auto_record?.enabled ? "Автозапись включена" : "Запись не идёт"}
        </Status>
        <button type="button" className="tp__primary" disabled={pending} onClick={() => record("start")}>
          <Icon as={Circle} size="sm" className="tp__glyph-fill" />
          Начать запись
        </button>
        <button type="button" className="tp__secondary" disabled={pending || blocked}
          aria-describedby={blocked ? "tp-provider" : undefined} onClick={startLive}>
          <Icon as={Sparkles} size="sm" className="tp__glyph-accent" />
          С ассистентом
        </button>
        {blocked && (
          <p id="tp-provider" className="tp__note">
            {NO_PROVIDER}.{" "}
            <button type="button" className="tp__inline-link"
              onClick={() => onOpen({ section: ASSISTANT_SECTION })}>Открыть настройки</button>
          </p>
        )}
      </>
    );
  }

  return (
    <div className="tp__content">
      {error && (
        <div className="tp__error" role="alert">
          <span>{error}</span>
          <button type="button" className="tp__error-close" aria-label="Скрыть ошибку" onClick={() => setError(null)}>
            <Icon as={X} size="sm" />
          </button>
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
        <button type="button" className="tp__link" onClick={() => onOpen({})}>
          <span className="tp__mark" aria-hidden="true" />
          Открыть Meet
        </button>
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
      {tone === "done" && <Icon as={Check} size="sm" className="tp__glyph-accent" />}
      {tone === "quiet" && <span className="tp__ring" aria-hidden="true" />}
      <span className="tp__status-text">{children}</span>
    </div>
  );
}
