import { AudioLines, Settings, Users, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { MeetMark } from "../ui/MeetMark";
import { RailTip } from "./RailTip";
import "./rail.css";

export type Section = "recordings" | "voices" | "settings";

const LABEL: Record<Section, string> = { recordings: "Записи", voices: "Голоса", settings: "Настройки" };
const ICON: Record<Section, LucideIcon> = { recordings: AudioLines, voices: Users, settings: Settings };

/**
 * Рейка разделов (60 px, по макету MeetApp): знак Meet, под ним — кнопка записи
 * (`record`, app/RecordingBadge), разделы «Записи» и «Голоса» кнопками-значками,
 * внизу — предупреждения записи (`alerts`) и «Настройки». Подписи — доступные
 * имена кнопок и подсказки справа. `groups` — группы встреч под «Записи»
 * (features/groups/GroupsNav; уходят в список записей на этапе 3, Task 2).
 * Пустое место рейки перетаскивает окно (атрибут — только у самой рейки).
 */
export function Nav({ section, onSelect, groups, record, alerts }: {
  section: Section;
  onSelect: (s: Section) => void;
  groups?: ReactNode;
  record?: ReactNode;
  alerts?: ReactNode;
}) {
  const item = (id: Section) => {
    const current = id === section;
    return (
      <RailTip tip={LABEL[id]}>
        <Button variant={current ? "deep" : "ghost"} size="lg" className="btn--icon rail__item"
          aria-label={LABEL[id]} aria-current={current ? "page" : undefined} onClick={() => onSelect(id)}>
          <Icon as={ICON[id]} />
        </Button>
      </RailTip>
    );
  };
  return (
    <nav className="rail" aria-label="Разделы" data-tauri-drag-region>
      <div className="rail__mark" title="Meet"><MeetMark size={22} /></div>
      {record}
      <div className="rail__sep" aria-hidden="true" />
      {item("recordings")}
      {groups}
      {item("voices")}
      <div className="rail__foot">
        {alerts}
        {item("settings")}
      </div>
    </nav>
  );
}
