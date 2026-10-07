import { ChevronDown, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { noProvider } from "../features/card/assistant";
import { type Endpoint, getAssistant, liveAttach, liveDetach, liveStart, liveStop, recordingCommand } from "../lib/api";
import { clock, errorText } from "../lib/format";
import { openScreenRecordingSettings } from "../lib/shell";
import type { AgentProfile, AssistantInfo, LiveStatus, Snapshot } from "../lib/types";
import { PROFILES, PROFILE_LABELS, PROFILE_NOTES } from "../live/profiles";
import { Button } from "../ui/Button";
import { floatingStyle, useFloating } from "../ui/floating";
import { Icon } from "../ui/Icon";

const LOW_DISK_GB = 5;
const ERROR_MS = 6000;
const TICK_MS = 1000;
const NO_PROVIDER = "Подключите Claude Code, Codex или OpenCode в настройках";
const START_FAILED = "Не удалось запустить ассистента";
const ATTACH_FAILED = "Не удалось включить ассистента";

/** Выбранное в настройках устройство не нашлось — с какого пишем вместо него. */
export function fallbackText(f: { kind: "mic" | "output"; name: string }): string {
  return f.kind === "mic"
    ? `Микрофон «${f.name}» не найден — запись с системного`
    : `Устройство вывода «${f.name}» не найдено — запись с системного`;
}

/** Общая часть ответов `/live/start` и `/live/stop` — новое `snapshot.live`. */
export const liveOf = (r: LiveStatus): LiveStatus => ({
  active: r.active, starting: r.starting, stopping: r.stopping,
  folder: r.folder, error: r.error, started_at: r.started_at,
  ...(r.attached === undefined ? {} : { attached: r.attached }),
  ...(r.ready === undefined ? {} : { ready: r.ready }),
  ...(r.stage === undefined ? {} : { stage: r.stage }),
  ...(r.error_at === undefined ? {} : { error_at: r.error_at }),
  ...(r.error_folder === undefined ? {} : { error_folder: r.error_folder }),
});

/** Одна и та же папка записи: путь от резидента и из снимка пишутся по-разному. */
const samePath = (a: string, b: string) => {
  const norm = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  return norm(a) === norm(b);
};

/** Ассистент ещё не слушает: этап старта, если резидент его знает. */
export function startingText(live: LiveStatus | undefined): string {
  const stage = live?.stage?.trim();
  return stage ? `Ассистент запускается: ${stage}` : "Ассистент запускается…";
}

/** Звук пишется, а модель ещё грузится (старый резидент `ready` не присылает — готов). */
const warmingUp = (live: LiveStatus | undefined) => !!live?.active && live.ready === false;

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
 * пошёл звук; неизвестно — без часов). Пока ассистент запускается, вместо
 * общего «запускается» — его этап (`live.stage`: «загружаю модель
 * распознавания…»); звук при этом уже пишется (`active` без `ready`). Ошибка
 * живого режима (`live.error`) видна с момента, как появилась в снимке, — и
 * во время обычной записи (подключённый ассистент упал, запись идёт); упал
 * (`ended_by: "crash"`) — висит до «×», иначе гаснет через несколько секунд.
 *
 * Во время обычной записи «▾» рядом со «Стоп» — «Включить ассистента»
 * (запись не прерывается: ассистент догоняет уже записанное и слушает
 * дальше) или, когда он включён (`live.attached`), «Выключить ассистента»
 * (запись идёт дальше). Без подключённой модели пункт неактивен с подсказкой.
 *
 * Профиль сессии (0.3.7) выбирается тем же щелчком: и «С ассистентом», и
 * «Включить ассистента» — по пункту на профиль («Рабочая встреча»,
 * «Нейтральный»); в шапке сессии его можно сменить по ходу.
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
  // Новая ошибка — по времени появления: тот же текст при новом сбое — новое уведомление.
  const liveErrorKey = liveError ? `${live?.error_at ?? ""}|${liveError}` : null;
  const crashed = live?.ended_by === "crash";
  // Ошибка про другую запись (хвост ассистента прошлой упал, когда идёт уже
  // эта) — не уведомление об этой: не показываем ни сейчас, ни после её конца.
  const errorFolder = live?.error_folder;
  const foreignError = !!errorFolder && recording && !!snapshot?.folder
    && !samePath(errorFolder, snapshot.folder);
  const [liveNotice, setLiveNotice] = useState<{ text: string; sticky: boolean } | null>(null);
  const seenLiveError = useRef<{ ready: boolean; value: string | null }>({ ready: false, value: null });
  const hasSnapshot = snapshot != null;
  // Запись началась или кончилась — уведомление о прошлом ассистенте больше не к месту
  // (до этого эффекта: сбой, пришедший в том же снимке, ещё покажется).
  const wasRecording = useRef(recording);
  useEffect(() => {
    if (wasRecording.current === recording) return;
    wasRecording.current = recording;
    setLiveNotice(null);
  }, [recording]);
  useEffect(() => {
    if (!hasSnapshot) return;
    const seen = seenLiveError.current;
    if (!seen.ready) {
      seen.ready = true;
      seen.value = liveErrorKey;
      return;
    }
    if (liveErrorKey === seen.value) return;
    seen.value = liveErrorKey;
    if (foreignError) return;
    setLiveNotice(liveError ? { text: liveError, sticky: crashed } : null);
  }, [hasSnapshot, liveErrorKey, liveError, crashed, foreignError]);
  useEffect(() => {
    if (!liveNotice || liveNotice.sticky) return;
    const t = setTimeout(() => setLiveNotice(null), ERROR_MS);
    return () => clearTimeout(t);
  }, [liveNotice]);
  const liveActive = !!live?.active;
  const idle = !recording && !liveActive && !live?.starting && !live?.stopping;
  // Ассистент, включённый посреди этой записи (запускается, слушает, выключается).
  const attached = recording && !!live?.attached && (liveActive || !!live?.starting || !!live?.stopping);
  const mode = idle ? "idle" : recording ? "recording" : "live";
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
  const menuBox = useRef<HTMLDivElement>(null);
  // Под «Начать запись ▾», правым краем к его правому краю; всегда в пределах окна (ui/floating).
  const menuPos = useFloating(menu ? split : null, menuBox, { align: "end", gap: 4 });
  const hintId = useId();
  const blocked = noProvider(assistant);
  // Сменился режим (простой ↔ запись ↔ запись с ассистентом) — меню больше не к месту.
  useEffect(() => { setMenu(false); }, [mode]);
  // Открытое меню — фокус на пункт; неактивен (нет провайдера) — остаётся на «▾».
  useEffect(() => {
    if (!menu) return;
    if (item.current && !item.current.disabled) item.current.focus();
    else more.current?.focus();
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
  const runLive = (call: typeof liveStart | typeof liveStop | typeof liveAttach | typeof liveDetach,
    profile?: AgentProfile) => {
    setMenu(false);
    setError(null);
    (profile ? (call as typeof liveStart)(endpoint, profile) : call(endpoint))
      .then((result) => {
        // Не запустился сразу (например, нет интерпретатора): ответ 200 с ok:false.
        if (call === liveStart && !result.ok) setError(result.error || START_FAILED);
        if (call === liveAttach && !result.ok) setError(result.error || ATTACH_FAILED);
        onSnapshot?.({ ...snapshot, live: liveOf(result) });
      })
      .catch((e) => setError(errorText(e)));
  };
  // Ошибка прошлого ассистента — не поверх нового (запускается, слушает,
  // дописывает), но и во время обычной записи: подключённый упал, запись идёт.
  const liveBusy = liveActive || !!live?.starting || !!live?.stopping;
  const shownError = error ?? (liveBusy ? null : liveNotice?.text ?? null);
  const dismiss = () => {
    setError(null);
    setLiveNotice(null);
  };

  let main;
  if (recording) {
    const note = !attached ? null : live?.stopping ? "Ассистент выключается…"
      : live?.starting || warmingUp(live) ? startingText(live) : null;
    const listening = attached && liveActive && !warmingUp(live) && !live?.stopping;
    main = (
      <>
        <span className="rec-badge__live num">● REC {clock(snapshot.elapsed_s + since)}{listening ? " · ассистент" : ""}</span>
        {snapshot.source === "auto" && <span className="badge">авто</span>}
        {note && <span className="muted">{note}</span>}
        <span className="split split--plain" ref={split}>
          <Button variant="danger" className="split__main" onClick={() => run("stop")}>Стоп</Button>
          <Button ref={more} variant="danger" className="split__more" aria-label="Ассистент в этой записи"
            aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}><Icon as={ChevronDown} size="sm" /></Button>
          {menu && (
            <div ref={menuBox} className="rec-menu" role="menu" aria-label="Ассистент в этой записи" style={floatingStyle(menuPos)}>
              {attached ? (
                <button ref={item} type="button" role="menuitem" className="rec-menu__item"
                  disabled={!liveActive || !!live?.stopping} onClick={() => runLive(liveDetach)}>
                  Выключить ассистента
                  <span className="rec-menu__note">запись продолжится, сводка останется в карточке</span>
                </button>
              ) : PROFILES.map((p, k) => (
                <button key={p} ref={k === 0 ? item : undefined} type="button" role="menuitem" className="rec-menu__item"
                  disabled={blocked} aria-describedby={blocked ? hintId : undefined}
                  onClick={() => runLive(liveAttach, p)}>
                  Включить ассистента · {PROFILE_LABELS[p]}
                  <span className="rec-menu__note">догонит начало встречи; {PROFILE_NOTES[p]}</span>
                </button>
              ))}
              {blocked && !attached && <div id={hintId} className="rec-menu__hint">{NO_PROVIDER}</div>}
            </div>
          )}
        </span>
      </>
    );
  } else if (live?.stopping) {
    main = (
      <>
        <span className="rec-badge__live">Останавливаю…</span>
        <Button variant="danger" disabled>Стоп</Button>
      </>
    );
  } else if (live?.starting) {
    main = (
      <>
        <span className="muted">{startingText(live)}</span>
        <Button variant="danger" onClick={() => runLive(liveStop)}>Стоп</Button>
      </>
    );
  } else if (live?.active) {
    const liveS = live.started_at == null ? null : now / 1000 - live.started_at;
    // Звук уже пишется, а модель ещё грузится: часы идут, ассистент — скоро.
    const warming = warmingUp(live);
    main = (
      <>
        <span className="rec-badge__live num">
          ● REC {liveS === null ? "" : `${clock(liveS)} `}{warming ? "" : "· ассистент"}
        </span>
        {warming && <span className="muted">{startingText(live)}</span>}
        <Button variant="danger" onClick={() => runLive(liveStop)}>Стоп</Button>
      </>
    );
  } else {
    main = (
      <span className="split" ref={split}>
        <Button variant="primary" className="split__main" onClick={() => run("start")}>Начать запись</Button>
        <Button ref={more} variant="primary" className="split__more" aria-label="Другие варианты записи"
          aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}><Icon as={ChevronDown} size="sm" /></Button>
        {menu && (
          <div ref={menuBox} className="rec-menu" role="menu" aria-label="Варианты записи" style={floatingStyle(menuPos)}>
            {PROFILES.map((p, k) => (
              <button key={p} ref={k === 0 ? item : undefined} type="button" role="menuitem" className="rec-menu__item"
                disabled={blocked} aria-describedby={blocked ? hintId : undefined}
                onClick={() => runLive(liveStart, p)}>
                С ассистентом · {PROFILE_LABELS[p]}
                <span className="rec-menu__note">{PROFILE_NOTES[p]}</span>
              </button>
            ))}
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
            onClick={dismiss}><Icon as={X} size="sm" /></button>
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
