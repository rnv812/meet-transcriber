export type Section = "recordings" | "voices" | "settings";

const ITEMS: { id: Section; label: string }[] = [
  { id: "recordings", label: "Записи" },
  { id: "voices", label: "Голоса" },
  { id: "settings", label: "Настройки" },
];

export function Nav({ section, onSelect }: { section: Section; onSelect: (s: Section) => void }) {
  return (
    <nav className="nav" role="navigation">
      <div className="nav__brand"><span className="nav__mark" aria-hidden="true" />Meet</div>
      {ITEMS.map((it) => (
        <button
          key={it.id}
          type="button"
          className="nav__item"
          aria-current={it.id === section ? "page" : undefined}
          onClick={() => onSelect(it.id)}
        >
          {it.label}
        </button>
      ))}
    </nav>
  );
}
