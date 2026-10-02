/**
 * Правка спикера прямо в расшифровке: щелчок по имени у реплики — меню
 * «Кому отдать» (только эта реплика или эта и следующие подряд того же
 * спикера), Ctrl+щелчок и Shift+щелчок по репликам — выбор нескольких и
 * «Назначить выбранные…». Каждое назначение — шаг истории встречи: его
 * отменяет «Отменить» здесь же или в панели «Спикеры».
 *
 * Здесь же — «Спросить агента» (✦): в меню реплики, в меню правого щелчка (с
 * готовыми вопросами) и для выбранных реплик.
 */

import { useCallback, useEffect, useMemo, useState, type MouseEvent, type ReactNode } from "react";
import { Sparkles } from "lucide-react";
import { AGENT_INTENTS } from "../../lib/agentRef";
import { relabelTurns, splitTurn, undoSpeakers, type Endpoint } from "../../lib/api";
import { nfc } from "../../lib/search";
import { clock, errorText, plural } from "../../lib/format";
import { NO_SPEAKER, runFrom, speakersOf, type Turn } from "../../lib/speakers";
import { wordAt } from "../../lib/textfix";
import type { Segment } from "../../lib/types";
import { Popover } from "../../ui/Popover";
import { TargetPicker, type Target } from "./speakers/TargetPicker";
import type { PersonColor } from "./Turns";

type Menu = { anchor: HTMLElement; turn: number | null };
/** «Разделить реплику здесь»: реплика, сегмент и место в его тексте; текст вокруг места — для подписи. */
type SplitAt = {
  anchor: HTMLElement; turn: number; seg: number; char: number; before: string; after: string;
  /** Место в тексте реплики целиком (для «Исправить слово…»). */
  offset: number;
};
/** Итог назначения; `step` — его шаг истории: «Отменить» — только пока он последний. */
type Done = { text: string; undo: boolean; step?: string };

const turnsWord = (n: number) => plural(n, "реплика", "реплики", "реплик");
const QUOTE = 28;

/** Место в тексте под указателем (или начало выделения) — номер символа в тексте `root`. */
export function caretOffset(root: HTMLElement, x: number, y: number): number | null {
  const doc = document as Document & {
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
  };
  let node: Node | null = null;
  let offset = 0;
  const pos = doc.caretPositionFromPoint?.(x, y);
  if (pos) {
    node = pos.offsetNode;
    offset = pos.offset;
  } else {
    const range = doc.caretRangeFromPoint?.(x, y);
    if (range) {
      node = range.startContainer;
      offset = range.startOffset;
    }
  }
  if (!node || !root.contains(node)) {
    const sel = window.getSelection?.();
    if (!sel || !sel.rangeCount || !root.contains(sel.anchorNode)) return null;
    const r = sel.getRangeAt(0);
    node = r.startContainer;
    offset = r.startOffset;
  }
  const range = document.createRange();
  range.setStart(root, 0);
  range.setEnd(node, offset);
  return range.toString().length;
}

/** Номер символа в тексте реплики (сегменты через пробел) → сегмент реплики и место в его тексте. */
export function locate(texts: string[], offset: number): { k: number; char: number } {
  let pos = 0;
  for (let k = 0; k < texts.length; k++) {
    const len = nfc(texts[k] ?? "").length;
    if (offset <= pos + len) return { k, char: Math.max(0, offset - pos) };
    pos += len + 1;
    if (offset < pos) return k + 1 < texts.length ? { k: k + 1, char: 0 } : { k, char: len };
  }
  const last = Math.max(0, texts.length - 1);
  return { k: last, char: nfc(texts[last] ?? "").length };
}

export type TurnEdit = {
  selected: ReadonlySet<number>;
  onSelect: (turn: number, how: "toggle" | "range") => void;
  onSpeaker: (turn: number, anchor: HTMLElement) => void;
  onSplitAt: (turn: number, event: MouseEvent<HTMLElement>) => void;
  /** Полоса над репликами: сколько выбрано, итог назначения с «Отменить». */
  bar: ReactNode;
  /** Меню «Кому отдать» у якоря. */
  menu: ReactNode;
};

export function useTurnEdit({
  endpoint, id, turns, segments, people, owner, avatarVersion, onOpenPanel, onChanged, onFixWord, head, onAskAgent,
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
  /** «Исправить слово…» в меню правого щелчка: слово в месте `at` текста реплики. */
  onFixWord?: (turn: number, at: number, anchor: HTMLElement) => void;
  /** Последний применённый шаг истории встречи (`edit_head` записи). */
  head?: string | null;
  /** «Спросить агента» о репликах; `intent` — готовый вопрос («Объясни»…). */
  onAskAgent?: (turns: number[], intent?: string) => void;
}): TurnEdit {
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const [anchorTurn, setAnchorTurn] = useState<number | null>(null);
  const [menu, setMenu] = useState<Menu | null>(null);
  const [splitAt, setSplitAt] = useState<SplitAt | null>(null);
  /** Меню правого щелчка у реплики без спикера: только «Спросить агента». */
  const [askOnly, setAskOnly] = useState<{ anchor: HTMLElement; turn: number } | null>(null);
  const [scope, setScope] = useState<"one" | "run">("one");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Done | null>(null);

  // Расшифровку перечитали — номера реплик могли сдвинуться.
  useEffect(() => {
    setSelected(new Set()); setAnchorTurn(null); setMenu(null); setSplitAt(null); setAskOnly(null);
  }, [segments]);
  useEffect(() => { setDone(null); }, [id]);

  const meeting = useMemo(() => speakersOf(segments), [segments]);
  /** Спикера реплики можно поменять (у «Неизвестного» менять нечего). */
  const editable = (t: number) => {
    const turn = turns[t];
    return !!turn && turn.kind !== "break" && turn.speaker !== NO_SPEAKER;
  };
  /** Реплику можно выбрать — любую, кроме отметки перерыва: спросить агента можно и о «Неизвестном». */
  const selectable = (t: number) => {
    const turn = turns[t];
    return !!turn && turn.kind !== "break";
  };

  const onSelect = useCallback((t: number, how: "toggle" | "range") => {
    if (!selectable(t)) return;
    setDone(null);
    setSelected((cur) => {
      const next = new Set(cur);
      if (how === "range" && anchorTurn !== null) {
        const [a, b] = anchorTurn < t ? [anchorTurn, t] : [t, anchorTurn];
        for (let i = a; i <= b; i++) if (selectable(i)) next.add(i);
      } else if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
    setAnchorTurn(t);
  }, [anchorTurn, turns]); // selectable читает turns

  const onSpeaker = useCallback((t: number, anchor: HTMLElement) => {
    setError(null);
    setScope("one");
    setMenu({ anchor, turn: t });
  }, []);
  const closeMenu = useCallback(() => { setMenu(null); setSplitAt(null); setAskOnly(null); setError(null); }, []);

  const onSplitAt = useCallback((t: number, e: MouseEvent<HTMLElement>) => {
    const turn = turns[t];
    if (!turn || turn.kind === "break") return;
    // Реплика без спикера: делить и отдавать нечего — меню только «Спросить агента».
    if (turn.speaker === NO_SPEAKER || !turn.idx?.length) {
      if (!onAskAgent) return;
      e.preventDefault();
      setError(null);
      setMenu(null);
      setSplitAt(null);
      setAskOnly({ anchor: e.currentTarget, turn: t });
      return;
    }
    const root = e.currentTarget;
    const offset = caretOffset(root, e.clientX, e.clientY);
    if (offset === null) return; // обычное меню браузера
    e.preventDefault();
    const { k, char } = locate(turn.texts, offset);
    const text = root.textContent ?? "";
    setError(null);
    setMenu(null);
    setSplitAt({
      anchor: root, turn: t, seg: turn.idx[k] ?? turn.idx[0]!, char, offset,
      before: text.slice(Math.max(0, offset - QUOTE), offset).trimStart(),
      after: text.slice(offset, offset + QUOTE).trimEnd(),
    });
  }, [turns, onAskAgent]);
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
      setDone({ text: `${chosen.length} ${turnsWord(chosen.length)} → ${name}`, undo: true, step: view.step?.id });
      setMenu(null);
      clear();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const split = async (at: SplitAt, to: Target) => {
    const turn = turns[at.turn];
    if (!turn?.idx || busy) return;
    setBusy(true);
    setError(null);
    try {
      const idx = turn.idx.filter((i) => segments[i]?.speaker);
      const labels = idx.map((i) => segments[i]!.speaker as string);
      const view = await splitTurn(endpoint, id, { turn: idx, at: at.seg, char: at.char, to, labels, count: segments.length });
      const op = view.step?.ops[0];
      const name = op && "to" in op ? op.to : to ?? "новому спикеру";
      setDone({ text: `Реплика разделена: вторая часть → ${name}`, undo: true, step: view.step?.id });
      setSplitAt(null);
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
      await undoSpeakers(endpoint, id, done?.step);
      setDone({ text: "Изменение отменено", undo: false });
      onChanged();
    } catch (e) {
      setDone({ text: errorText(e), undo: false });
    } finally {
      setBusy(false);
    }
  };

  /** «Спросить агента…» и готовые вопросы — в меню правого щелчка. */
  const askGroup = (turn: number) => onAskAgent && (
    <div className="tmenu__ask" role="group" aria-label="Спросить агента об этой реплике">
      <button type="button" className="tmenu__ask-main" onClick={() => { closeMenu(); onAskAgent([turn]); }}>
        <Sparkles size={14} strokeWidth={1.75} aria-hidden="true" />Спросить агента
      </button>
      {AGENT_INTENTS.map((intent) => (
        <button key={intent} type="button" className="tmenu__intent"
          onClick={() => { closeMenu(); onAskAgent([turn], intent); }}>{intent}</button>
      ))}
    </div>
  );
  /** Выбранные реплики, у которых можно поменять спикера (назначение). */
  const assignable = [...selected].filter(editable).sort((a, b) => a - b);

  let menuNode: ReactNode = null;
  if (menu) {
    const one = menu.turn;
    const turn = one !== null ? turns[one] : undefined;
    const run = one !== null ? runFrom(turns, one) : [];
    const chosen = one === null ? assignable : scope === "run" ? run : [one];
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
              <span className="muted tmenu__hint">
                Чтобы разделить реплику на две, щёлкните правой кнопкой мыши по месту разделения в тексте.
              </span>
              <button type="button" className="spk-link" onClick={() => { onSelect(one, "toggle"); closeMenu(); }}>
                {selected.has(one) ? "Убрать из выбранных" : "Выбрать реплику (Ctrl+щелчок)"}
              </button>
              <button type="button" className="spk-link" onClick={() => { closeMenu(); onOpenPanel(turn.speaker); }}>
                Все реплики спикера — в панели «Спикеры»
              </button>
              {onAskAgent && (
                <button type="button" className="spk-link" onClick={() => { closeMenu(); onAskAgent([one]); }}>
                  Спросить агента об этой реплике
                </button>
              )}
            </div>
          )}
        </div>
      </Popover>
    );
  }

  if (splitAt) {
    const turn = turns[splitAt.turn];
    const words = segments[splitAt.seg]?.has_words;
    const turnText = nfc(turn?.texts.join(" ") ?? "");
    const word = onFixWord ? wordAt(turnText, splitAt.offset) : null;
    menuNode = (
      <Popover anchor={splitAt.anchor} onClose={closeMenu} label="Разделить реплику здесь">
        <div className="tmenu">
          <div className="tmenu__title">Разделить реплику здесь</div>
          <div className="tmenu__quote">
            <span className="muted">…{splitAt.before}</span>
            <span className="tmenu__cut" aria-label="место разделения">|</span>
            <span>{splitAt.after}…</span>
          </div>
          {!words && (
            <div className="muted tmenu__hint">
              У этой расшифровки нет времени отдельных слов: реплика разделится по границе ближайшей фразы.
            </div>
          )}
          <div className="tmenu__hint muted">Кому отдать вторую часть (до конца реплики):</div>
          <TargetPicker label="Кому отдать вторую часть реплики" speakers={meeting} current={turn?.speaker ?? null}
            people={people} owner={owner} endpoint={endpoint} avatarVersion={avatarVersion}
            placeholder="Спикер встречи, имя или поиск" onPick={(to) => void split(splitAt, to)} />
          {error && <div className="card__error tmenu__error" role="alert">{error}</div>}
          {askGroup(splitAt.turn)}
          {word && onFixWord && (
            <div className="tmenu__links">
              <button type="button" className="spk-link" title="Исправить распознанное (выделите слова и нажмите Ctrl+E)"
                onClick={() => { const at = splitAt; closeMenu(); onFixWord(at.turn, at.offset, at.anchor); }}>
                Исправить слово «{turnText.slice(word.start, word.end)}»…
              </button>
            </div>
          )}
        </div>
      </Popover>
    );
  }

  if (askOnly) {
    const turn = turns[askOnly.turn];
    menuNode = (
      <Popover anchor={askOnly.anchor} onClose={closeMenu} label="Спросить агента об этой реплике">
        <div className="tmenu">
          {turn && <div className="tmenu__title">{`Реплика ${clock(turn.start)} · ${turn.speaker}`}</div>}
          {askGroup(askOnly.turn)}
        </div>
      </Popover>
    );
  }

  const bar = selected.size || done ? (
    <div className="tsel" role="status" aria-live="polite">
      {selected.size > 0 ? (
        <>
          <span>Выбрано: {selected.size} {turnsWord(selected.size)}</span>
          {assignable.length > 0 && (
            <button type="button" className="spk-btn" disabled={busy}
              onClick={(e) => { setError(null); setMenu({ anchor: e.currentTarget, turn: null }); }}>
              Назначить выбранные…
            </button>
          )}
          {onAskAgent && (
            <button type="button" className="spk-btn tsel__ask"
              onClick={() => onAskAgent([...selected].sort((a, b) => a - b))}>
              <Sparkles size={14} strokeWidth={1.75} aria-hidden="true" />Спросить агента о выбранных
            </button>
          )}
          <button type="button" className="spk-link" onClick={clear}>Снять выделение</button>
          <span className="muted tsel__hint">Ctrl+щелчок — добавить реплику, Shift+щелчок — диапазон, Esc — снять</span>
        </>
      ) : done && (
        <>
          <span>{done.text}</span>
          {done.undo && (!done.step || done.step === head) && (
            <button type="button" className="spk-link" disabled={busy} onClick={() => void undo()}>Отменить</button>
          )}
          <button type="button" className="spk-link tsel__close" aria-label="Скрыть" onClick={() => setDone(null)}>×</button>
        </>
      )}
    </div>
  ) : null;

  return { selected, onSelect, onSpeaker, onSplitAt, bar, menu: menuNode };
}
