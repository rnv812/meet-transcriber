/**
 * Панель «Спикеры встречи» сбоку карточки: кто сколько говорил, как звучит
 * (▶ у фраз), на кого из базы голосов похож — и назначение имён.
 *
 * Правки копятся в панели (`staging.ts`) и применяются одним набором — это
 * шаг истории встречи (резидент хранит его в meta.json записи).
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { applySpeakers, getSpeakers, type Endpoint } from "../../../lib/api";
import { clock, errorText, plural } from "../../../lib/format";
import { isUnnamed } from "../../../lib/speakers";
import type { SpeakerRow, SpeakersView } from "../../../lib/types";
import { Avatar } from "../../../ui/Avatar";
import { Button } from "../../../ui/Button";
import { HelpTip, TipLine } from "../../../ui/HelpTip";
import type { PersonColor } from "../Turns";
import {
  changeText, finalOf, preview, prune, rememberDefault, toOps, type Change, type Staged,
} from "./staging";
import "./speakers.css";

/** Сколько секунд играет фраза по ▶. */
export const PHRASE_S = 6;
/** Сколько людей базы показывать в списке «Кто это?». */
const PICK_MAX = 8;

const fold = (s: string) => s.trim().toLowerCase().replace(/ё/g, "е");
const pct = (x: number) => `${Math.round(x * 100)}%`;

type Pick = { label: string; mode: "assign" | "merge" };
type Option = { key: string; text: string; hint?: string; change: Change; person?: PersonColor };

export function SpeakersPanel({
  endpoint, recordingId, people, avatarVersion, open, focus, version, playable,
  onClose, onPlay, onShowTurns, onChanged,
}: {
  endpoint: Endpoint;
  recordingId: string;
  people: PersonColor[];
  avatarVersion?: Record<string, number>;
  open: boolean;
  /** Строка, к которой перейти (клик по участнику); `n` растёт с каждой просьбой. */
  focus: { label: string; n: number } | null;
  /** Меняется вместе с расшифровкой: перечитать спикеров. */
  version: unknown;
  playable: boolean;
  onClose: () => void;
  onPlay: (start: number, until: number) => void;
  /** «Показать все реплики» этого спикера в расшифровке. */
  onShowTurns: (label: string) => void;
  /** Правки применены: перечитать запись и базу голосов. */
  onChanged: () => void;
}) {
  const [view, setView] = useState<SpeakersView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inflight = useRef(false);
  const [staged, setStaged] = useState<Staged>({});
  const [remember, setRemember] = useState<Record<string, boolean>>({});
  const [pick, setPick] = useState<Pick | null>(null);
  const rows = useRef<Record<string, HTMLElement | null>>({});
  const current = useRef({ endpoint, recordingId });
  current.current = { endpoint, recordingId };

  const show = useCallback((next: SpeakersView) => {
    setView(next);
    const labels = next.speakers.map((r) => r.label);
    setStaged((s) => prune(s, labels));
  }, []);

  const load = useCallback(async () => {
    try {
      const next = await getSpeakers(endpoint, recordingId);
      if (current.current.endpoint !== endpoint || current.current.recordingId !== recordingId) return;
      show(next);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [endpoint, recordingId, show]);

  useEffect(() => { void load(); }, [load, version]);

  const order = useMemo(() => view?.speakers.map((r) => r.label) ?? [], [view]);
  const owner = view?.owner ?? "Вы";
  const pending = Object.keys(staged).length > 0;

  const run = useCallback(async (call: () => Promise<SpeakersView>, done?: string) => {
    if (inflight.current) return;
    inflight.current = true;
    setBusy(true);
    setError(null);
    setWarning(null);
    setNotice(null);
    try {
      const next = await call();
      show(next);
      if (next.voices_error) setWarning(next.voices_error);
      if (done) setNotice(done);
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  }, [show, onChanged]);

  // Клик по участнику: прокрутить к его строке и поставить на неё фокус.
  useEffect(() => {
    if (!open || !focus || !view) return;
    const el = rows.current[focus.label];
    el?.scrollIntoView?.({ block: "nearest" });
    el?.focus({ preventScroll: true });
  }, [open, focus, view]);

  const stage = (label: string, change: Change | null) => {
    setStaged((s) => {
      const next = { ...s };
      const noop = change === null
        || (change.kind === "rename" && change.to === label)
        || (change.kind === "reset" && isUnnamed(label));
      if (noop) delete next[label];
      else next[label] = change;
      return next;
    });
    setRemember((r) => {
      const next = { ...r };
      delete next[label];
      return next;
    });
    setPick(null);
    setNotice(null);
  };

  const rememberOf = (row: SpeakerRow) => remember[row.label] ?? rememberDefault(row, staged, owner);
  const canRemember = (row: SpeakerRow) => {
    if (!row.has_voice || !staged[row.label]) return false;
    const final = finalOf(row.label, staged);
    return final !== null && !isUnnamed(final);
  };

  const apply = () => {
    if (!view || !pending) return;
    const flags: Record<string, boolean> = {};
    for (const row of view.speakers) if (staged[row.label] && canRemember(row)) flags[row.label] = rememberOf(row);
    void run(async () => {
      const next = await applySpeakers(endpoint, recordingId, toOps(order, staged), flags);
      setStaged({});
      setRemember({});
      return next;
    }, "Изменения применены. Итоги не пересчитываются автоматически.");
  };
  const discard = () => { setStaged({}); setRemember({}); setPick(null); };

  const onPanelKey = (e: KeyboardEvent) => {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    e.preventDefault();
    if (pick) setPick(null);
    else onClose();
  };

  return (
    <aside className="spk" role="dialog" aria-modal="false" aria-label="Спикеры встречи" hidden={!open}
      onKeyDown={onPanelKey}>
      <div className="spk__head">
        <h3 className="spk__title">Спикеры встречи</h3>
        <div className="spk__tools">
          <button type="button" className="spk__close" onClick={onClose} aria-label="Закрыть панель спикеров"
            title="Закрыть (Esc)">×</button>
        </div>
      </div>
      {error && <div className="card__error spk__msg" role="alert">{error}</div>}
      {warning && <div className="spk__msg spk__warn" role="status">Голос не сохранён: {warning}</div>}
      {notice && <div className="spk__msg spk__ok muted" role="status">{notice}</div>}
      <div className="spk__body">
        {!view && !error && <div className="muted spk__msg">Загрузка…</div>}
        {view && view.speakers.length === 0 && <div className="muted spk__msg">В расшифровке нет спикеров</div>}
        {view?.speakers.map((row) => (
          <SpeakerRowView key={row.label} row={row} rows={view.speakers} staged={staged} owner={owner}
            people={people} avatarVersion={avatarVersion} endpoint={endpoint} playable={playable}
            focused={focus?.label === row.label}
            pick={pick?.label === row.label ? pick.mode : null}
            remember={canRemember(row) ? rememberOf(row) : null}
            refEl={(el) => { rows.current[row.label] = el; }}
            onPick={(mode) => setPick((p) => (p?.label === row.label && p.mode === mode ? null : { label: row.label, mode }))}
            onStage={(c) => stage(row.label, c)}
            onRemember={(v) => setRemember((r) => ({ ...r, [row.label]: v }))}
            onPlay={onPlay} onShowTurns={onShowTurns} />
        ))}
      </div>
      {pending && (
        <div className="spk__foot">
          <div className="spk__preview" aria-live="polite">{preview(order, staged)}</div>
          <div className="spk__note muted">Итоги не пересчитываются автоматически</div>
          <div className="spk__actions">
            <Button onClick={discard} disabled={busy}>Сбросить</Button>
            <Button variant="primary" onClick={apply} disabled={busy}>Применить</Button>
          </div>
        </div>
      )}
    </aside>
  );
}

function SpeakerRowView({
  row, rows, staged, owner, people, avatarVersion, endpoint, playable, focused, pick, remember, refEl,
  onPick, onStage, onRemember, onPlay, onShowTurns,
}: {
  row: SpeakerRow;
  rows: SpeakerRow[];
  staged: Staged;
  owner: string;
  people: PersonColor[];
  avatarVersion?: Record<string, number>;
  endpoint: Endpoint;
  playable: boolean;
  focused: boolean;
  pick: Pick["mode"] | null;
  /** null — флажка нет (нечего запоминать). */
  remember: boolean | null;
  refEl: (el: HTMLElement | null) => void;
  onPick: (mode: Pick["mode"]) => void;
  onStage: (c: Change | null) => void;
  onRemember: (v: boolean) => void;
  onPlay: (start: number, until: number) => void;
  onShowTurns: (label: string) => void;
}) {
  const person = people.find((p) => p.name === row.label);
  const change = staged[row.label];
  const titleId = useId();
  return (
    <section ref={refEl} tabIndex={-1} aria-labelledby={titleId}
      className={`spk-row${focused ? " spk-row--focus" : ""}${change ? " spk-row--staged" : ""}`}>
      <div className="spk-row__head">
        <Avatar name={row.label} color={person?.color} hasAvatar={person?.has_avatar}
          version={avatarVersion?.[row.label]} size={32} endpoint={endpoint} />
        <div className="spk-row__who">
          <div className="spk-row__name" id={titleId}>
            <span className={isUnnamed(row.label) ? "spk-row__label--unnamed" : ""}>{row.label}</span>
            {change && <span className="spk-row__change">{changeText(row.label, staged).slice(row.label.length + 1)}</span>}
          </div>
          <div className="spk-row__stats muted num">
            {pct(row.share)} времени · {row.turns} {plural(row.turns, "реплика", "реплики", "реплик")}
          </div>
          <div className="spk-row__bar" aria-hidden="true"><span style={{ width: pct(row.share) }} /></div>
        </div>
      </div>

      {row.samples.length > 0 && (
        <ul className="spk-row__phrases" aria-label="Фразы спикера">
          {row.samples.map((s) => (
            <li key={s.start} className="spk-phrase">
              <button type="button" className="spk-phrase__play" disabled={!playable}
                aria-label={`Прослушать фразу с ${clock(s.start)}`}
                onClick={() => onPlay(s.start, Math.min(s.end, s.start + PHRASE_S))}>▶</button>
              <span className="spk-phrase__time num muted">{clock(s.start)}</span>
              <span className="spk-phrase__text">{s.text}</span>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="spk-link" onClick={() => onShowTurns(row.label)}>Показать все реплики</button>

      {row.suggestions.length > 0 && (
        <div className="spk-row__sugs" role="group" aria-label="Похожие голоса из базы">
          <span className="muted">Похож на:</span>
          {row.suggestions.map((s) => {
            const p = people.find((x) => x.name === s.name);
            const same = finalOf(row.label, staged) === s.name;
            return (
              <button key={s.name} type="button" className={`spk-sug${same ? " spk-sug--on" : ""}`}
                aria-pressed={same} aria-label={`Это ${s.name}, сходство ${pct(s.score)}`}
                onClick={() => onStage(same ? null : { kind: "rename", to: s.name })}>
                <Avatar name={s.name} color={p?.color} hasAvatar={p?.has_avatar} version={avatarVersion?.[s.name]}
                  size={18} endpoint={endpoint} />
                <span>{s.name}</span>
                <span className="spk-sug__score num">{pct(s.score)}</span>
              </button>
            );
          })}
        </div>
      )}

      <div className="spk-row__actions">
        <button type="button" className="spk-btn" aria-expanded={pick === "assign"} onClick={() => onPick("assign")}>
          Назначить…
        </button>
        {rows.length > 1 && (
          <button type="button" className="spk-btn" aria-expanded={pick === "merge"} onClick={() => onPick("merge")}>
            Объединить с…
          </button>
        )}
        {change && <button type="button" className="spk-link" onClick={() => onStage(null)}>Убрать правку</button>}
      </div>

      {pick === "assign" && (
        <AssignPicker row={row} owner={owner} people={people} suggestions={row.suggestions.map((s) => s.name)}
          avatarVersion={avatarVersion} endpoint={endpoint} onStage={onStage} />
      )}
      {pick === "merge" && (
        <div className="spk-pick" role="group" aria-label={`Объединить «${row.label}» с другим спикером`}>
          <div className="spk-pick__title">
            Тот же человек, что и…
            <HelpTip label="Что значит «Объединить»" title="Объединить спикеров">
              <TipLine>Бывает, что разделение на спикеров приняло одного человека за двоих.</TipLine>
              <TipLine>Объединение переносит все реплики этой строки к выбранному спикеру: дальше это один участник.</TipLine>
              <TipLine>Изменение применяется вместе с остальными по кнопке «Применить» и отменяется кнопкой «Отменить».</TipLine>
            </HelpTip>
          </div>
          {rows.filter((r) => r.label !== row.label).map((r) => (
            <button key={r.label} type="button" className="spk-opt" onClick={() => onStage({ kind: "merge", into: r.label })}>
              <Avatar name={r.label} size={18} endpoint={endpoint} />
              <span>{r.label}</span>
              <span className="muted num">{pct(r.share)}</span>
            </button>
          ))}
        </div>
      )}

      {remember !== null && (
        <label className="spk-row__remember">
          <input type="checkbox" checked={remember} onChange={(e) => onRemember(e.target.checked)} />
          Запомнить голос
          <HelpTip label="Что значит «Запомнить голос»" title="Запомнить голос">
            <TipLine>Голосовой отпечаток этого спикера попадёт в базу голосов под выбранным именем.</TipLine>
            <TipLine>На следующих встречах человек будет узнан автоматически.</TipLine>
            <TipLine>Отмена изменения убирает и запомненный голос.</TipLine>
          </HelpTip>
        </label>
      )}
    </section>
  );
}

/** «Кто это?»: поиск по базе голосов, новый человек, «Это я» и «Неизвестный». */
function AssignPicker({ row, owner, people, suggestions, avatarVersion, endpoint, onStage }: {
  row: SpeakerRow;
  owner: string;
  people: PersonColor[];
  suggestions: string[];
  avatarVersion?: Record<string, number>;
  endpoint: Endpoint;
  onStage: (c: Change) => void;
}) {
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const q = fold(text);
  const typed = text.trim();

  const options: Option[] = [];
  const ranked = [...people].sort((a, b) => {
    const sa = suggestions.indexOf(a.name), sb = suggestions.indexOf(b.name);
    return (sa < 0 ? 99 : sa) - (sb < 0 ? 99 : sb) || a.name.localeCompare(b.name, "ru");
  });
  const matches = ranked.filter((p) => !q || fold(p.name).split(/\s+/).some((w) => w.startsWith(q)) || fold(p.name).startsWith(q));
  for (const p of matches.slice(0, PICK_MAX)) {
    options.push({ key: `p:${p.name}`, text: p.name, hint: "в базе голосов", change: { kind: "rename", to: p.name }, person: p });
  }
  if (typed && !people.some((p) => fold(p.name) === q) && fold(owner) !== q) {
    options.push({ key: "new", text: `Новый человек «${typed}»`, change: { kind: "rename", to: typed } });
  }
  if (row.label !== owner && (!q || fold(owner).startsWith(q) || "это я".startsWith(q))) {
    options.push({ key: "me", text: `Это я — ${owner}`, change: { kind: "rename", to: owner } });
  }
  if (!q || "неизвестный".startsWith(q)) {
    options.push({ key: "unknown", text: "Неизвестный", hint: "снять имя", change: { kind: "reset" } });
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
      if (o) onStage(o.change);
    }
  };

  return (
    <div className="spk-pick">
      <input className="spk-pick__input" role="combobox" aria-label={`Кто это: ${row.label}`} autoFocus
        aria-expanded="true" aria-controls={listId} aria-autocomplete="list"
        aria-activedescendant={options[at] ? `${listId}-${at}` : undefined}
        placeholder="Имя или поиск по базе голосов" value={text}
        onChange={(e) => { setText(e.target.value); setActive(0); }} onKeyDown={onKey} />
      <ul className="spk-pick__list" role="listbox" id={listId} aria-label="Варианты">
        {options.map((o, i) => (
          // Мышью — щелчок по варианту, с клавиатуры — стрелки и Enter в поле (aria-activedescendant).
          <li key={o.key} id={`${listId}-${i}`} role="option" aria-selected={i === at}
            className={`spk-opt${i === at ? " spk-opt--active" : ""}`}
            onMouseDown={(e) => e.preventDefault()} onClick={() => onStage(o.change)}>
            {o.person ? (
              <Avatar name={o.person.name} color={o.person.color} hasAvatar={o.person.has_avatar}
                version={avatarVersion?.[o.person.name]} size={18} endpoint={endpoint} />
            ) : <span className="spk-opt__icon" aria-hidden="true">{o.key === "new" ? "＋" : o.key === "me" ? "●" : "?"}</span>}
            <span>{o.text}</span>
            {o.hint && <span className="muted spk-opt__hint">{o.hint}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
