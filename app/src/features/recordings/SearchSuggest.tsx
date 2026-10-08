/**
 * Строка поиска по записям с подсказками — combobox по ARIA 1.2: фокус остаётся в поле,
 * текущий вариант — `aria-activedescendant`. Первый вариант — «Искать «…» в тексте» (то же,
 * что Enter), дальше — дописать префикс, участники (`GET /participants`, с задержкой), группы,
 * категории, толкования даты (lib/libraryQuery `suggestions`).
 *
 * Клавиатура: ↓/↑ — по вариантам (↓ и открывает список), Enter — выбрать вариант (список
 * закрыт — то же, что «Искать»: готовые префиксы — в метки), Esc — закрыть список, Tab —
 * уйти (список закрывается), Backspace в пустом поле — убрать последнюю метку.
 *
 * Список сам не открывается, когда в нём только «Искать «…» в тексте» (это и так Enter, а
 * список закрывал бы первые строки): ↓ открывает и его. Поле получило фокус с префиксом на
 * конце («участник:» от «ещё…» в «Фильтрах») — список сразу открыт. Пояснения («Не понял
 * дату…») видны в списке, но не выбираются (`aria-disabled`).
 */

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Search } from "lucide-react";
import { getParticipants, type Endpoint } from "../../lib/api";
import {
  nameMatches, personQuery, suggestions, type PersonOption, type QueryContext, type Suggestion,
} from "../../lib/libraryQuery";
import { CategoryDot } from "../../ui/Category";
import { Icon } from "../../ui/Icon";

/** Значение на конце — префикс без значения («бюджет участник:»): фокус сразу открывает список. */
const PREFIX_AT_END = /(?:^|\s)(?:участник|группа|категория|дата|после|до|есть|нет|дольше|короче):$/i;

/** Участники — не на каждую букву. */
export const PEOPLE_DELAY_MS = 200;
/** Сколько участников показывать: у `участник:` — больше, у простого слова — немного. */
const PEOPLE_FOR_PREFIX = 8;
const PEOPLE_FOR_WORD = 3;

export function SearchSuggest({ value, onChange, onApply, onBackspaceEmpty, endpoint, ctx, placeholder = "Поиск" }: {
  value: string;
  onChange: (text: string) => void;
  /** Выбран вариант или нажат Enter. */
  onApply: (action: Suggestion["action"]) => void;
  /** Backspace в пустом поле: убрать последнюю метку. */
  onBackspaceEmpty?: () => void;
  /** Откуда участники; нет резидента — без них. */
  endpoint: Endpoint | null;
  ctx: QueryContext;
  placeholder?: string;
}) {
  const listId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  /** Открыт стрелкой ↓: показывать и единственный вариант «Искать…». */
  const [forced, setForced] = useState(false);
  const [active, setActive] = useState(0);
  const [people, setPeople] = useState<{ q: string; items: PersonOption[] }>({ q: "", items: [] });

  // Участники под нынешнее слово: с задержкой, прежний запрос отменяется.
  const pq = open ? personQuery(value) : null;
  const prefixed = /(^|\s)участник:\S*$|(^|\s)участник:["«“][^"»”]*$/i.test(value);
  useEffect(() => {
    if (pq === null || !endpoint) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      getParticipants(endpoint, pq, prefixed ? PEOPLE_FOR_PREFIX : PEOPLE_FOR_WORD, controller.signal)
        .then((list) => {
          if (controller.signal.aborted) return;
          setPeople({ q: pq, items: list.map((p) => ({ name: p.name, meetings: p.meetings })) });
        })
        .catch(() => { /* подсказки — не повод для ошибки */ });
    }, PEOPLE_DELAY_MS);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [pq, prefixed, endpoint]);

  // Пока новый ответ не пришёл, из прежнего — только подходящие к набранному.
  const shownPeople = useMemo(() => (pq === null ? [] : people.items.filter((p) => nameMatches(p.name, pq))),
    [people, pq]);
  const options = useMemo(() => suggestions(value, ctx, shownPeople), [value, ctx, shownPeople]);
  const shown = open && (forced ? options.length > 0 : options.some((o) => o.kind !== "search"));
  const at = Math.min(active, options.length - 1);
  /** Следующий выбираемый вариант (пояснения пропускаются). */
  const step = (from: number, dir: 1 | -1) => {
    for (let i = 1; i <= options.length; i++) {
      const k = (from + dir * i + options.length * 2) % options.length;
      if (!options[k]!.disabled) return k;
    }
    return from;
  };
  const optionId = (i: number) => `${listId}-o${i}`;

  // Текущий вариант виден в прокручиваемом списке.
  useEffect(() => {
    if (!shown) return;
    document.getElementById(optionId(at))?.scrollIntoView?.({ block: "nearest" });
  });

  const apply = (s: Suggestion | undefined) => {
    if (s?.disabled) return;
    const action = s?.action ?? { type: "commit" as const };
    onApply(action);
    setActive(0);
    // Дописали префикс («категория:») — сразу его значения; иначе список закрывается.
    setOpen(action.type === "replace" && !action.chip && /:$/.test(action.text));
    setForced(false);
    input.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    // Набор через IME: Enter, завершающий слово, в WebKit приходит с isComposing=false, но keyCode 229.
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        if (!shown) { setOpen(true); setForced(true); setActive(0); } else setActive(step(at, 1));
        break;
      case "ArrowUp":
        if (!shown) return;
        e.preventDefault();
        setActive(step(at, -1));
        break;
      case "Enter":
        e.preventDefault();
        apply(shown ? options[at] : undefined);
        break;
      case "Escape":
        if (!shown) return;
        // Только список: поле не очищается, окна вокруг не закрываются.
        e.preventDefault();
        e.stopPropagation();
        setOpen(false);
        break;
      case "Tab":
        setOpen(false);
        break;
      case "Backspace":
        if (value === "" && onBackspaceEmpty) { e.preventDefault(); onBackspaceEmpty(); }
        break;
      default:
        break;
    }
  };

  return (
    // Поле — Aurora `.search` + `.field--md` (значок слева); рамка фокуса — одна, у `.field`.
    <div className="rec-search search">
      <Icon as={Search} />
      <input
        ref={input}
        type="search"
        className="field field--md rec-search__input"
        role="combobox"
        placeholder={placeholder}
        aria-description="Поиск по названиям и тексту расшифровок. Префиксы: участник:, группа:, категория:, дата:, есть:, дольше:…"
        aria-label="Поиск по записям"
        aria-autocomplete="list"
        aria-expanded={shown}
        aria-controls={listId}
        aria-activedescendant={shown ? optionId(at) : undefined}
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(e) => { onChange(e.target.value); setOpen(e.target.value !== ""); setForced(false); setActive(0); }}
        onFocus={() => { if (PREFIX_AT_END.test(value)) setOpen(true); }}
        onKeyDown={onKeyDown}
        onBlur={() => setOpen(false)}
      />
      {/* Список в разметке всегда (aria-controls указывает на существующее), пустой — скрыт. */}
      <ul id={listId} role="listbox" aria-label="Подсказки поиска" className="rec-suggest glass glass--dense" hidden={!shown}>
        {shown && options.map((o, i) => (
          <li key={o.id} id={optionId(i)} role="option" aria-selected={i === at} aria-disabled={o.disabled || undefined}
            className={`rec-suggest__option rec-suggest__option--${o.kind}${i === at ? " rec-suggest__option--active" : ""}`}
            // Щелчок не уводит фокус из поля: список не закрывается раньше выбора.
            onMouseDown={(e) => e.preventDefault()}
            onMouseMove={() => { if (i !== at && !o.disabled) setActive(i); }}
            onClick={() => apply(o)}>
            {o.color !== undefined && <CategoryDot color={o.color} />}
            <span className="rec-suggest__label">{o.label}</span>
            {o.detail && <span className="rec-suggest__detail">{o.detail}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
