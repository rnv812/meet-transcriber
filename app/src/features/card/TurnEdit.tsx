/**
 * Правка спикера прямо в расшифровке: щелчок по имени у реплики — меню
 * «Кому отдать» (только эта реплика или эта и следующие подряд того же
 * спикера), Ctrl+щелчок и Shift+щелчок по репликам — выбор нескольких и
 * «Назначить выбранные…». Каждое назначение — шаг истории встречи: его
 * отменяет «Отменить» здесь же или в панели «Спикеры».
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { relabelTurns, undoSpeakers, type Endpoint } from "../../lib/api";
import { clock, errorText, plural } from "../../lib/format";
import { NO_SPEAKER, runFrom, speakersOf, type Turn } from "../../lib/speakers";
import type { Segment } from "../../lib/types";
import { Popover } from "../../ui/Popover";
import { TargetPicker, type Target } from "./speakers/TargetPicker";
import type { PersonColor } from "./Turns";

type Menu = { anchor: HTMLElement; turn: number | null };
type Done = { text: string; undo: boolean };

const turnsWord = (n: number) => plural(n, "реплика", "реплики", "реплик");

export type TurnEdit = {
  selected: ReadonlySet<number>;
  onSelect: (turn: number, how: "toggle" | "range") => void;
  onSpeaker: (turn: number, anchor: HTMLElement) => void;
  /** Полоса над репликами: сколько выбрано, итог назначения с «Отменить». */
  bar: ReactNode;
  /** Меню «Кому отдать» у якоря. */
  menu: ReactNode;
};

export function useTurnEdit({
  endpoint, id, turns, segments, people, owner, avatarVersion, onOpenPanel, onChanged,
}: {
  endpoint: Endpoint;
  id: string;
  turns: Turn[];
  segments: Segment[];
  people: PersonColor[];
  owner: string;
  avatarVersion?: Record<string, number>;
  /** «Все реплики спикера — в панели спикеров». */
  onOpenPanel: (label: string) => void;
  /** Назначено или отменено: перечитать запись. */
  onChanged: () => void;
}): TurnEdit {
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [anchorTurn, setAnchorTurn] = useState<number | null>(null);
  const [menu, setMenu] = useState<Menu | null>(null);
  const [scope, setScope] = useState<"one" | "run">("one");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Done | null>(null);

  // Расшифровку перечитали — номера реплик могли сдвинуться.
  useEffect(() => { setSelected(new Set()); setAnchorTurn(null); setMenu(null); }, [segments]);
  useEffect(() => { setDone(null); }, [id]);

  const meeting = useMemo(() => speakersOf(segments), [segments]);
  const editable = (t: number) => {
    const turn = turns[t];
    return !!turn && turn.kind !== "break" && turn.speaker !== NO_SPEAKER;
  };

  const onSelect = useCallback((t: number, how: "toggle" | "range") => {
    if (!editable(t)) return;
    setDone(null);
    setSelected((cur) => {
      const next = new Set(cur);
      if (how === "range" && anchorTurn !== null) {
        const [a, b] = anchorTurn < t ? [anchorTurn, t] : [t, anchorTurn];
        for (let i = a; i <= b; i++) if (editable(i)) next.add(i);
      } else if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
    setAnchorTurn(t);
  }, [anchorTurn, turns]); // editable читает turns

  const onSpeaker = useCallback((t: number, anchor: HTMLElement) => {
    setError(null);
    setScope("one");
    setMenu({ anchor, turn: t });
  }, []);
  const closeMenu = useCallback(() => { setMenu(null); setError(null); }, []);
  const clear = useCallback(() => { setSelected(new Set()); setAnchorTurn(null); }, []);

  // Esc снимает выделение (если не открыто меню — его закрывает сам Popover).
  useEffect(() => {
    if (!selected.size || menu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (e.target instanceof Element && e.target.closest("input, textarea, select, [role=dialog]")) return;
      clear();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selected, menu, clear]);

  const assign = async (chosen: number[], to: Target) => {
    const idx = chosen.flatMap((t) => turns[t]?.idx ?? []).filter((i) => segments[i]?.speaker);
    if (!idx.length || busy) return;
    setBusy(true);
    setError(null);
    try {
      const labels = idx.map((i) => segments[i]!.speaker as string);
      const view = await relabelTurns(endpoint, id, { idx, labels, count: segments.length, to });
      const name = view.step?.ops[0] && "to" in view.step.ops[0] ? view.step.ops[0].to : to ?? "новому спикеру";
      setDone({ text: `${chosen.length} ${turnsWord(chosen.length)} → ${name}`, undo: true });
      setMenu(null);
      clear();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const undo = async () => {
    setBusy(true);
    try {
      await undoSpeakers(endpoint, id);
      setDone({ text: "Назначение отменено", undo: false });
      onChanged();
    } catch (e) {
      setDone({ text: errorText(e), undo: false });
    } finally {
      setBusy(false);
    }
  };

  let menuNode: ReactNode = null;
  if (menu) {
    const one = menu.turn;
    const turn = one !== null ? turns[one] : undefined;
    const run = one !== null ? runFrom(turns, one) : [];
    const chosen = one === null ? [...selected].sort((a, b) => a - b) : scope === "run" ? run : [one];
    const current = turn?.speaker ?? null;
    const title = turn ? `Реплика ${clock(turn.start)} · ${turn.speaker}` : `Выбрано: ${chosen.length} ${turnsWord(chosen.length)}`;
    menuNode = (
      <Popover anchor={menu.anchor} onClose={closeMenu} label="Кому отдать реплики">
        <div className="tmenu">
          <div className="tmenu__title">{title}</div>
          {one !== null && run.length > 1 && (
            <div className="tmenu__scope" role="radiogroup" aria-label="Какие реплики">
              <label><input type="radio" name="tmenu-scope" checked={scope === "one"}
                onChange={() => setScope("one")} /> Только эта реплика</label>
              <label><input type="radio" name="tmenu-scope" checked={scope === "run"}
                onChange={() => setScope("run")} /> Эта и следующие подряд того же спикера ({run.length})</label>
            </div>
          )}
          <div className="tmenu__hint muted">Кому отдать:</div>
          <TargetPicker label="Кому отдать реплики" speakers={meeting} current={one !== null ? current : null}
            people={people} owner={owner} endpoint={endpoint} avatarVersion={avatarVersion}
            placeholder="Спикер встречи, имя или поиск" onPick={(to) => void assign(chosen, to)} />
          {error && <div className="card__error tmenu__error" role="alert">{error}</div>}
          {one !== null && turn && (
            <div className="tmenu__links">
              <button type="button" className="spk-link" onClick={() => { onSelect(one, "toggle"); closeMenu(); }}>
                {selected.has(one) ? "Убрать из выбранных" : "Выбрать реплику (Ctrl+щелчок)"}
              </button>
              <button type="button" className="spk-link" onClick={() => { closeMenu(); onOpenPanel(turn.speaker); }}>
                Все реплики спикера — в панели «Спикеры»
              </button>
            </div>
          )}
        </div>
      </Popover>
    );
  }

  const bar = selected.size || done ? (
    <div className="tsel" role="status" aria-live="polite">
      {selected.size > 0 ? (
        <>
          <span>Выбрано: {selected.size} {turnsWord(selected.size)}</span>
          <button type="button" className="spk-btn" disabled={busy}
            onClick={(e) => { setError(null); setMenu({ anchor: e.currentTarget, turn: null }); }}>
            Назначить выбранные…
          </button>
          <button type="button" className="spk-link" onClick={clear}>Снять выделение</button>
          <span className="muted tsel__hint">Ctrl+щелчок — добавить реплику, Shift+щелчок — диапазон, Esc — снять</span>
        </>
      ) : done && (
        <>
          <span>{done.text}</span>
          {done.undo && <button type="button" className="spk-link" disabled={busy} onClick={() => void undo()}>Отменить</button>}
          <button type="button" className="spk-link tsel__close" aria-label="Скрыть" onClick={() => setDone(null)}>×</button>
        </>
      )}
    </div>
  ) : null;

  return { selected, onSelect, onSpeaker, bar, menu: menuNode };
}
