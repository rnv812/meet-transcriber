/**
 * Вкладки записи: у готовой — «Расшифровка · Итоги · Агент»; пока идёт запись
 * с ассистентом — «Живой режим · Агент»; пока запись ждёт расшифровки или
 * расшифровывается — «Расшифровка · Агент» (на первой — ход работы); запись без
 * ассистента — «Запись · Агент».
 *
 * Вкладка монтируется при первом открытии и дальше живёт скрытой: прокрутка
 * не теряется при переключении, а итоги не запрашиваются у тех, кто их не
 * открывал. Агент (терминал с Claude Code или Codex) так же переживает
 * переключение вкладок — и смену этапа записи: живой режим → расшифровка →
 * готово (панели ключуются по вкладке); останавливается вместе с карточкой.
 *
 * `agentRequest` — «Спросить агента» (✦): новая просьба открывает «Агент» и
 * уходит туда ссылкой для поля ввода.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { AgentRequest } from "../../lib/agentRef";
import type { Endpoint } from "../../lib/api";
import type { Job } from "../../lib/types";
import { AgentTab, type AgentInsert } from "./AgentTab";
import { useAssistant } from "./assistant";
import { SummaryTab } from "./SummaryTab";
import { TranscriptShown } from "./transcriptShown";
import "./assistant.css";

type Tab = "transcript" | "summary" | "agent";
/**
 * Этап записи: готова, идёт запись с ассистентом, идёт запись без него, ждёт
 * расшифровки или расшифровывается.
 */
export type CardStage = "ready" | "live" | "recording" | "pending";

/** Где Ctrl+F не уводит к поиску по расшифровке (в терминале агента клавиши — агенту). */
const FIND_IGNORED = ".rec-item__input, [role=dialog], [aria-modal=true], .popover, .item-menu, [data-agent-terminal]";

const TABS: Record<CardStage, { id: Tab; label: string }[]> = {
  ready: [
    { id: "transcript", label: "Расшифровка" },
    { id: "summary", label: "Итоги" },
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
};

export function CardTabs({
  endpoint, id, folder, jobs, transcript, onOpenSettings, showTranscript, stage = "ready", agentRequest = null,
  onAskAgent, onAgentTaken,
}: {
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
}) {
  const tabs = TABS[stage];
  const [chosen, setTab] = useState<Tab>("transcript");
  // Вкладки этапа нет (итоги во время перерасшифровки) — первая.
  const tab = tabs.some((t) => t.id === chosen) ? chosen : "transcript";
  const [opened, setOpened] = useState<Set<Tab>>(() => new Set(["transcript"]));
  const assistant = useAssistant(endpoint);
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
      if (stage !== "ready") return;
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
    summary: () => <SummaryTab {...shared} onAskAgent={onAskAgent} />,
    agent: () => (
      <AgentTab id={id} assistant={assistant} onOpenSettings={onOpenSettings} endpoint={endpoint} insert={agentRequest}
        onTaken={onAgentTaken} />
    ),
  };

  return (
    <div className="tabs">
      <div className="tabs__list" role="tablist" aria-label="Содержимое записи" onKeyDown={onKeyDown}>
        {tabs.map((t) => (
          <button
            key={t.id} type="button" role="tab" className="tabs__tab"
            id={`${base}-${t.id}`} aria-controls={`${base}-${t.id}-panel`}
            aria-selected={tab === t.id} tabIndex={tab === t.id ? 0 : -1}
            ref={(el) => { buttons.current[t.id] = el; }}
            onClick={() => open(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tabs.map((t) => (
        <div key={t.id} className="tabs__panel" role="tabpanel"
          ref={t.id === "transcript" ? transcriptPanel : undefined}
          id={`${base}-${t.id}-panel`} aria-labelledby={`${base}-${t.id}`} hidden={tab !== t.id}>
          {opened.has(t.id) && panels[t.id]()}
        </div>
      ))}
    </div>
  );
}
