import { AudioLines, Settings, Users, type LucideIcon } from "lucide-react";
import { Icon } from "../ui/Icon";

export type Section = "recordings" | "voices" | "settings";

const ITEMS: { id: Section; label: string; icon: LucideIcon }[] = [
  { id: "recordings", label: "Записи", icon: AudioLines },
  { id: "voices", label: "Голоса", icon: Users },
  { id: "settings", label: "Настройки", icon: Settings },
];

/**
 * Разделы окна. В узком окне (до 1000 px) — полоса значков: подпись остаётся
 * доступным именем кнопки и всплывает подсказкой.
 */
export function Nav({ section, onSelect }: { section: Section; onSelect: (s: Section) => void }) {
  return (
    <nav className="nav" role="navigation">
      <div className="nav__brand" title="Meet"><span className="nav__mark" aria-hidden="true" /><span className="nav__label">Meet</span></div>
      {ITEMS.map((it) => (
        <button
          key={it.id}
          type="button"
          className="nav__item"
          title={it.label}
          aria-current={it.id === section ? "page" : undefined}
          onClick={() => onSelect(it.id)}
        >
          <Icon as={it.icon} className="nav__icon" />
          <span className="nav__label">{it.label}</span>
        </button>
      ))}
    </nav>
  );
}
