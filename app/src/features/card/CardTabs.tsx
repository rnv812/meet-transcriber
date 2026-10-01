/**
 * Вкладки готовой записи: «Расшифровка · Итоги · Вопросы · Агент».
 *
 * Вкладка монтируется при первом открытии и дальше живёт скрытой: ожидающий
 * вопрос и прокрутка не теряются при переключении, а итоги и вопросы не
 * запрашиваются у тех, кто их не открывал. Агент (терминал с Claude Code или
 * Codex) так же переживает переключение вкладок и останавливается вместе с
 * карточкой.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { Endpoint } from "../../lib/api";
import type { Job } from "../../lib/types";
import { AgentTab } from "./AgentTab";
import { useAssistant } from "./assistant";
import { QaTab } from "./QaTab";
import { SummaryTab } from "./SummaryTab";
import { TranscriptShown } from "./transcriptShown";
import "./assistant.css";

type Tab = "transcript" | "summary" | "qa" | "agent";

/** Где Ctrl+F не уводит к поиску по расшифровке (в терминале агента клавиши — агенту). */
const FIND_IGNORED = ".rec-item__input, [role=dialog], [aria-modal=true], .popover, .item-menu, [data-agent-terminal]";

const TABS: { id: Tab; label: string }[] = [
  { id: "transcript", label: "Расшифровка" },
  { id: "summary", label: "Итоги" },
  { id: "qa", label: "Вопросы" },
  { id: "agent", label: "Агент" },
];

export function CardTabs({ endpoint, id, folder, jobs, transcript, onOpenSettings, showTranscript }: {
  endpoint: Endpoint;
  id: string;
  folder: string;
  jobs: Job[];
  transcript: ReactNode;
  onOpenSettings?: (section: string) => void;
  /** Растёт, когда снаружи просят показать расшифровку (переход из поиска по записям). */
  showTranscript?: number;
}) {
  const [tab, setTab] = useState<Tab>("transcript");
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
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.code !== "KeyF") return;
      if (e.target instanceof Element && e.target.closest(FIND_IGNORED)) return;
      e.preventDefault();
      setTab("transcript");
      setFindTick((n) => n + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
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
    const at = TABS.findIndex((t) => t.id === tab);
    const next = TABS[(at + step + TABS.length) % TABS.length]!.id;
    open(next);
    buttons.current[next]?.focus();
  };

  const shared = { endpoint, id, folder, jobs, assistant, onOpenSettings };
  const panels: Record<Tab, () => ReactNode> = {
    transcript: () => <TranscriptShown.Provider value={shown}>{transcript}</TranscriptShown.Provider>,
    summary: () => <SummaryTab {...shared} />,
    qa: () => <QaTab {...shared} />,
    agent: () => <AgentTab id={id} assistant={assistant} onOpenSettings={onOpenSettings} />,
  };

  return (
    <div className="tabs">
      <div className="tabs__list" role="tablist" aria-label="Содержимое записи" onKeyDown={onKeyDown}>
        {TABS.map((t) => (
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
      {TABS.map((t) => (
        <div key={t.id} className="tabs__panel" role="tabpanel"
          ref={t.id === "transcript" ? transcriptPanel : undefined}
          id={`${base}-${t.id}-panel`} aria-labelledby={`${base}-${t.id}`} hidden={tab !== t.id}>
          {opened.has(t.id) && panels[t.id]()}
        </div>
      ))}
    </div>
  );
}
