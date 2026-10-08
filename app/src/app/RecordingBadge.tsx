import { CircleAlert, Power, Save, Square, Timer, Trash2, TriangleAlert, X } from "lucide-react";
import {
  Fragment, useEffect, useId, useRef, useState,
  type ComponentPropsWithRef, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent, type ReactNode,
} from "react";

import { noProvider } from "../features/card/assistant";
import { type Endpoint, getAssistant, liveAttach, liveDetach, liveStart, liveStop, recordingCommand } from "../lib/api";
import { clock, errorText } from "../lib/format";
import {
  DISCARD_LABEL, KEEP_LABEL, discardConfirm, TEMP_BADGE, TEMP_END_CONFIRM, TEMP_LABEL, TEMP_NOTE, TEMP_STOP_LABEL,
} from "../lib/recordingStop";
import { openScreenRecordingSettings } from "../lib/shell";
import type { AgentProfile, AssistantInfo, LiveStatus, Snapshot } from "../lib/types";
import { PROFILES, PROFILE_LABELS, PROFILE_NOTES, profileOf } from "../live/profiles";
import { AgentMark } from "../ui/AgentMark";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { floatingStyle, useFloating } from "../ui/floating";
import { Icon } from "../ui/Icon";
import { IconButton } from "../ui/IconButton";
import { RailTip } from "./RailTip";
import "./rail.css";

/** Меньше стольких ГБ на диске записей — предупреждение «Мало места» (рейка и страница «Идёт запись»). */
export const LOW_DISK_GB = 5;
const ERROR_MS = 6000;
const TICK_MS = 1000;
const NO_PROVIDER = "Подключите Claude Code, Codex или OpenCode в настройках";
const START_FAILED = "Не удалось запустить ассистента";
export const ATTACH_FAILED = "Не удалось включить ассистента";
const START_LABEL = "Начать запись";
const STOP_LABEL = "Остановить и сохранить";
const CANCEL_START_LABEL = "Отменить запуск";
const RECORD_ITEM = "Записать";
const RECORD_NOTE = "без ассистента; расшифровка — после остановки";
const STOP_NOTE = "запись сохранится и расшифруется";
const TEMP_STOP_NOTE = "встреча и разговор удалятся — с подтверждением";
const IDLE_MENU = "Варианты записи";
const RECORDING_MENU = "Действия с записью";

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

/** Строки подсказки: первая — состояние, дальше — пометки; для диктора — через точку. */
function TipLines({ lines }: { lines: string[] }) {
  return lines.map((line, i) => (
    <Fragment key={i}>
      {i > 0 && <span className="sr-only">. </span>}
      <span className="rail__tip-line">{line}</span>
    </Fragment>
  ));
}

/** Ошибка у рейки: справа от неё, с «Скрыть ошибку»; `up` — раскрывается вверх (низ рейки). */
function RailError({ text, onDismiss, up = false }: { text: string; onDismiss: () => void; up?: boolean }) {
  return (
    <div className={`rail-error glass glass--dense${up ? " rail-error--up" : ""}`} role="alert">
      <Icon as={CircleAlert} className="rail-error__icon" />
      <p className="rail-error__text">{text}</p>
      <IconButton icon={X} size="xs" label="Скрыть ошибку" onClick={onDismiss} />
    </div>
  );
}

/** Пункт меню кнопки записи: значок, название и строка пояснения под ним. */
function RecItem({ icon, title, note, danger = false, ...rest }: {
  icon: ReactNode;
  title: ReactNode;
  note: string;
  danger?: boolean;
} & Omit<ComponentPropsWithRef<"button">, "title" | "type" | "role">) {
  return (
    <button type="button" role="menuitem" tabIndex={-1}
      className={`rec-menu__item${danger ? " rec-menu__item--danger" : ""}`} {...rest}>
      <span className="rec-menu__icon" aria-hidden="true">{icon}</span>
      <span className="rec-menu__text">
        <span className="rec-menu__title">{title}</span>
        <span className="rec-menu__note">{note}</span>
      </span>
    </button>
  );
}

/** Ошибка гаснет сама через несколько секунд. */
function useFadingError(): [string | null, (text: string | null) => void] {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), ERROR_MS);
    return () => clearTimeout(t);
  }, [error]);
  return [error, setError];
}

/**
 * Кнопка записи в рейке (app/Nav) и её меню.
 *
 * Кнопка-значок 48 px: в простое — главная «Начать запись», во время записи —
 * «Остановить и сохранить» (danger). Состояние — в подсказке справа (она же
 * описание кнопки): «Идёт запись · мм:сс», «· ассистент», «· автозапись»,
 * пометка временной встречи, этап запуска ассистента.
 *
 * Снимок приходит раз в несколько секунд, поэтому часы тикают локально:
 * `elapsed_s` из снимка плюс время, прошедшее с его прихода (`snapshotAt`).
 * Ответ команды записи — тоже снимок: применяем его сразу, не дожидаясь опроса.
 *
 * Сама кнопка (щелчок, Enter, контекстное меню) открывает меню справа от себя;
 * фокус — на первом пункте, это прежнее действие кнопки: «Записать» в простое,
 * «Остановить и сохранить» (у временной — «Закончить временную встречу») во
 * время записи. Поэтому Enter → Enter (или два щелчка) — как раньше один.
 * Отдельной узкой кнопки «ещё» под главной нет (ревью 0.4: мелкая и
 * некрасивая). Запись одного ассистента, его запуск и остановка — без меню:
 * у кнопки одно действие («Остановить и сохранить», «Отменить запуск»).
 *
 * В простое в меню, кроме «Записать», — запись «С ассистентом» (живой режим). При нём
 * `snapshot.status` остаётся "idle", а состояние — в `snapshot.live`; часы
 * живого режима идут от `started_at` (стенное время резидента, когда пошёл
 * звук; неизвестно — без часов). Пока ассистент запускается — его этап
 * (`live.stage`: «загружаю модель распознавания…»); звук при этом уже пишется
 * (`active` без `ready`). Ошибка живого режима (`live.error`) видна с момента,
 * как появилась в снимке, — и во время обычной записи (подключённый ассистент
 * упал, запись идёт); упал (`ended_by: "crash"`) — висит до «×», иначе гаснет
 * через несколько секунд.
 *
 * Во время обычной записи в меню — «Включить ассистента» (запись не
 * прерывается: ассистент догоняет уже записанное и слушает дальше) или, когда
 * он включён (`live.attached`), «Выключить ассистента» (запись идёт дальше).
 * Без подключённой модели пункт неактивен с подсказкой. Там же —
 * «Остановить без сохранения…» (с вопросом: запись и всё, что с ней связано,
 * удаляется).
 *
 * Профиль сессии (0.3.7) выбирается тем же щелчком: и «С ассистентом», и
 * «Включить ассистента» — по пункту на профиль («Рабочая встреча»,
 * «Личный»; профиль из настроек — первым, с пометкой); в шапке сессии
 * его можно сменить по ходу.
 *
 * «Временная встреча с ассистентом» (в меню простоя): запись идёт вне
 * библиотеки и на остановке удаляется. Пока она идёт — пометка «Временная — не
 * сохранится», кнопка — «Закончить временную встречу» и спрашивает
 * «Временная встреча закончится и будет удалена.», а в меню — «Сохранить как
 * обычную встречу».
 *
 * Предупреждения записи (мало места, подмена устройства, системный звук macOS) —
 * `RecordingWarnings`, внизу рейки.
 */
export function RecordingBadge({ endpoint, snapshot, snapshotAt, online = true, onSnapshot }: {
  endpoint: Endpoint | null;
  snapshot: Snapshot | null;
  /** Date.now() прихода снимка; без него — момент, когда кнопка его увидела. */
  snapshotAt?: number;
  /** Резидент на связи: иначе снимок устарел и кнопка ничего не сделает. */
  online?: boolean;
  onSnapshot?: (s: Snapshot) => void;
}) {
  const [error, setError] = useFadingError();
  // Открытый вопрос: «Остановить без сохранения?» или конец временной встречи.
  const [asking, setAsking] = useState<"discard" | "temp-end" | null>(null);
  const [seenAt, setSeenAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const recording = snapshot?.status === "recording";
  const temporary = recording && !!snapshot?.temporary;
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

  // Меню: кто ответит — спрашиваем при каждом открытии, настройки могли смениться.
  const [menu, setMenu] = useState(false);
  const [assistant, setAssistant] = useState<AssistantInfo | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const anchor = useRef<HTMLSpanElement>(null);
  const main = useRef<HTMLButtonElement>(null);
  const item = useRef<HTMLButtonElement>(null);
  const menuBox = useRef<HTMLDivElement>(null);
  // Справа от кнопки, верхом вровень с ней; всегда в пределах окна (ui/floating).
  const menuPos = useFloating(menu ? anchor : null, menuBox, { gap: 0 });
  const hintId = useId();
  const tipId = useId();
  const blocked = noProvider(assistant);
  // Профиль по умолчанию (`assist.profile`) — первым и с пометкой (ревью M5);
  // старый резидент его не присылает — порядок как есть, без пометки.
  const fallback = assistant?.profile ? profileOf(assistant.profile) : null;
  const profiles = fallback ? [fallback, ...PROFILES.filter((p) => p !== fallback)] : PROFILES;
  const mark = (p: AgentProfile) => (p === fallback ? " (по умолчанию)" : "");
  // Сменился режим (простой ↔ запись ↔ запись с ассистентом) — меню и вопрос больше не к месту.
  useEffect(() => { setMenu(false); setAsking(null); }, [mode]);
  // Открытое меню — фокус на первый пункт (прежнее действие кнопки: он всегда доступен).
  useEffect(() => {
    if (menu) item.current?.focus();
  }, [menu]);
  useEffect(() => {
    if (!menu || !endpoint) return;
    let current = true;
    getAssistant(endpoint).then((info) => { if (current) setAssistant(info); }).catch(() => {});
    return () => { current = false; };
  }, [menu, endpoint]);
  useEffect(() => {
    if (!menu) return;
    const down = (e: globalThis.MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setMenu(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setMenu(false);
      main.current?.focus();
    };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [menu]);

  if (!endpoint || !snapshot || !online) return null;
  const since = Math.max(0, now - (snapshotAt ?? seenAt)) / 1000;
  const run = (cmd: "start" | "stop" | "cancel" | "keep") => {
    setMenu(false);
    setError(null);
    recordingCommand(endpoint, cmd)
      .then((result) => onSnapshot?.(result))
      .catch((e) => setError(errorText(e)));
  };
  /** `failed` — текст, если ответ 200 с ok:false (не запустился сразу: например, нет интерпретатора). */
  const runLive = (call: (ep: Endpoint) => Promise<{ ok: boolean } & LiveStatus>, failed?: string) => {
    setMenu(false);
    setError(null);
    call(endpoint)
      .then((result) => {
        if (failed && !result.ok) setError(result.error || failed);
        onSnapshot?.({ ...snapshot, live: liveOf(result) });
      })
      .catch((e) => setError(errorText(e)));
  };
  const startTemporary = () => runLive((ep) => liveStart(ep, { temporary: true }), START_FAILED);
  const ask = (what: "discard" | "temp-end") => {
    setMenu(false);
    setAsking(what);
  };
  // Ошибка прошлого ассистента — не поверх нового (запускается, слушает,
  // дописывает), но и во время обычной записи: подключённый упал, запись идёт.
  const liveBusy = liveActive || !!live?.starting || !!live?.stopping;
  const shownError = error ?? (liveBusy ? null : liveNotice?.text ?? null);
  const dismiss = () => {
    setError(null);
    setLiveNotice(null);
  };

  // Главная кнопка: имя, действие (нет — неактивна) и строки подсказки (нет — подсказка = имя).
  let label = START_LABEL;
  let act: (() => void) | null = () => run("start");
  let lines: string[] = [];
  if (recording) {
    const note = !attached ? null : live?.stopping ? "Ассистент выключается…"
      : live?.starting || warmingUp(live) ? startingText(live) : null;
    const listening = attached && liveActive && !warmingUp(live) && !live?.stopping;
    label = temporary ? TEMP_STOP_LABEL : STOP_LABEL;
    act = () => (temporary ? ask("temp-end") : run("stop"));
    const head = [`Идёт запись · ${clock(snapshot.elapsed_s + since)}`,
      listening ? "ассистент" : null, snapshot.source === "auto" ? "автозапись" : null];
    lines = [head.filter(Boolean).join(" · "), temporary ? TEMP_BADGE : null, note]
      .filter((x): x is string => !!x);
  } else if (live?.stopping) {
    label = STOP_LABEL;
    act = null;
    lines = ["Останавливаю…"];
  } else if (live?.starting) {
    label = CANCEL_START_LABEL;
    act = () => runLive(liveStop);
    lines = [startingText(live)];
  } else if (live?.active) {
    const liveS = live.started_at == null ? null : now / 1000 - live.started_at;
    // Звук уже пишется, а модель ещё грузится: часы идут, ассистент — скоро.
    const warming = warmingUp(live);
    label = STOP_LABEL;
    act = () => runLive(liveStop);
    const head = ["Идёт запись", liveS === null ? null : clock(liveS), warming ? null : "ассистент"];
    lines = [head.filter(Boolean).join(" · "), warming ? startingText(live) : null]
      .filter((x): x is string => !!x);
  }
  // Меню — в простое и во время обычной записи; у записи ассистента — одно действие, без меню.
  const menuLabel = idle ? IDLE_MENU : recording ? RECORDING_MENU : null;
  const openMenu = (e: MouseEvent) => {
    if (!menuLabel) return;
    e.preventDefault();
    setMenu(true);
  };
  // Клавиатура меню: ↑/↓/Home/End — по доступным пунктам, Tab — закрыть (Esc — общий обработчик выше).
  const menuKeys = (e: ReactKeyboardEvent) => {
    if (e.key === "Tab") {
      e.preventDefault();
      setMenu(false);
      main.current?.focus();
      return;
    }
    const all = [...(menuBox.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]:not(:disabled)") ?? [])];
    if (!all.length) return;
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    const next = e.key === "ArrowDown" ? (at + 1) % all.length
      : e.key === "ArrowUp" ? (at - 1 + all.length) % all.length
        : e.key === "Home" ? 0 : e.key === "End" ? all.length - 1 : -1;
    if (next < 0) return;
    e.preventDefault();
    all[next]?.focus();
  };
  const agentIcon = <AgentMark size={16} />;
  const noProviderHint = blocked ? hintId : undefined;

  let items: ReactNode = null;
  if (menu && recording) {
    items = (
      <>
        <RecItem ref={item} icon={<Icon as={Square} fill="currentColor" stroke="none" size="sm" />}
          title={temporary ? TEMP_STOP_LABEL : STOP_LABEL} note={temporary ? TEMP_STOP_NOTE : STOP_NOTE}
          onClick={() => act?.()} />
        {attached ? (
          <RecItem icon={<Icon as={Power} />} title="Выключить ассистента"
            note="запись продолжится, сводка останется в карточке"
            disabled={!liveActive || !!live?.stopping} onClick={() => runLive(liveDetach)} />
        ) : profiles.map((p) => (
          <RecItem key={p} icon={agentIcon} title={<>Включить ассистента · {PROFILE_LABELS[p]}{mark(p)}</>}
            note={`догонит начало встречи; ${PROFILE_NOTES[p]}`}
            disabled={blocked} aria-describedby={noProviderHint}
            onClick={() => runLive((ep) => liveAttach(ep, p), ATTACH_FAILED)} />
        ))}
        {blocked && !attached && <div id={hintId} className="rec-menu__hint">{NO_PROVIDER}</div>}
        {temporary ? (
          <RecItem icon={<Icon as={Save} />} title={KEEP_LABEL}
            note="запись ляжет в библиотеку и расшифруется, как обычная" onClick={() => run("keep")} />
        ) : (
          <>
            <div className="rec-menu__sep" role="separator" />
            <RecItem danger icon={<Icon as={Trash2} />} title={`${DISCARD_LABEL}…`}
              note="запись, чат и материалы удалятся" onClick={() => ask("discard")} />
          </>
        )}
      </>
    );
  } else if (menu && idle) {
    items = (
      <>
        <RecItem ref={item} icon={<i className="rec-menu__dot" />} title={RECORD_ITEM} note={RECORD_NOTE}
          onClick={() => act?.()} />
        {profiles.map((p) => (
          <RecItem key={p} icon={agentIcon} title={<>С ассистентом · {PROFILE_LABELS[p]}{mark(p)}</>}
            note={PROFILE_NOTES[p]} disabled={blocked} aria-describedby={noProviderHint}
            onClick={() => runLive((ep) => liveStart(ep, { profile: p }), START_FAILED)} />
        ))}
        <RecItem icon={<Icon as={Timer} />} title={TEMP_LABEL} note={TEMP_NOTE}
          disabled={blocked} aria-describedby={noProviderHint} onClick={startTemporary} />
        {blocked && <div id={hintId} className="rec-menu__hint">{NO_PROVIDER}</div>}
      </>
    );
  }
  const open = !!items && !!menuLabel;

  return (
    <div className={`rail-rec${open ? " rail-rec--open" : ""}`} ref={box}>
      <RailTip tip={lines.length ? <TipLines lines={lines} /> : label} id={lines.length ? tipId : undefined}>
        <Button ref={main} variant={idle ? "primary" : "danger"} size="lg" flat className="btn--icon" aria-label={label}
          aria-describedby={lines.length ? tipId : undefined} disabled={!act}
          aria-haspopup={menuLabel ? "menu" : undefined} aria-expanded={menuLabel ? open : undefined}
          onClick={() => (menuLabel ? setMenu(!menu) : act?.())} onContextMenu={openMenu}>
          {idle ? <i className="rail-rec__dot" aria-hidden="true" />
            : <Icon as={Square} fill="currentColor" stroke="none" />}
        </Button>
      </RailTip>
      <span ref={anchor} className="rail-rec__anchor" aria-hidden="true" />
      {open && (
        <div ref={menuBox} className="rec-menu glass glass--dense" role="menu" aria-label={menuLabel ?? undefined}
          style={floatingStyle(menuPos)} onKeyDown={menuKeys}>
          {items}
        </div>
      )}
      {shownError && <RailError text={shownError} onDismiss={dismiss} />}
      {asking && (
        <ConfirmDialog {...(asking === "discard" ? discardConfirm(snapshot.forget_gaps) : TEMP_END_CONFIRM)}
          returnFocus={main}
          onCancel={() => setAsking(null)}
          onConfirm={() => {
            setAsking(null);
            run(asking === "discard" ? "cancel" : "stop");
          }} />
      )}
    </div>
  );
}

/**
 * Предупреждения записи внизу рейки — кнопки-значки цвета --warning; текст —
 * их имя и подсказка справа: мало места на диске; выбранное устройство не
 * нашлось (во время записи, своей или ассистента); macOS — системный звук не
 * пишется без разрешения (во время записи; по нажатию — настройки системы).
 */
export function RecordingWarnings({ endpoint, snapshot, online = true }: {
  endpoint: Endpoint | null;
  snapshot: Snapshot | null;
  online?: boolean;
}) {
  const [error, setError] = useFadingError();
  const baseId = useId();
  if (!endpoint || !snapshot || !online) return null;
  const recording = snapshot.status === "recording";
  const warns: { key: string; label: string; details: string[]; onClick?: () => void }[] = [];
  if (snapshot.disk_free_gb !== null && snapshot.disk_free_gb < LOW_DISK_GB) {
    warns.push({ key: "disk", label: `Мало места: ${snapshot.disk_free_gb} ГБ`, details: [] });
  }
  const sys = recording ? snapshot.system_audio_missing : null;
  if (sys) {
    warns.push({
      key: "system-audio", label: sys.notice,
      details: sys.permission ? ["Открыть настройки: нажмите на значок"] : [],
      onClick: sys.permission ? () => void openScreenRecordingSettings()
        .catch((cause) => setError(errorText(cause))) : undefined,
    });
  }
  // Подмена устройства — про идущую запись (свою или ассистента), не про простой.
  for (const f of recording || snapshot.live?.active ? snapshot.devices_fallback ?? [] : []) {
    warns.push({ key: f.kind, label: fallbackText(f), details: f.device ? [`Запись идёт с «${f.device}»`] : [] });
  }
  if (!warns.length && !error) return null;
  return (
    <div className="rail-warn">
      {warns.map((w) => {
        const id = w.details.length ? `${baseId}-${w.key}` : undefined;
        return (
          <RailTip key={w.key} id={id} tip={id ? <TipLines lines={[w.label, ...w.details]} /> : w.label}>
            <Button variant="ghost" size="md" className="btn--icon rail__warn" aria-label={w.label}
              aria-describedby={id} onClick={w.onClick}>
              <Icon as={TriangleAlert} />
            </Button>
          </RailTip>
        );
      })}
      {error && <RailError text={error} onDismiss={() => setError(null)} up />}
    </div>
  );
}
