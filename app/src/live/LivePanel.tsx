/**
 * Плавающая панель ассистента (окно `live`, страница live.html), Atlas Aurora.
 *
 * Окно создаёт и закрывает оболочка по `snapshot.live.active`; окно прозрачное
 * — стекло рисует корень панели. Панель двигают за шапку (двойной щелчок по
 * ней — на весь экран и обратно), её растягивают за края. Шапка 48 px: точка
 * записи, таймер, знак агента в его состоянии (`AgentMark`: слушает, ищет,
 * пишет, ждёт) и слово, бейдж непрочитанного, кнопки.
 *
 * Свёрнутая (макет MeetLiveMini) — под шапкой одно из: «Не удалось запустить
 * ассистента» («Повторить», «Открыть настройки»), вопрос вам («Копировать»,
 * «Показать в ленте»), «Догоняю начало встречи» с полосой хода, иначе одна
 * строка: последнее сообщение агента или самая важная подсказка (она
 * сменяется, только когда сменилась сама); щелчок разворачивает панель.
 * Развёрнутая и на весь экран — рабочая область (`LiveWorkspace`): вкладки,
 * а в широком окне — две колонки.
 *
 * «Не отвлекать»: ни подсветки, ни счётчиков, строка свёрнутой панели не
 * меняется, пока её подсказка жива; содержимое при этом обновляется.
 * Исключение — «Вам вопрос»: он встаёт в строку свёрнутой панели и в
 * «Не отвлекать» (без анимации) — ответа ждут сейчас.
 *
 * Ассистент, включённый посреди обычной записи (`status.attached`): вместо
 * «Стоп» — «Выключить ассистента», запись при этом идёт дальше; пока он
 * догоняет начало встречи, в шапке — «Догоняю N %».
 *
 * Запись ведёт резидент (ассистент к ней подключён) — в шапке и «Остановить
 * без сохранения» (корзина, с вопросом: запись, чат и материалы удаляются).
 * Временная встреча (`status.temporary`): пометка «Временная — не
 * сохранится», «Стоп» спрашивает «Временная встреча закончится и будет
 * удалена.», рядом — «Сохранить как обычную встречу». Вопрос встаёт на место
 * содержимого панели (свёрнутую на это время разворачиваем — в строку он не
 * помещается).
 *
 * Размер и место помнит оболочка (`useLiveWindow`). Фокус панель не берёт:
 * окно создаётся без фокуса, и ни один элемент не фокусируется сам —
 * клавиатура остаётся у звонка, пока человек не щёлкнет в панель.
 */

import { type MouseEvent, type ReactNode, useEffect, useRef, useState } from "react";

import { plainMarkdown } from "../lib/agentRef";
import {
  type Endpoint, NoResidentError, liveAttach, liveDetach, liveStart, liveStop, recordingCommand, resolveEndpoint,
} from "../lib/api";
import { clock, errorText } from "../lib/format";
import {
  DISCARD_LABEL, KEEP_LABEL, discardConfirm, TEMP_BADGE, TEMP_END_CONFIRM, TEMP_NOTE, TEMP_STOP_LABEL,
} from "../lib/recordingStop";
import { inTauri, invoke, trayPanelOpen } from "../lib/shell";
import type { AgentProfile, ChatMessage, LiveHint } from "../lib/types";
import {
  Bell, BellOff, Check, ChevronDown, ChevronUp, Copy, CornerDownRight, Maximize2, Minimize2, Pin, PowerOff,
  RefreshCw, Save, SlidersHorizontal, Square, Trash2, TriangleAlert,
} from "lucide-react";
import { AgentMark, type AgentState } from "../ui/AgentMark";
import { BADGE_CLASS } from "../ui/badge";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Icon } from "../ui/Icon";
import { IconButton } from "../ui/IconButton";
import { Truncate } from "../ui/Truncate";
import { isFinalAgent } from "./chatModel";
import { CatchupNote, LiveWorkspace, participantOn, useLiveView } from "./LiveWorkspace";
import { stateOf } from "./SessionBar";
import { KIND_LABEL, isUrgent, topHint } from "./liveModel";
import { useQuiet, useUnseen } from "./useAttention";
import { type Chat, useChat } from "./useChat";
import { useLiveAsk } from "./useLastLook";
import { useLive } from "./useLive";
import { useLiveStatus } from "./useLiveStatus";
import { useLiveWindow } from "./useLiveWindow";
import { useWide } from "./useWide";
import { LIVE_NUDGE_LEAD, OwnerVoiceNudge, wantsOwnerSample } from "../features/settings/OwnerVoiceDialog";
import { useAppearance } from "../theme/useAppearance";
import type { PanelStatus } from "./useLiveStatus";
import "./live.css";

const TICK_MS = 1000;
/** Сколько держать в шапке заметку о несработавшем действии с подсказкой. */
export const HINT_NOTE_MS = 8000;

const HINT_FAILED: Record<string, string> = {
  dismiss: "Подсказку не удалось скрыть",
  restore: "Подсказку не удалось вернуть",
  pin: "Подсказку не удалось закрепить",
  unpin: "Подсказку не удалось открепить",
};

/** Несработавшее действие с подсказкой — заметкой на HINT_NOTE_MS (карточки скрытой подсказки уже нет на экране). */
function useHintNote(error: { id: string; text: string; action?: string } | null): string | null {
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    if (!error) return;
    setNote(`${HINT_FAILED[error.action ?? ""] ?? "Действие с подсказкой не удалось"}: ${error.text}`);
    const t = setTimeout(() => setNote(null), HINT_NOTE_MS);
    return () => clearTimeout(t);
  }, [error]);
  return note;
}
const FIND_MS = 2000;
/** Сколько «Копировать» показывает «Скопировано». */
const COPIED_MS = 1500;
/** «Показать в ленте»: сколько ждать, пока сообщение появится в ленте, и сколько его подсвечивать. */
const REVEAL_MS = 1500;
const FLASH_MS = 1600;

/**
 * Знак агента по слову его состояния: слушает, ищет (думает, догоняет),
 * пишет (отвечает), ждёт (запускается, останавливается), покой. Ошибка — не
 * знак агента, а значок предупреждения (null).
 */
const MARKS: Record<string, AgentState> = {
  listening: "listen", thinking: "search", searching: "search", writing: "write", answering: "write",
  waiting: "wait", idle: "rest",
};
export function markOf(key: string): AgentState | null {
  return key === "error" ? null : MARKS[key] ?? "rest";
}

/**
 * Ассистент не запустился (или упал): режим кончился сбоем, причина
 * известна, нового запуска ещё нет. Окно оболочка закрывает по фронту
 * `active`, но до этого панель говорит, что случилось, и предлагает повторить.
 */
export function startFailed(s: PanelStatus | null | undefined): boolean {
  return !!s && !s.active && !s.starting && !s.stopping && s.ended_by === "crash" && !!s.error;
}

/**
 * Секунды с `started_at` (стенное время резидента, когда ассистент начал
 * слушать), тикает раз в секунду; null — время начала неизвестно.
 */
function useElapsed(startedAt: number | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt == null) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [startedAt]);
  return startedAt == null ? null : Math.max(0, now / 1000 - startedAt);
}

/** Кнопки, поля и ссылки шапки окно не таскают: по ним щёлкают. */
const CONTROLS = "button, input, textarea, select, a, [role='button']";

/**
 * Нажатие левой кнопкой на свободное место шапки: одиночное — тащить окно
 * (системное перетаскивание, оболочка `live_start_drag`), двойное — на весь
 * экран и обратно. Как `data-tauri-drag-region`, но двойной щелчок знает
 * вид панели и проверяется тестами.
 */
export function headPress(e: MouseEvent, drag: () => void, toggle?: () => void) {
  if (e.button !== 0 || (e.target as Element).closest(CONTROLS)) return;
  e.preventDefault(); // без выделения текста шапки
  if (e.detail === 2) toggle?.();
  else if (e.detail === 1) drag();
}

/**
 * Подсказка строки свёрнутой панели: самая важная; сменяется, только когда
 * сменилась самая важная. «Не отвлекать» — держим показанную, пока она жива,
 * но «Вам вопрос» встаёт в строку и тогда.
 */
function useShownHint(hints: LiveHint[], quiet: boolean): LiveHint | null {
  const shownId = useRef<string | null>(null);
  const best = topHint(hints);
  const kept = quiet && shownId.current ? hints.find((h) => h.id === shownId.current) : undefined;
  const shown = best && isUrgent(best) && !(kept && isUrgent(kept)) ? best : kept ?? best;
  shownId.current = shown?.id ?? null;
  return shown;
}

/**
 * Сообщение агента в строке свёрнутой панели: закреплённый вопрос, иначе
 * последнее. «Не отвлекать» — строка держит показанное, пока оно в ленте;
 * вопрос к вам встаёт в неё и тогда (ревью live-chat, M15).
 */
function useShownAgentLine(chat: Chat, quiet: boolean): ChatMessage | null {
  const shownId = useRef<string | null>(null);
  const kept = quiet && shownId.current ? chat.state.byId[shownId.current] : undefined;
  const shown = chat.pinned ?? (kept && isFinalAgent(kept) ? kept : null) ?? chat.lastAgent;
  shownId.current = shown?.id ?? null;
  return shown;
}

export function LivePanel({ endpoint }: { endpoint: Endpoint }) {
  const status = useLiveStatus(endpoint);
  const [stopRequested, setStopRequested] = useState(false);
  const stopping = stopRequested || !!status?.stopping;
  // До первого снимка считаем режим идущим: окно существует только при нём.
  // Дописывает запись — поток резидент уже закрыл, переподключаться незачем
  // (иначе «Нет связи с ассистентом» на всё время остановки).
  const chat = useChat(endpoint);
  const live = useLive(endpoint, (status ? status.active : true) && !stopping, chat.sink);
  const participant = participantOn(live, chat);
  const elapsed = useElapsed(status?.started_at);
  const { view, setExpanded, setMaximized, setPinned, startDrag } = useLiveWindow();
  const [stopError, setStopError] = useState<string | null>(null);
  const [quiet, setQuiet] = useQuiet(live.quietDefault);
  const root = useRef<HTMLDivElement>(null);
  const wide = useWide(root);
  // На весь экран — всё содержимое, как у развёрнутой.
  const open = view.expanded || view.maximized;
  const ask = useLiveAsk(live, open);
  const ws = useLiveView(live, { open, wide, quiet });
  const shown = useShownHint(live.hints, quiet);
  const hintNote = useHintNote(live.hintError);
  // Агент-участник: свёрнутая строка — закреплённый вопрос или последнее сообщение агента.
  const agentKeys = chat.items.flatMap((it) => (it.type === "message" && isFinalAgent(it.message) ? [it.message.id] : []));
  const unseenAgent = useUnseen(agentKeys, open, chat.loaded);

  // Esc возвращает обычный размер (клавиатура у панели, только если по ней
  // щёлкнули). В поле вопроса Esc — дело поля, окно не трогаем.
  useEffect(() => {
    if (!view.maximized) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || (e.target as Element | null)?.closest?.("input, textarea")) return;
      setMaximized(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view.maximized, setMaximized]);

  const attached = !!status?.attached;
  // «Запись с ассистентом»: запись — обычная, ассистент к ней подключён; из
  // панели её можно и остановить с сохранением, и только выключить ассистента.
  const withAssistant = attached && status?.source === "live";
  const [fullStop, setFullStop] = useState(false);
  const stop = (detach: boolean = attached) => {
    setFullStop(!detach);
    setStopRequested(true);
    setStopError(null);
    (detach ? liveDetach : liveStop)(endpoint).catch((e) => {
      setStopRequested(false);
      setStopError(errorText(e));
    });
  };
  // Временная встреча (идёт запись резидента с ассистентом в ней).
  const temporary = attached && !!status?.temporary;
  // Была ли эта встреча временной: после сбоя пометки в снимке уже нет, а
  // «Повторить» должно начать снова временную, а не сохраняемую встречу.
  const wasTemporary = useRef(false);
  if (status?.temporary) wasTemporary.current = true;
  // Вопрос «Остановить без сохранения?» / конец временной встречи. Свёрнутую
  // панель на время вопроса разворачиваем (в строку он не помещается), потом — обратно.
  const [asking, setAsking] = useState<"discard" | "temp-end" | null>(null);
  const unfolded = useRef(false);
  const askStop = (what: "discard" | "temp-end") => {
    unfolded.current = !(view.expanded || view.maximized);
    if (unfolded.current) setExpanded(true);
    setAsking(what);
  };
  const closeAsk = () => {
    setAsking(null);
    if (unfolded.current) setExpanded(false);
    unfolded.current = false;
  };
  const discard = () => {
    closeAsk();
    setFullStop(true);
    setStopRequested(true);
    setStopError(null);
    recordingCommand(endpoint, "cancel").catch((e) => {
      setStopRequested(false);
      setStopError(errorText(e));
    });
  };
  const keep = () => {
    setStopError(null);
    recordingCommand(endpoint, "keep").catch((e) => setStopError(errorText(e)));
  };

  const openHints = () => {
    ws.setTab(shown ? "hints" : "feed");
    setExpanded(true);
  };

  // Не запустился: «Повторить» — тот же старт (или подключение к идущей записи).
  const failed = startFailed(status) && !stopping;
  const [retryError, setRetryError] = useState<string | null>(null);
  const retry = () => {
    setRetryError(null);
    const profile: AgentProfile | undefined = chat.agent?.profile ?? live.agent?.profile;
    const opts = { ...(wasTemporary.current ? { temporary: true } : {}), ...(profile ? { profile } : {}) };
    const call = status?.recording ? liveAttach(endpoint, profile) : liveStart(endpoint, opts);
    return call.catch((e) => setRetryError(errorText(e)));
  };
  // Раздел «Модели ИИ» главного окна (оболочка откроет окно Meet на нём).
  const openModels = () => { void trayPanelOpen({ section: "models" }); };

  // Коротко: шапка узкой панели (от 300 px) вмещает таймер, состояние и пять кнопок.
  const catchup = live.catchup?.active ? live.catchup : null;
  // Звук уже идёт, а модель распознавания ещё грузится — этап старта.
  const warming = !!status?.active && status.ready === false;
  const stage = status?.stage?.trim();
  // С агентом-участником шапка говорит, что с ним (как его шапка сессии), а не
  // всегда «Слушает»: знак один, и состояния не спорят (ревью live-chat, M6).
  const agent = participant ? chat.agent ?? live.agent ?? null : null;
  const agentState = agent ? stateOf(agent, !!chat.writing) : { key: "listening", text: "слушает" };
  const head: { word: string; mark: AgentState | null } = stopping
    ? { word: attached && !fullStop ? "Выключаю…" : "Останавливаю…", mark: "wait" }
    : failed ? { word: "Ошибка", mark: null }
    : warming ? { word: stage ? `Запускается: ${stage}` : "Запускается…", mark: "wait" }
    : catchup ? { word: `Догоняю ${catchup.percent} %`, mark: "search" }
    : { word: agentState.text[0]!.toUpperCase() + agentState.text.slice(1), mark: markOf(agentState.key) };
  const state = head.word;
  const sizeLabel = view.maximized ? "Обычный размер" : "На весь экран";
  const last = live.lines.at(-1);
  const newHints = quiet ? 0 : participant ? unseenAgent : ws.unseen.hints;
  const shownAgent = useShownAgentLine(chat, quiet);
  const agentLine = participant ? shownAgent : null;
  // «Показать в ленте»: развернуть и, когда лента чата отрисуется, прокрутить
  // к сообщению, перевести на него фокус и подсветить (как «к сообщению …» в
  // ленте). Не появилось за REVEAL_MS — больше не ждём.
  const [reveal, setReveal] = useState<string | null>(null);
  useEffect(() => {
    if (!open || !reveal) return;
    const rows = root.current?.querySelectorAll<HTMLElement>("li[data-id]") ?? [];
    const row = [...rows].find((el) => el.dataset.id === reveal);
    if (!row) {
      const t = setTimeout(() => setReveal(null), REVEAL_MS);
      return () => clearTimeout(t);
    }
    row.scrollIntoView?.({ block: "center" });
    row.tabIndex = 0;
    row.focus({ preventScroll: true });
    row.classList.add("is-flash");
    setTimeout(() => row.classList.remove("is-flash"), FLASH_MS);
    setReveal(null);
  }, [open, reveal, chat.items]);
  const showInFeed = (id: string) => {
    setReveal(id);
    setExpanded(true);
  };
  const mods = `${open ? " live-panel--open" : ""}${view.maximized ? " live-panel--maximized" : ""}`;
  // Свёрнутую панель можно тащить за любое свободное место, а не только за шапку.
  const dragAnywhere = (e: MouseEvent) => {
    if (!open && !(e.target as Element).closest(".live-head")) headPress(e, startDrag);
  };
  // Временная встреча в свёрнутой панели: метка — в строке карточки, а не
  // отдельной строкой (под шапкой 48 в окне 120 места на одну карточку).
  const [tempShort, tempRest] = TEMP_BADGE.split(" — ");
  const tempBadge = temporary && !open ? (
    <span className={`${BADGE_CLASS.temp} live-temp__badge`} role="note" title={TEMP_NOTE}>
      {tempShort}{tempRest && <span className="sr-only"> — {tempRest}</span>}
    </span>
  ) : null;
  const failure = failed ? (
    <StartFailed error={status?.error ?? ""} retryError={retryError} onRetry={retry} onSettings={openModels}
      compact={!open} lead={tempBadge} />
  ) : null;
  // Свёрнутая строка: последнее сообщение агента, самая важная подсказка или реплика.
  const line = participant ? (
    <button type="button" className="live-last" onClick={() => setExpanded(true)}
      aria-label={agentLine ? `Ассистент: ${agentText(agentLine)}. Открыть чат` : "Развернуть панель"}>
      {agentLine ? (
        <>
          <span className="live-last__kind live-last__kind--agent">Ассистент</span>
          <Truncate className="live-last__text">{agentText(agentLine)}</Truncate>
        </>
      ) : last ? (
        <Truncate className="live-last__text muted" text={`${last.speaker ? `${last.speaker}: ` : ""}${last.text}`}>
          {last.speaker && <span className="live-feed__who">{last.speaker}</span>}
          <span>{last.text}</span>
        </Truncate>
      ) : <span className="live-last__text muted">Ассистент слушает встречу</span>}
    </button>
  ) : (
    <button type="button" className={`live-last${shown && isUrgent(shown) ? " live-last--urgent" : ""}`} onClick={openHints}
      aria-label={shown ? `Подсказка: ${shown.text}. Открыть подсказки` : "Развернуть панель"}>
      {shown ? (
        <>
          <span className={`live-last__kind live-hint--${shown.kind}`}>{KIND_LABEL[shown.kind]}</span>
          <Truncate className="live-last__text">{shown.text}</Truncate>
        </>
      ) : last ? (
        <Truncate className="live-last__text muted" text={`${last.speaker ? `${last.speaker}: ` : ""}${last.text}`}>
          {last.speaker && <span className="live-feed__who">{last.speaker}</span>}
          <span>{last.text}</span>
        </Truncate>
      ) : <span className="live-last__text muted">{live.loaded || participant ? "Реплики появятся, как только их расшифрует ассистент" : "Ассистент подключается…"}</span>}
    </button>
  );
  // Прежний режим: «Вам вопрос» важнее хода «Догоняю» — как раньше.
  const urgentHint = !participant && !!shown && isUrgent(shown);
  return (
    <div ref={root} className={`live-panel glass glass--dense${mods}${quiet ? " live-panel--quiet" : ""}`}
      onMouseDown={dragAnywhere}>
      <header className="live-head" onMouseDown={(e) => headPress(e, startDrag, () => setMaximized(!view.maximized))}>
        <span className="live-head__title"
          title={failed ? status?.error ?? state : stopping || warming ? state : "Ассистент слушает встречу"}>
          <span className="live-head__rec" aria-hidden="true" />
          <span className="live-head__clock">{elapsed === null ? "—" : clock(elapsed)}</span>{" "}
          <span className="live-head__sep" aria-hidden="true" />
          {head.mark ? <AgentMark state={head.mark} size={open ? 16 : 14} />
            : <Icon as={TriangleAlert} className="live-head__alert" />}
          <span className="live-head__state">{state}</span>
          {quiet && <span className="live-head__quiet-tag">тихо</span>}
          {/* Ошибки и связь — в той же строке состояния шапки (одна, по важности):
              не закрывают поле вопроса и действия и не сдвигают содержимое. */}
          {stopError ? (
            <span className="live-status live-status--error" role="alert" title={stopError}>
              <span className="live-status__dot" aria-hidden="true" />
              <span className="live-status__text">{stopError}</span>
            </span>
          ) : hintNote ? (
            <span className="live-status live-status--error" role="alert" title={hintNote}>
              <span className="live-status__dot" aria-hidden="true" />
              <span className="live-status__text">{hintNote}</span>
            </span>
          ) : live.error ? (
            <span className="live-status" role="status" title={live.error}>
              <span className="live-status__dot" aria-hidden="true" />
              <span className="live-status__text">{live.error}</span>
            </span>
          ) : live.status && !stopping && (
            <span className="live-status" role="status" title={live.status}>
              <span className="live-status__dot" aria-hidden="true" />
              <span className="live-status__text">{live.status}</span>
            </span>
          )}
        </span>
        {!open && newHints > 0 && (
          <span className={`${BADGE_CLASS.run} badge--plain live-head__count`}>
            <span className="sr-only">{participant ? "новых сообщений" : "новых подсказок"}: </span>{newHints}
          </span>
        )}
        {/* Все кнопки шапки — значки 32 px одного вида при любой ширине; подпись — в подсказке. */}
        <span className="live-head__actions">
          <IconButton icon={quiet ? BellOff : Bell} label="Не отвлекать" pressed={quiet} className="live-head__quiet"
            tooltip={quiet ? "«Не отвлекать» включено: без подсветки и счётчиков" : "Не отвлекать: без подсветки и счётчиков"}
            onClick={() => setQuiet(!quiet)} />
          <IconButton icon={Pin} label="Поверх всех окон" pressed={view.pinned} className="live-head__pin"
            tooltip={view.pinned ? "Панель поверх всех окон — открепить" : "Закрепить поверх всех окон"}
            onClick={() => setPinned(!view.pinned)} />
          <IconButton icon={view.maximized ? Minimize2 : Maximize2} label={sizeLabel}
            tooltip={`${sizeLabel} (двойной щелчок по шапке)`} onClick={() => setMaximized(!view.maximized)} />
          <IconButton icon={open ? ChevronUp : ChevronDown} label={open ? "Свернуть" : "Развернуть"}
            aria-expanded={open} onClick={() => setExpanded(!open)} />
          {attached && (
            <IconButton icon={PowerOff} label="Выключить ассистента" variant={withAssistant ? undefined : "danger"}
              className={withAssistant ? "live-head__detach" : "live-head__stop"}
              tooltip="Выключить ассистента — запись продолжится" onClick={() => stop(true)} disabled={stopping} />
          )}
          {temporary && (
            <IconButton icon={Save} label={KEEP_LABEL} className="live-head__keep"
              tooltip={`${KEEP_LABEL}: запись ляжет в библиотеку и расшифруется`} onClick={keep}
              disabled={stopping} />
          )}
          {attached && !temporary && (
            <IconButton icon={Trash2} label={DISCARD_LABEL} variant="danger" className="live-head__discard"
              tooltip={`${DISCARD_LABEL}: запись, чат и материалы удалятся`} onClick={() => askStop("discard")}
              disabled={stopping} />
          )}
          {(!attached || withAssistant) && (
            temporary ? (
              <IconButton icon={Square} label={TEMP_STOP_LABEL} variant="danger" className="live-head__stop"
                tooltip={`${TEMP_STOP_LABEL} — она будет удалена`} onClick={() => askStop("temp-end")}
                disabled={stopping} />
            ) : (
              <Button variant="danger" icon={Square} className="btn--icon live-head__stop"
                aria-label={withAssistant ? "Остановить и сохранить" : "Стоп"} title="Остановить и сохранить запись"
                onClick={() => stop(false)} disabled={stopping} />
            )
          )}
        </span>
      </header>
      {/* Отдельной строкой под шапкой: в шапке узкой панели метка обрезалась бы.
          Свёрнутая — метка в строке карточки (`tempBadge`). */}
      {temporary && open && (
        <div className="live-temp" role="note" title={TEMP_NOTE}>
          <span className={`${BADGE_CLASS.temp} live-temp__badge`}>{TEMP_BADGE}</span>
          <span className="live-temp__note">удалится вместе с чатом</span>
        </div>
      )}
      {asking ? (
        <div className="live-panel__confirm">
          <ConfirmDialog inline {...(asking === "discard" ? discardConfirm(status?.forget_gaps) : TEMP_END_CONFIRM)}
            onCancel={closeAsk}
            onConfirm={() => {
              if (asking === "discard") discard();
              else {
                closeAsk();
                stop(false);
              }
            }} />
        </div>
      ) : open ? (
        <div className="live-panel__body">
          {failure}
          {wantsOwnerSample(live.mic) && (
            <OwnerVoiceNudge endpoint={endpoint} lead={LIVE_NUDGE_LEAD} className="live-nudge" />
          )}
          <LiveWorkspace live={live} view={ws} onAsk={ask} disabled={stopping} chat={chat} />
        </div>
      ) : failure ? failure
      : participant && chat.pinned && agentLine ? (
        <AskYou m={agentLine} lead={tempBadge} onOpen={() => setExpanded(true)} onShow={() => showInFeed(agentLine.id)} />
      ) : catchup && !urgentHint ? (
        <div className="live-mini"><CatchupNote catchup={catchup} mini lead={tempBadge} /></div>
      ) : tempBadge ? (
        <div className="live-line">{tempBadge}{line}</div>
      ) : line}
    </div>
  );
}

/** Текст сообщения агента для свёрнутой строки: без разметки; упавший без текста — так и сказать. */
function agentText(m: ChatMessage): string {
  return plainMarkdown(m.text || "") || (m.status === "failed" ? "Не удалось получить ответ" : m.error || "");
}

/**
 * Свёрнутая: закреплённый вопрос агента к вам. Щелчок по тексту — развернуть
 * в чат; «Копировать» — текст вопроса; «Показать в ленте» — развернуть и
 * прокрутить к нему.
 */
function AskYou({ m, lead, onOpen, onShow }: {
  m: ChatMessage; lead?: ReactNode; onOpen: () => void; onShow: () => void;
}) {
  const text = agentText(m);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(t);
  }, [copied]);
  const copy = () => {
    if (!navigator.clipboard) return;
    return navigator.clipboard.writeText(text).then(() => setCopied(true), () => {});
  };
  return (
    <div className="live-mini" role="group" aria-label="Вопрос вам">
      <button type="button" className="live-mini__open" onClick={onOpen} aria-label={`Вопрос вам: ${text}. Открыть чат`}>
        <span className={`${BADGE_CLASS.run} live-mini__badge`}>Вопрос вам</span>
        <Truncate className="live-mini__text">{text}</Truncate>
      </button>
      <div className="live-mini__actions">
        {lead}
        {m.t != null && <span className="live-mini__time">{clock(m.t)}</span>}
        <Button size="xs" icon={copied ? Check : Copy} onClick={copy}>{copied ? "Скопировано" : "Копировать"}</Button>
        <Button size="xs" variant="ghost" icon={CornerDownRight} onClick={onShow}>Показать в ленте</Button>
      </div>
    </div>
  );
}

/**
 * «Не удалось запустить ассистента»: причина, «Повторить» (тот же старт или
 * подключение) и «Открыть настройки» (раздел «Модели ИИ»). В свёрнутой
 * панели причина — в одной строке с заголовком: под шапкой места на две строки.
 */
function StartFailed({ error, retryError, onRetry, onSettings, compact, lead }: {
  error: string; retryError: string | null; onRetry: () => unknown; onSettings: () => void; compact: boolean;
  /** Метка временной встречи в свёрнутой панели — первой в строке кнопок. */
  lead?: ReactNode;
}) {
  const reason = retryError ? `Повтор не удался: ${retryError}` : error;
  return (
    <div className={`live-mini live-fail${compact ? " live-fail--compact" : ""}`} role="alert">
      <p className="live-fail__head" title={reason}>
        <b className="live-fail__title">Не удалось запустить ассистента</b>
        <span className="live-fail__reason">{reason}</span>
      </p>
      <div className="live-mini__actions">
        {lead}
        <Button size="xs" icon={RefreshCw} onClick={onRetry}>Повторить</Button>
        <Button size="xs" variant="ghost" icon={SlidersHorizontal} onClick={onSettings}>Открыть настройки</Button>
      </div>
    </div>
  );
}

/** Тащить окно, пока панели ещё нет (резидента ищем). */
function dragWindow() {
  if (inTauri()) invoke("live_start_drag").catch((cause) => console.warn("live_start_drag:", cause));
}

/** Страница окна: находит резидента (адрес и токен от оболочки) и показывает панель. */
export function LiveWindow() {
  const [endpoint, setEndpoint] = useState<Endpoint | null>(null);
  useAppearance(endpoint);
  useEffect(() => {
    let gone = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const find = () => {
      resolveEndpoint().then((ep) => { if (!gone) setEndpoint(ep); }).catch((cause) => {
        if (!(cause instanceof NoResidentError)) console.warn("resolveEndpoint:", cause);
        if (!gone) timer = setTimeout(find, FIND_MS);
      });
    };
    find();
    return () => { gone = true; clearTimeout(timer); };
  }, []);

  if (!endpoint) {
    return (
      <div className="live-panel glass glass--dense">
        <header className="live-head" onMouseDown={(e) => headPress(e, dragWindow)}>
          <span className="live-head__title">
            <span className="live-head__rec" aria-hidden="true" />
            <AgentMark state="wait" size={14} />
            <span className="live-head__state">Ассистент слушает…</span>
          </span>
        </header>
      </div>
    );
  }
  return <LivePanel endpoint={endpoint} />;
}
