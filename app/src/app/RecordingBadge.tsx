import { useEffect, useId, useRef, useState } from "react";

import { noProvider } from "../features/card/assistant";
import { type Endpoint, getAssistant, liveStart, liveStop, recordingCommand } from "../lib/api";
import { clock, errorText } from "../lib/format";
import { openScreenRecordingSettings } from "../lib/shell";
import type { AssistantInfo, LiveStatus, Snapshot } from "../lib/types";
import { Button } from "../ui/Button";

const LOW_DISK_GB = 5;
const ERROR_MS = 6000;
const TICK_MS = 1000;
const NO_PROVIDER = "Подключите Claude Code или Codex в настройках";
const START_FAILED = "Не удалось запустить ассистента";

/** Выбранное в настройках устройство не нашлось — с какого пишем вместо него. */
export function fallbackText(f: { kind: "mic" | "output"; name: string }): string {
  return f.kind === "mic"
    ? `Микрофон «${f.name}» не найден — запись с системного`
    : `Устройство вывода «${f.name}» не найдено — запись с системного`;
}

/** Общая часть ответов `/live/start` и `/live/stop` — новое `snapshot.live`. */
const liveOf = (r: LiveStatus): LiveStatus => ({
  active: r.active, starting: r.starting, stopping: r.stopping,
  folder: r.folder, error: r.error, started_at: r.started_at,
});

/**
 * Кнопка записи и таймер REC.
 *
 * Снимок приходит раз в несколько секунд, поэтому часы тикают локально:
 * `elapsed_s` из снимка плюс время, прошедшее с его прихода (`snapshotAt`).
 * Ответ команды записи — тоже снимок: применяем его сразу, не дожидаясь опроса.
 *
 * «▾» рядом с «Начать запись» — меню с записью «С ассистентом» (живой режим).
 * При нём `snapshot.status` остаётся "idle", а состояние — в `snapshot.live`;
 * часы живого режима идут от `started_at` (стенное время резидента, когда
 * ассистент начал слушать; неизвестно — без часов). Ошибка живого режима
 * (`live.error`) видна несколько секунд с момента, как появилась в снимке;
 * любую ошибку можно скрыть «×».
 */
export function RecordingBadge({ endpoint, snapshot, snapshotAt, online = true, onSnapshot }: {
  endpoint: Endpoint | null;
  snapshot: Snapshot | null;
  /** Date.now() прихода снимка; без него — момент, когда бейдж его увидел. */
  snapshotAt?: number;
  /** Резидент на связи: иначе снимок устарел и кнопка ничего не сделает. */
  online?: boolean;
  onSnapshot?: (s: Snapshot) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [seenAt, setSeenAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), ERROR_MS);
    return () => clearTimeout(t);
  }, [error]);
  const recording = snapshot?.status === "recording";
  const live = snapshot?.live;
  // Ошибка прошлого живого режима — уведомление о событии, а не состояние:
  // показываем, только когда она появилась (переход), а не всё время
  // простоя. Первый снимок — точка отсчёта: окно, открытое через час после
  // сбоя, старую ошибку не показывает.
  const liveError = live?.error?.trim() || null;
  const [liveNotice, setLiveNotice] = useState<string | null>(null);
  const seenLiveError = useRef<{ ready: boolean; value: string | null }>({ ready: false, value: null });
  const hasSnapshot = snapshot != null;
  useEffect(() => {
    if (!hasSnapshot) return;
    const seen = seenLiveError.current;
    if (!seen.ready) {
      seen.ready = true;
      seen.value = liveError;
      return;
    }
    if (liveError === seen.value) return;
    seen.value = liveError;
    setLiveNotice(liveError);
  }, [hasSnapshot, liveError]);
  useEffect(() => {
    if (!liveNotice) return;
    const t = setTimeout(() => setLiveNotice(null), ERROR_MS);
    return () => clearTimeout(t);
  }, [liveNotice]);
  const liveActive = !!live?.active;
  const idle = !recording && !liveActive && !live?.starting && !live?.stopping;
  const elapsedS = snapshot?.elapsed_s;
  useEffect(() => {
    const t = Date.now();
    setSeenAt(t);
    setNow(t);
  }, [elapsedS, recording]);
  useEffect(() => {
    if (!recording && !liveActive) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [recording, liveActive]);

  // Меню «▾»: кто ответит — спрашиваем при каждом открытии, настройки могли смениться.
  const [menu, setMenu] = useState(false);
  const [assistant, setAssistant] = useState<AssistantInfo | null>(null);
  const split = useRef<HTMLSpanElement>(null);
  const more = useRef<HTMLButtonElement>(null);
  const item = useRef<HTMLButtonElement>(null);
  const hintId = useId();
  const blocked = noProvider(assistant);
  // Ушли из простоя (запись, ассистент) — меню больше не к месту.
  useEffect(() => { if (!idle) setMenu(false); }, [idle]);
  // Открытое меню — фокус на пункт; неактивен (нет провайдера) — остаётся на «▾».
  useEffect(() => {
    if (!menu) return;
    if (blocked) more.current?.focus();
    else item.current?.focus();
  }, [menu, blocked]);
  useEffect(() => {
    if (!menu || !endpoint) return;
    let current = true;
    getAssistant(endpoint).then((info) => { if (current) setAssistant(info); }).catch(() => {});
    return () => { current = false; };
  }, [menu, endpoint]);
  useEffect(() => {
    if (!menu) return;
    const down = (e: MouseEvent) => {
      if (split.current && !split.current.contains(e.target as Node)) setMenu(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setMenu(false);
      more.current?.focus();
    };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [menu]);

  if (!endpoint || !snapshot || !online) return null;
  const low = snapshot.disk_free_gb !== null && snapshot.disk_free_gb < LOW_DISK_GB;
  // Подмена устройства — про идущую запись (свою или ассистента), не про простой.
  const fallbacks = recording || liveActive ? snapshot.devices_fallback ?? [] : [];
  const since = Math.max(0, now - (snapshotAt ?? seenAt)) / 1000;
  const run = (cmd: "start" | "stop") => {
    setError(null);
    recordingCommand(endpoint, cmd)
      .then((result) => onSnapshot?.(result))
      .catch((e) => setError(errorText(e)));
  };
  const runLive = (call: typeof liveStart | typeof liveStop) => {
    setMenu(false);
    setError(null);
    call(endpoint)
      .then((result) => {
        // Не запустился сразу (например, нет интерпретатора): ответ 200 с ok:false.
        if (call === liveStart && !result.ok) setError(result.error || START_FAILED);
        onSnapshot?.({ ...snapshot, live: liveOf(result) });
      })
      .catch((e) => setError(errorText(e)));
  };
  const shownError = error ?? (idle ? liveNotice : null);
  const dismiss = () => {
    setError(null);
    setLiveNotice(null);
  };

  let main;
  if (live?.stopping) {
    main = (
      <>
        <span className="rec-badge__live">Останавливаю…</span>
        <Button variant="danger" disabled>Стоп</Button>
      </>
    );
  } else if (live?.starting) {
    main = (
      <>
        <span className="muted">Ассистент запускается…</span>
        <Button variant="danger" onClick={() => runLive(liveStop)}>Стоп</Button>
      </>
    );
  } else if (live?.active) {
    const liveS = live.started_at == null ? null : now / 1000 - live.started_at;
    main = (
      <>
        <span className="rec-badge__live num">● REC {liveS === null ? "" : `${clock(liveS)} `}· ассистент</span>
        <Button variant="danger" onClick={() => runLive(liveStop)}>Стоп</Button>
      </>
    );
  } else if (recording) {
    main = (
      <>
        <span className="rec-badge__live num">● REC {clock(snapshot.elapsed_s + since)}</span>
        {snapshot.source === "auto" && <span className="badge">авто</span>}
        <Button variant="danger" onClick={() => run("stop")}>Стоп</Button>
      </>
    );
  } else {
    main = (
      <span className="split" ref={split}>
        <Button variant="primary" className="split__main" onClick={() => run("start")}>Начать запись</Button>
        <Button ref={more} variant="primary" className="split__more" aria-label="Другие варианты записи"
          aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>▾</Button>
        {menu && (
          <div className="rec-menu" role="menu" aria-label="Варианты записи">
            <button ref={item} type="button" role="menuitem" className="rec-menu__item" disabled={blocked}
              aria-describedby={blocked ? hintId : undefined} onClick={() => runLive(liveStart)}>
              С ассистентом
              <span className="rec-menu__note">дайджест и вопросы по ходу встречи</span>
            </button>
            {blocked && <div id={hintId} className="rec-menu__hint">{NO_PROVIDER}</div>}
          </div>
        )}
      </span>
    );
  }

  return (
    <div className="rec-badge">
      {shownError && (
        <span className="import__error import__error--box" role="alert">
          {shownError}
          <button type="button" className="import__close" aria-label="Скрыть ошибку"
            onClick={dismiss}>×</button>
        </span>
      )}
      {low && <span className="rec-badge__warn">Мало места: {snapshot.disk_free_gb} ГБ</span>}
      {recording && snapshot.system_audio_missing && (
        // macOS: запись идёт только с микрофона, пока не дано разрешение.
        <span className="rec-badge__warn" role="status">
          {snapshot.system_audio_missing.notice}
          {snapshot.system_audio_missing.permission && (
            <Button onClick={() => void openScreenRecordingSettings()
              .catch((cause) => setError(errorText(cause)))}>
              Открыть настройки
            </Button>
          )}
        </span>
      )}
      {fallbacks.map((f) => (
        <span key={f.kind} className="rec-badge__warn" title={f.device ? `Запись идёт с «${f.device}»` : undefined}>
          {fallbackText(f)}
        </span>
      ))}
      {main}
    </div>
  );
}
