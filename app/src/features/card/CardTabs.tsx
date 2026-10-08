/**
 * Вкладки записи: у готовой — «Расшифровка · Итоги · Ассистент · Агент»; пока
 * идёт запись с ассистентом — «Живой режим · Агент» (чат — в живом режиме);
 * пока запись ждёт расшифровки или расшифровывается — «Расшифровка · Агент» (на
 * первой — ход работы); запись без ассистента — «Запись · Агент». «Ассистент» —
 * чат с агентом-участником после встречи (`AssistantTab`); у текста без
 * спикеров — «Расшифровка · Ассистент · Агент».
 *
 * Вкладка монтируется при первом открытии и дальше живёт скрытой: прокрутка
 * не теряется при переключении, а итоги не запрашиваются у тех, кто их не
 * открывал. Агент (терминал с Claude Code, Codex или OpenCode) так же переживает
 * переключение вкладок — и смену этапа записи: живой режим → расшифровка →
 * готово (панели ключуются по вкладке); сам сеанс агента живёт и без карточки
 * (agentSessions), пока работает — на вкладке «Агент» зелёная точка.
 *
 * `agentRequest` — «Спросить агента» (✦): новая просьба открывает «Агент» и
 * уходит туда ссылкой для поля ввода.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { AgentRequest } from "../../lib/agentRef";
import type { Endpoint } from "../../lib/api";
import type { ChatUpdatedEvent, Job } from "../../lib/types";
import { Tip } from "../../ui/Tip";
import { AgentTab, type AgentInsert } from "./AgentTab";
import { AssistantTab } from "./AssistantTab";
import { useAgentLive } from "./agentSessions";
import { useAssistant } from "./assistant";
import { SummaryTab } from "./SummaryTab";
import { TranscriptShown } from "./transcriptShown";
import "./assistant.css";

type Tab = "transcript" | "summary" | "assistant" | "agent";
/**
 * Этап записи: готова, идёт запись с ассистентом, идёт запись без него, ждёт
 * расшифровки или расшифровывается; `text` — текст расшифровки уже есть, а
 * спикеров ещё нет (Р4): итогов по нему нет, а поиск по тексту есть.
 */
export type CardStage = "ready" | "live" | "recording" | "pending" | "text";

/** Где Ctrl+F не уводит к поиску по расшифровке (в терминале агента клавиши — агенту). */
const FIND_IGNORED = ".rec-item__input, [role=dialog], [role=alertdialog], [aria-modal=true], .popover, .item-menu, [data-agent-terminal]";

const TABS: Record<CardStage, { id: Tab; label: string }[]> = {
  ready: [
    { id: "transcript", label: "Расшифровка" },
    { id: "summary", label: "Итоги" },
    { id: "assistant", label: "Ассистент" },
    { id: "agent", label: "Агент" },
  ],
  live: [
    { id: "transcript", label: "Живой режим" },
    { id: "agent", label: "Агент" },
  ],
  recording: [
    { id: "transcript", label: "Запись" },
    { id: "agent", label: "Агент" },
  ],
  pending: [
    { id: "transcript", label: "Расшифровка" },
    { id: "agent", label: "Агент" },
  ],
  text: [
    { id: "transcript", label: "Расшифровка" },
    { id: "assistant", label: "Ассистент" },
    { id: "agent", label: "Агент" },
  ],
};

export function CardTabs({
  endpoint, id, folder, jobs, transcript, onOpenSettings, showTranscript, stage = "ready", agentRequest = null,
  onAskAgent, onAgentTaken, agentContext, chatEvent = null, meetingEnd = null, onTime,
}: {
  /** Время «[мм:сс]» в итогах: перейти к этому месту записи. */
  onTime?: (seconds: number) => void;
  endpoint: Endpoint;
  id: string;
  folder: string;
  jobs: Job[];
  /** Первая вкладка: расшифровка, живой режим или ход расшифровки. */
  transcript: ReactNode;
  onOpenSettings?: (section: string) => void;
  /** Растёт, когда снаружи просят показать расшифровку (переход из поиска по записям). */
  showTranscript?: number;
  stage?: CardStage;
  /** «Спросить агента»: ссылка для поля ввода агента; новый объект — новая просьба. */
  agentRequest?: AgentInsert | null;
  /** ✦ у пунктов итогов. */
  onAskAgent?: (request: AgentRequest) => void;
  /** Вкладка «Агент» приняла просьбу `agentRequest` — владелец её сбрасывает. */
  onAgentTaken?: () => void;
  /** Версия расшифровки для агента (фаза и время записи): сменилась — агент перечитывает, что получит. */
  agentContext?: string;
  /** Последнее `chat.updated` этой записи (вкладка «Ассистент»). */
  chatEvent?: ChatUpdatedEvent | null;
  /** Когда кончилась встреча: «Встреча закончилась в …» над журналом ассистента. */
  meetingEnd?: Date | null;
}) {
  const tabs = TABS[stage];
  const [chosen, setTab] = useState<Tab>("transcript");
  // Вкладки этапа нет (итоги во время перерасшифровки) — первая.
  const tab = tabs.some((t) => t.id === chosen) ? chosen : "transcript";
  const [opened, setOpened] = useState<Set<Tab>>(() => new Set(["transcript"]));
  const assistant = useAssistant(endpoint);
  /** Агент этой записи работает (и когда вкладка «Агент» закрыта) — точка на вкладке. */
  const agentLive = useAgentLive(id);
  const base = useId();
  const buttons = useRef<Record<string, HTMLButtonElement | null>>({});

  const open = (next: Tab) => {
    setTab(next);
    setOpened((cur) => (cur.has(next) ? cur : new Set(cur).add(next)));
  };

  // Показы «Расшифровки»: отложенная (пока панель была скрыта) прокрутка к
  // совпадению идёт на следующем показе — layout-эффект, до отрисовки кадра.
  const [shown, setShown] = useState(0);
  useLayoutEffect(() => {
    if (tab === "transcript") setShown((n) => n + 1);
  }, [tab]);

  // «Спросить агента» — на «Агент», с какой бы вкладки ни были.
  const lastAgent = useRef<AgentInsert | null>(null);
  useEffect(() => {
    if (!agentRequest || agentRequest === lastAgent.current) return;
    lastAgent.current = agentRequest;
    open("agent");
  }, [agentRequest]);

  // Просьба из списка (фрагмент поиска) — на «Расшифровку», с какой бы вкладки ни были.
  const lastShow = useRef(showTranscript);
  useEffect(() => {
    if (showTranscript === lastShow.current) return;
    lastShow.current = showTranscript;
    setTab("transcript");
  }, [showTranscript]);

  // Ctrl+F — поиск по расшифровке с любой вкладки (по коду клавиши: и в русской раскладке).
  // Не из поля переименования в списке, диалога или всплывающего окна: там у Ctrl+F своё место.
  const transcriptPanel = useRef<HTMLDivElement>(null);
  const [findTick, setFindTick] = useState(0);
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      // Поиска по расшифровке ещё нет (живой режим, расшифровка идёт) — Ctrl+F браузера.
      if (stage !== "ready" && stage !== "text") return;
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.code !== "KeyF") return;
      if (e.target instanceof Element && e.target.closest(FIND_IGNORED)) return;
      e.preventDefault();
      setTab("transcript");
      setFindTick((n) => n + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stage]);
  useEffect(() => {
    if (!findTick) return;
    const field = transcriptPanel.current?.querySelector<HTMLInputElement>("[data-transcript-search]");
    field?.focus();
    field?.select();
  }, [findTick]);
  // Стрелки по списку вкладок — как у обычного tablist.
  const onKeyDown = (e: KeyboardEvent) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const at = tabs.findIndex((t) => t.id === tab);
    const next = tabs[(at + step + tabs.length) % tabs.length]!.id;
    open(next);
    buttons.current[next]?.focus();
  };

  const shared = { endpoint, id, folder, jobs, assistant, onOpenSettings };
  const panels: Record<Tab, () => ReactNode> = {
    transcript: () => <TranscriptShown.Provider value={shown}>{transcript}</TranscriptShown.Provider>,
    // Время в итогах — к этой реплике: на «Расшифровку», лента прокручивается к ней.
    summary: () => <SummaryTab {...shared} onAskAgent={onAskAgent}
      onTime={onTime ? (t) => { onTime(t); open("transcript"); } : undefined} />,
    assistant: () => (
      <AssistantTab endpoint={endpoint} id={id} folder={folder} jobs={jobs} event={chatEvent} assistant={assistant}
        onOpenSettings={onOpenSettings} endedAt={meetingEnd} />
    ),
    agent: () => (
      <AgentTab id={id} folder={folder} assistant={assistant} onOpenSettings={onOpenSettings} endpoint={endpoint} insert={agentRequest}
        onTaken={onAgentTaken} contextVersion={agentContext} textPhase={stage === "text"} />
    ),
  };

  // Полоса вкладок — Aurora `.tabs` под шапкой карточки, вне прокрутки; под ней —
  // тело карточки (`.card__body`, граница сверху): прокручивается только содержимое.
  return (
    <div className="card-tabs">
      <div className="card-tabs__bar">
        <div className="tabs card-tabs__list" role="tablist" aria-label="Содержимое записи" onKeyDown={onKeyDown}>
          {tabs.map((t) => (
            <button
              key={t.id} type="button" role="tab"
              id={`${base}-${t.id}`} aria-controls={`${base}-${t.id}-panel`}
              aria-selected={tab === t.id} tabIndex={tab === t.id ? 0 : -1}
              ref={(el) => { buttons.current[t.id] = el; }}
              onClick={() => open(t.id)}
            >
              {t.label}
              {t.id === "agent" && agentLive && (
                <Tip content="Агент работает" describe={false}>
                  <span className="agent-live" aria-hidden="true" />
                </Tip>
              )}
            </button>
          ))}
        </div>
      </div>
      <div className="card__body">
        {tabs.map((t) => (
          <div key={t.id} className="card-tabs__panel" role="tabpanel"
            ref={t.id === "transcript" ? transcriptPanel : undefined}
            id={`${base}-${t.id}-panel`} aria-labelledby={`${base}-${t.id}`} hidden={tab !== t.id}>
            {opened.has(t.id) && panels[t.id]()}
          </div>
        ))}
      </div>
    </div>
  );
}
