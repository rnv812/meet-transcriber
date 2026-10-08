import { AudioLines, Settings, Users, type LucideIcon } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import { Icon } from "../ui/Icon";
import { MeetMark } from "../ui/MeetMark";

export type Section = "recordings" | "voices" | "settings";

const ITEMS: { id: Section; label: string; icon: LucideIcon }[] = [
  { id: "recordings", label: "Записи", icon: AudioLines },
  { id: "voices", label: "Голоса", icon: Users },
  { id: "settings", label: "Настройки", icon: Settings },
];

/**
 * Разделы окна. В узком окне (до 1000 px) — полоса значков: подпись остаётся
 * доступным именем кнопки и всплывает подсказкой. `groups` — группы встреч
 * под «Записи» (features/groups/GroupsNav).
 */
export function Nav({ section, onSelect, groups }: {
  section: Section;
  onSelect: (s: Section) => void;
  groups?: ReactNode;
}) {
  return (
    <nav className="nav" role="navigation">
      <div className="nav__brand" title="Meet"><span className="nav__mark" aria-hidden="true"><MeetMark size={18} /></span><span className="nav__label">Meet</span></div>
      {ITEMS.map((it) => (
        <Fragment key={it.id}>
          <button
            type="button"
            className="nav__item"
            title={it.label}
            aria-current={it.id === section ? "page" : undefined}
            onClick={() => onSelect(it.id)}
          >
            <Icon as={it.icon} className="nav__icon" />
            <span className="nav__label">{it.label}</span>
          </button>
          {it.id === "recordings" && groups}
        </Fragment>
      ))}
    </nav>
  );
}
