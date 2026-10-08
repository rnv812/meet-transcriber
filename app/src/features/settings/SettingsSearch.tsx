/**
 * «Поиск по настройкам» над меню (0.4): поле Aurora `.search` + `field--sm`;
 * пока в нём текст, вместо меню — выдача «Раздел › Параметр» из
 * `SETTINGS_INDEX`. Клавиши: ↑/↓ — по выдаче, Enter — открыть выбранное, Esc —
 * очистить. Поле — combobox, выдача — listbox (как поиск по записям). Что
 * делать с выбранной строкой (открыть раздел, раскрыть «Тонкую настройку»,
 * прокрутить и подсветить), решает SettingsPane (`onPick`).
 */

import { useId, useMemo, useState, type KeyboardEvent, type RefObject } from "react";
import { Search } from "lucide-react";
import { IS_MAC } from "../../lib/platform";
import { Icon } from "../../ui/Icon";
import { searchSettings, sectionTitle, type SettingsEntry } from "./settingsIndex";

export function SettingsSearch({ query, onQuery, onPick, inputRef }: {
  query: string;
  onQuery: (q: string) => void;
  onPick: (entry: SettingsEntry) => void;
  inputRef?: RefObject<HTMLInputElement | null>;
}) {
  const listId = useId();
  const results = useMemo(() => searchSettings(query), [query]);
  const [active, setActive] = useState(0);
  // Новый запрос — выбор снова на первой строке выдачи.
  const [shownFor, setShownFor] = useState(query);
  if (shownFor !== query) {
    setShownFor(query);
    setActive(0);
  }
  const typed = query.trim() !== "";
  const at = Math.min(active, Math.max(results.length - 1, 0));
  const optionId = (i: number) => `${listId}-${i}`;

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      if (!query) return;
      e.preventDefault();
      e.stopPropagation();
      onQuery("");
      return;
    }
    if (!typed || results.length === 0) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((at + step + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      onPick(results[at]!);
    }
  };

  return (
    <div className="ssearch">
      <div className="search">
        <Icon as={Search} />
        <input ref={inputRef} type="search" className="field field--sm ssearch__input"
          role="combobox" aria-label="Поиск по настройкам" placeholder="Поиск"
          aria-autocomplete="list" aria-expanded={typed && results.length > 0} aria-controls={listId}
          aria-activedescendant={typed && results.length > 0 ? optionId(at) : undefined}
          autoComplete="off" spellCheck={false} value={query}
          onChange={(e) => onQuery(e.target.value)} onKeyDown={onKeyDown} />
        {!typed && <kbd className="kbd" aria-hidden="true">{IS_MAC ? "⌘F" : "Ctrl+F"}</kbd>}
      </div>
      {/* Выдача в разметке всегда (aria-controls указывает на существующее), пустая — скрыта. */}
      <ul id={listId} role="listbox" aria-label="Найденные настройки" className="ssearch__list"
        hidden={!typed || results.length === 0}>
        {typed && results.map((entry, i) => (
          <li key={`${entry.section}-${entry.label}`} id={optionId(i)} role="option" aria-selected={i === at}
            className="ssearch__item" onMouseDown={(e) => e.preventDefault()} onMouseMove={() => setActive(i)}
            onClick={() => onPick(entry)}>
            <span className="ssearch__section">{sectionTitle(entry.section)}</span>{" › "}
            <span className="ssearch__label">{entry.label}</span>
          </li>
        ))}
      </ul>
      {typed && results.length === 0 && <p className="muted ssearch__empty" role="status">Ничего не найдено</p>}
    </div>
  );
}
