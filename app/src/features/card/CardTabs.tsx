/**
 * Вкладки готовой записи: «Транскрипт · Итоги · Вопросы».
 *
 * Вкладка монтируется при первом открытии и дальше живёт скрытой: ожидающий
 * вопрос и прокрутка не теряются при переключении, а итоги и вопросы не
 * запрашиваются у тех, кто их не открывал.
 */

import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { Endpoint } from "../../lib/api";
import type { Job } from "../../lib/types";
import { useAssistant } from "./assistant";
import { QaTab } from "./QaTab";
import { SummaryTab } from "./SummaryTab";
import "./assistant.css";

type Tab = "transcript" | "summary" | "qa";

const TABS: { id: Tab; label: string }[] = [
  { id: "transcript", label: "Транскрипт" },
  { id: "summary", label: "Итоги" },
  { id: "qa", label: "Вопросы" },
];

export function CardTabs({ endpoint, id, folder, jobs, transcript, onOpenSettings }: {
  endpoint: Endpoint;
  id: string;
  folder: string;
  jobs: Job[];
  transcript: ReactNode;
  onOpenSettings?: (section: string) => void;
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
    transcript: () => transcript,
    summary: () => <SummaryTab {...shared} />,
    qa: () => <QaTab {...shared} />,
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
          id={`${base}-${t.id}-panel`} aria-labelledby={`${base}-${t.id}`} hidden={tab !== t.id}>
          {opened.has(t.id) && panels[t.id]()}
        </div>
      ))}
    </div>
  );
}
