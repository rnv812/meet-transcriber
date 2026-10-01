/**
 * «Программы звонков» для автозаписи: пресеты с понятными именами, свои
 * программы чипами и «Добавить программу…» с поиском по запущенным.
 *
 * Хранится по-прежнему список имён exe (`auto_record.processes`): пресет —
 * лишь способ отметить сразу все exe одного клиента. Детектор сравнивает имена
 * без учёта регистра, поэтому и здесь сравнение такое же.
 */

import { useEffect, useId, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import type { Processes } from "../../lib/api";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";

export type CallProgram = { id: string; title: string; exes: string[]; messenger?: boolean };

/**
 * Известные клиенты. Имена exe сверены по документации и описаниям процессов;
 * где у клиента несколько вариантов установки, перечислены все — лишнее имя
 * безвредно (детектор ищет точное совпадение).
 */
export const CALL_PROGRAMS: CallProgram[] = [
  { id: "zoom", title: "Zoom", exes: ["Zoom.exe"] },
  // Новый Teams — ms-teams.exe, классический — Teams.exe.
  { id: "teams", title: "Microsoft Teams", exes: ["ms-teams.exe", "Teams.exe"] },
  // Телемост в составе Яндекс Диска — YandexTelemost.exe (подтверждено).
  // Telemost.exe — кандидат для отдельной установки (MSI для организаций),
  // имя не подтверждено.
  { id: "telemost", title: "Яндекс Телемост", exes: ["YandexTelemost.exe", "Telemost.exe"] },
  { id: "dion", title: "Dion", exes: ["Dion.exe"] },
  // Webex App: звук встречи ведёт CiscoCollabHost.exe; Webex.exe был в
  // списке по умолчанию; классический Webex Meetings — webexmta.exe и atmgr.exe.
  { id: "webex", title: "Webex", exes: ["Webex.exe", "CiscoCollabHost.exe", "webexmta.exe", "atmgr.exe"] },
  { id: "telegram", title: "Telegram", exes: ["Telegram.exe"], messenger: true },
  { id: "discord", title: "Discord", exes: ["Discord.exe"], messenger: true },
  { id: "slack", title: "Slack", exes: ["slack.exe"], messenger: true },
  { id: "skype", title: "Skype", exes: ["Skype.exe"], messenger: true },
  // С конца 2025 WhatsApp для Windows — оболочка WebView2 (WhatsApp.Root.exe);
  // прежнее приложение — WhatsApp.exe.
  { id: "whatsapp", title: "WhatsApp", exes: ["WhatsApp.exe", "WhatsApp.Root.exe"], messenger: true },
  { id: "viber", title: "Viber", exes: ["Viber.exe"], messenger: true },
];

const MAX_OPTIONS = 50;
const BAD_CHARS = /[\\/:*?"<>|]/;

const lower = (s: string) => s.toLowerCase();
const PRESET_EXES = new Set(CALL_PROGRAMS.flatMap((p) => p.exes.map(lower)));

/** Ошибка в имени программы или null, если имя годится. */
export function exeProblem(name: string): string | null {
  if (!name) return null;
  if (BAD_CHARS.test(name)) return "Имя программы — без пути и символов \\ / : * ? \" < > |";
  if (!lower(name).endsWith(".exe") || name.length <= 4) return "Имя программы должно оканчиваться на .exe";
  return null;
}

function PresetBox({ program, selected, onToggle }: {
  program: CallProgram; selected: Set<string>; onToggle: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const count = program.exes.filter((e) => selected.has(lower(e))).length;
  const all = count === program.exes.length;
  const some = count > 0 && !all;
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = some;
  }, [some]);
  return (
    <label className="callapps__item">
      <input ref={ref} type="checkbox" checked={all} aria-label={program.title} onChange={onToggle} />
      <span className="callapps__name">
        <span>{program.title}</span>
        <span className="callapps__exes">{program.exes.join(", ")}</span>
      </span>
    </label>
  );
}

function AddProgram({ processes: initial, loadProcesses, selected, onAdd, onClose }: {
  processes: Processes | null; loadProcesses?: () => Promise<Processes>; selected: Set<string>;
  onAdd: (name: string) => void; onClose: () => void;
}) {
  // Список запущенных — свежий на каждое открытие: пользователь мог только что
  // запустить программу звонков. До ответа — то, что было при открытии настроек.
  const [processes, setProcesses] = useState(initial);
  useEffect(() => {
    if (!loadProcesses) return;
    let current = true;
    loadProcesses().then((p) => { if (current) setProcesses(p); }).catch(() => {});
    return () => { current = false; };
  }, [loadProcesses]);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(-1);
  const listId = useId();
  const q = lower(query.trim());
  const seen = new Set<string>();
  const options = (processes?.running ?? []).filter((name) => {
    const key = lower(name);
    if (selected.has(key) || seen.has(key) || (q && !key.includes(q))) return false;
    seen.add(key);
    return true;
  }).slice(0, MAX_OPTIONS);
  const typed = query.trim();
  const problem = exeProblem(typed);
  const expanded = options.length > 0;

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, options.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const option = options[active];
      if (option) onAdd(option);
      else if (typed && !problem) onAdd(typed);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <div className="callapps__add">
      <span className="with-unit">
        <input type="text" role="combobox" aria-label="Программа" aria-expanded={expanded} aria-controls={listId}
          aria-autocomplete="list" aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
          placeholder="Поиск по запущенным или имя.exe" autoFocus value={query}
          onChange={(e) => { setQuery(e.target.value); setActive(-1); }} onKeyDown={onKey} />
        <Button onClick={onClose}>Отмена</Button>
      </span>
      {typed && problem && options.length === 0 && <span className="muted callapps__note">{problem}</span>}
      {typed && !problem && !options.some((o) => lower(o) === lower(typed)) && (
        <span className="muted callapps__note">Нажмите Enter, чтобы добавить «{typed}»</span>
      )}
      {processes && !processes.available && (
        <span className="muted callapps__note">Список запущенных программ недоступен{processes.error ? `: ${processes.error}` : ""}</span>
      )}
      <ul id={listId} role="listbox" aria-label="Запущенные программы" className="callapps__options" hidden={!expanded}>
        {options.map((name, i) => (
          <li key={name} id={`${listId}-${i}`} role="option" aria-selected={i === active}
            className={`callapps__option${i === active ? " callapps__option--active" : ""}`}
            onMouseDown={(e) => e.preventDefault()} onClick={() => onAdd(name)}>
            {name}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AddButton({ buttonRef, onClick }: { buttonRef: RefObject<HTMLButtonElement | null>; onClick: () => void }) {
  return <div><Button ref={buttonRef} onClick={onClick}>Добавить программу…</Button></div>;
}

export function CallPrograms({ value, processes, loadProcesses, onChange }: {
  value: string[]; processes: Processes | null;
  /** Перечитать запущенные программы — при каждом открытии поиска. */
  loadProcesses?: () => Promise<Processes>;
  onChange: (v: string[]) => void;
}) {
  const [adding, setAdding] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);
  // Поиск закрылся (добавили или Esc) — фокус обратно на «Добавить программу…».
  const wasAdding = useRef(false);
  useEffect(() => {
    if (wasAdding.current && !adding) addButton.current?.focus();
    wasAdding.current = adding;
  }, [adding]);
  const selected = new Set(value.map(lower));
  const custom = value.filter((name) => !PRESET_EXES.has(lower(name)));

  const toggle = (program: CallProgram) => {
    const exes = new Set(program.exes.map(lower));
    const all = program.exes.every((e) => selected.has(lower(e)));
    onChange(all
      ? value.filter((name) => !exes.has(lower(name)))
      : [...value, ...program.exes.filter((e) => !selected.has(lower(e)))]);
  };
  const add = (name: string) => {
    if (!selected.has(lower(name))) onChange([...value, name]);
    setAdding(false);
  };
  const remove = (name: string) => onChange(value.filter((n) => n !== name));

  const group = (messengers: boolean) => CALL_PROGRAMS.filter((p) => Boolean(p.messenger) === messengers)
    .map((p) => <PresetBox key={p.id} program={p} selected={selected} onToggle={() => toggle(p)} />);

  return (
    <div className="callapps" role="group" aria-label="Программы звонков">
      <div className="callapps__head">
        <span className="srow__label">Программы звонков</span>
        <HelpTip label="Какие звонки распознаются" title="Как распознаётся звонок">
          <TipLine>Запись начинается, когда отмеченная программа использует микрофон или воспроизводит звук.</TipLine>
          <TipLine>
            Звонки в браузере (Google Meet, веб-версии Zoom, Teams и Телемоста) распознаются по микрофону —
            браузеры отмечаются ниже, в группе «Звонки в браузере».
          </TipLine>
        </HelpTip>
      </div>
      <span className="srow__hint">Отметьте программы, в которых вы созваниваетесь</span>
      <div className="callapps__grid">{group(false)}</div>
      <div className="callapps__head callapps__head--sub">
        <span className="srow__hint">Мессенджеры</span>
        <HelpTip label="Почему осторожно с мессенджерами" title="Мессенджеры">
          <TipLine>
            Мессенджеры воспроизводят звуки уведомлений, и приложение может принять их за начало звонка.
            Короткие записи сохраняются, но не расшифровываются автоматически.
          </TipLine>
        </HelpTip>
      </div>
      <div className="callapps__grid">{group(true)}</div>
      {custom.length > 0 && (
        <ul className="callapps__chips" aria-label="Другие программы">
          {custom.map((name) => (
            <li key={name} className="chip">
              <span>{name}</span>
              <button type="button" className="chip__remove" aria-label={`Убрать ${name}`} onClick={() => remove(name)}>×</button>
            </li>
          ))}
        </ul>
      )}
      {adding
        ? <AddProgram processes={processes} loadProcesses={loadProcesses} selected={selected} onAdd={add}
            onClose={() => setAdding(false)} />
        : <AddButton buttonRef={addButton} onClick={() => setAdding(true)} />}
      {value.length === 0 && (
        <p className="muted callapps__note">
          Не выбрано ни одной программы — будет использован список по умолчанию.
        </p>
      )}
    </div>
  );
}
