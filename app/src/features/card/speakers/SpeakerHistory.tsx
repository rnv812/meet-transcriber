/**
 * История правок спикеров одной встречи: «Отменить» / «Повторить» (и Ctrl+Z /
 * Ctrl+Shift+Z, пока фокус в карточке и не в поле ввода) и список шагов с
 * возвратом к любому состоянию. Каждый шаг — один применённый набор правок;
 * его отмена убирает и голоса, которые он запомнил в базе.
 */

import { ChevronDown, ChevronRight, Redo2, Undo2 } from "lucide-react";
import { useEffect, useState, type RefObject } from "react";
import type { SpeakerStep } from "../../../lib/types";
import { describeStep, stepTime } from "./staging";
import { Icon } from "../../../ui/Icon";
import { ConfirmDialog } from "../../../ui/ConfirmDialog";

/** Сколько шагов хранит резидент (meet/speakers.py, HISTORY_MAX). */
export const HISTORY_MAX = 50;
/** Где Ctrl+Z — это правка текста, а не отмена правок спикеров. */
const TYPING = "input, textarea, select, [contenteditable=''], [contenteditable=true], [data-agent-terminal]";

/** Ctrl+Z — отменить, Ctrl+Shift+Z (и Ctrl+Y) — повторить; только пока `on` и фокус в карточке. */
export function useUndoKeys(on: boolean, cardRef: RefObject<HTMLElement | null>, undo: () => void, redo: () => void) {
  useEffect(() => {
    const card = cardRef.current;
    if (!on || !card) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const z = e.code === "KeyZ";
      const y = e.code === "KeyY" && !e.shiftKey;
      if (!z && !y) return;
      if (e.target instanceof Element && e.target.closest(TYPING)) return;
      e.preventDefault();
      if (z && !e.shiftKey) undo();
      else redo();
    };
    card.addEventListener("keydown", onKey);
    return () => card.removeEventListener("keydown", onKey);
  }, [on, cardRef, undo, redo]);
}

export function HistoryTools({ canUndo, canRedo, onUndo, onRedo }: {
  canUndo: boolean; canRedo: boolean; onUndo: () => void; onRedo: () => void;
}) {
  return (
    <>
      <button type="button" className="spk__tool" onClick={onUndo} disabled={!canUndo}
        aria-keyshortcuts="Control+Z" title="Отменить (Ctrl+Z)"><Icon as={Undo2} size="sm" />Отменить</button>
      <button type="button" className="spk__tool" onClick={onRedo} disabled={!canRedo}
        aria-keyshortcuts="Control+Shift+Z" title="Повторить (Ctrl+Shift+Z)"><Icon as={Redo2} size="sm" />Повторить</button>
    </>
  );
}

/** «История изменений»: исходное состояние и шаги; текущий отмечен, отменённые приглушены. */
export function HistoryList({ history, pos, busy, trimmed = false, onRevert }: {
  history: SpeakerStep[];
  pos: number;
  busy: boolean;
  /** Самые старые шаги отброшены (резидент хранит последние 50): до них не вернуться. */
  trimmed?: boolean;
  /** Вернуться к состоянию после шага; null — до всех правок. */
  onRevert: (stepId: string | null) => void;
}) {
  const [shown, setShown] = useState(false);
  /** Куда просили вернуться (0 — исходное состояние, i — после шага i): ждёт подтверждения. */
  const [asking, setAsking] = useState<number | null>(null);
  const ask = (target: number) => setAsking(target);
  const confirmText = (target: number) => {
    const n = Math.abs(pos - target);
    return target < pos
      ? `Последние изменения спикеров этой встречи (${n}) будут отменены. Их можно будет вернуть отсюда же.`
      : `Отменённые раньше изменения (${n}) будут применены снова.`;
  };
  return (
    <section className="spk-hist" aria-label="История изменений">
      <button type="button" className="spk-hist__toggle" aria-expanded={shown} onClick={() => setShown((v) => !v)}>
        <Icon as={shown ? ChevronDown : ChevronRight} size="sm" />История изменений{history.length ? ` (${history.length})` : ""}
      </button>
      {shown && (
        <ol className="spk-hist__list">
          {trimmed && (
            <li className="spk-hist__note muted">
              Хранятся последние {HISTORY_MAX} изменений — более ранние отменить нельзя
            </li>
          )}
          <HistoryItem time="" text={trimmed ? "Состояние до этих изменений" : "Исходное состояние"}
            current={pos === 0} undone={false}
            busy={busy} onRevert={() => ask(0)} />
          {history.map((step, i) => (
            <HistoryItem key={step.id} time={stepTime(step.at)} text={describeStep(step)}
              current={pos === i + 1} undone={i >= pos} busy={busy} onRevert={() => ask(i + 1)} />
          ))}
        </ol>
      )}
      {asking !== null && (
        <ConfirmDialog title="Вернуться к этому состоянию?" message={confirmText(asking)}
          confirmLabel="Вернуть" danger={asking < pos}
          onCancel={() => setAsking(null)}
          onConfirm={() => {
            const target = asking;
            setAsking(null);
            onRevert(target === 0 ? null : history[target - 1]!.id);
          }} />
      )}
    </section>
  );
}

function HistoryItem({ time, text, current, undone, busy, onRevert }: {
  time: string; text: string; current: boolean; undone: boolean; busy: boolean; onRevert: () => void;
}) {
  return (
    <li className={`spk-hist__item${undone ? " spk-hist__item--undone" : ""}`} aria-current={current ? "step" : undefined}>
      <div className="spk-hist__text">
        {time && <span className="spk-hist__time num">{time}</span>}
        <span>{text}</span>
        {current && <span className="spk-hist__now">сейчас</span>}
        {undone && <span className="muted"> (отменено)</span>}
      </div>
      {!current && (
        <button type="button" className="spk-link" disabled={busy} onClick={onRevert}>Вернуть к этому состоянию…</button>
      )}
    </li>
  );
}
