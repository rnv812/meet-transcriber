/**
 * «Кому отдать»: спикер этой встречи, человек из базы голосов, новый человек,
 * «Это я» или новый безымянный спикер. Поле с поиском и список вариантов —
 * стрелки и Enter в поле или щелчок по варианту.
 */

import { CircleHelp, Plus, UserRound, type LucideIcon } from "lucide-react";
import { useId, useState, type KeyboardEvent } from "react";
import type { Endpoint } from "../../../lib/api";
import { isUnnamed } from "../../../lib/speakers";
import { Avatar } from "../../../ui/Avatar";
import { Icon } from "../../../ui/Icon";
import type { PersonColor } from "../Turns";

/** Сколько людей базы показывать без поиска. */
const PICK_MAX = 8;

const fold = (s: string) => s.trim().toLowerCase().replace(/ё/g, "е");
const hits = (name: string, q: string) =>
  !q || fold(name).startsWith(q) || fold(name).split(/\s+/).some((w) => w.startsWith(q));

type Option = { key: string; text: string; hint?: string; to: string | null; icon?: LucideIcon; avatar?: string };

/** `null` — новый безымянный «Спикер N» (номер выберет резидент). */
export type Target = string | null;

export function TargetPicker({
  label, speakers, current, people, owner, endpoint, avatarVersion, placeholder = "Имя или поиск", onPick,
}: {
  /** Подпись поля для чтения с экрана. */
  label: string;
  /** Спикеры этой встречи, в порядке появления. */
  speakers: string[];
  /** Нынешний спикер — его в списке нет. */
  current?: string | null;
  people: PersonColor[];
  owner: string;
  endpoint: Endpoint;
  avatarVersion?: Record<string, number>;
  placeholder?: string;
  onPick: (to: Target) => void;
}) {
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const q = fold(text);
  const typed = text.trim();

  const options: Option[] = [];
  const seen = new Set<string>();
  for (const s of speakers) {
    if (s === current || !hits(s, q)) continue;
    seen.add(s);
    options.push({ key: `s:${s}`, text: s, hint: "в этой встрече", to: s, avatar: s });
  }
  const base = people.filter((p) => !seen.has(p.name) && p.name !== current && hits(p.name, q))
    .sort((a, b) => a.name.localeCompare(b.name, "ru"));
  for (const p of base.slice(0, q ? 50 : PICK_MAX)) {
    seen.add(p.name);
    options.push({ key: `p:${p.name}`, text: p.name, hint: "в базе голосов", to: p.name, avatar: p.name });
  }
  const exact = [...speakers, ...people.map((p) => p.name), owner].some((n) => fold(n) === q);
  if (typed && !exact && !isUnnamed(typed)) {
    options.push({ key: "new", text: `Новый человек «${typed}»`, to: typed, icon: Plus });
  }
  if (owner !== current && !seen.has(owner) && (!q || hits(owner, q) || "это я".startsWith(q))) {
    options.push({ key: "me", text: `Это я — ${owner}`, to: owner, icon: UserRound });
  }
  if (!q || "новый спикер без имени".startsWith(q) || "неизвестный".startsWith(q)) {
    options.push({ key: "unnamed", text: "Новый спикер без имени", hint: "«Спикер N»", to: null, icon: CircleHelp });
  }
  const at = Math.min(active, Math.max(0, options.length - 1));

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((at + step + options.length) % Math.max(1, options.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const o = options[at];
      if (o) onPick(o.to);
    }
  };

  return (
    <div className="spk-pick tpick">
      <input className="field field--sm spk-pick__input" role="combobox" aria-label={label} autoFocus
        aria-expanded="true" aria-controls={listId} aria-autocomplete="list"
        aria-activedescendant={options[at] ? `${listId}-${at}` : undefined}
        placeholder={placeholder} value={text}
        onChange={(e) => { setText(e.target.value); setActive(0); }} onKeyDown={onKey} />
      <ul className="spk-pick__list" role="listbox" id={listId} aria-label="Варианты">
        {options.map((o, i) => {
          const person = o.avatar ? people.find((p) => p.name === o.avatar) : undefined;
          return (
            <li key={o.key} id={`${listId}-${i}`} role="option" aria-selected={i === at}
              className={`spk-opt${i === at ? " spk-opt--active" : ""}`}
              onMouseDown={(e) => e.preventDefault()} onClick={() => onPick(o.to)}>
              {o.avatar ? (
                <Avatar name={o.avatar} color={person?.color} hasAvatar={person?.has_avatar}
                  version={avatarVersion?.[o.avatar]} size={18} endpoint={endpoint} />
              ) : <span className="spk-opt__icon" aria-hidden="true">{o.icon && <Icon as={o.icon} size="sm" />}</span>}
              <span>{o.text}</span>
              {o.hint && <span className="muted spk-opt__hint">{o.hint}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
