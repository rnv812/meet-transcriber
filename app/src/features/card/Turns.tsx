import { memo, type MouseEvent } from "react";
import { clock } from "../../lib/format";
import { nfc, type Range } from "../../lib/search";
import { NO_SPEAKER, isUnnamed, type Turn } from "../../lib/speakers";
import { Highlight } from "../../ui/Highlight";

export type PersonColor = { name: string; color: string; has_avatar: boolean };

/** Подсветка поиска: что выделить в реплике и номер её первого совпадения. */
export type TurnMarks = Map<number, { ranges: Range[]; first: number }>;

/** Плоский список без компонента на реплику: 2 часа записи — около тысячи блоков. */
export const Turns = memo(function Turns({
  turns, colors, playable, onPlay, onNameSpeaker, onSpeaker, selected, onSelect, marks,
}: {
  turns: Turn[];
  colors: Map<string, string>;
  playable: boolean;
  onPlay: (turn: Turn) => void;
  onNameSpeaker?: (label: string) => void;
  /** Щелчок по имени у реплики: меню правки спикера (иначе — `onNameSpeaker`). */
  onSpeaker?: (turn: number, anchor: HTMLElement) => void;
  /** Выбранные реплики (Ctrl/Shift+щелчок). */
  selected?: ReadonlySet<number>;
  onSelect?: (turn: number, how: "toggle" | "range") => void;
  marks?: TurnMarks;
}) {
  // Ctrl/Shift+щелчок по реплике — выбор; простой щелчок по тексту остаётся выделением текста.
  const pick = (e: MouseEvent, i: number) => {
    if (!onSelect) return false;
    if (e.ctrlKey || e.metaKey) onSelect(i, "toggle");
    else if (e.shiftKey) onSelect(i, "range");
    else return false;
    return true;
  };
  return (
    <div className="turns">
      {turns.map((t, i) => {
        if (t.kind === "break") {
          return (
            <div className="turn-break" role="separator" aria-label={t.texts.join(" ")} key={i}>
              <span className="turn-break__text">{t.texts.join(" ")}</span>
            </div>
          );
        }
        const unnamed = isUnnamed(t.speaker);
        const color = colors.get(t.speaker);
        // Как в поиске (lib/search.ts, prepare): подсветка — по тексту в NFC.
        const text = nfc(t.texts.join(" "));
        const mark = marks?.get(i);
        const on = selected?.has(i) ?? false;
        return (
          <div className={`turn${mark ? " turn--found" : ""}${on ? " turn--selected" : ""}`} key={i}
            data-selected={on || undefined}
            onMouseDown={onSelect ? (e) => { if (e.shiftKey) e.preventDefault(); } : undefined}
            onClick={onSelect ? (e) => { if (pick(e, i)) e.preventDefault(); } : undefined}>
            {on && <span className="sr-only">Выбрано.</span>}
            {playable ? (
              <button type="button" className="turn__time num"
                onClick={(e) => { if (!(onSelect && (e.ctrlKey || e.metaKey || e.shiftKey))) onPlay(t); }}>
                {`▶ ${clock(t.start)}`}
              </button>
            ) : (
              <span className="turn__time num">{clock(t.start)}</span>
            )}
            <div className="turn__body">
              <div className="turn__head">
                {t.speaker === NO_SPEAKER ? (
                  <span className="turn__speaker turn__speaker--unnamed">{t.speaker}</span>
                ) : (
                  <button type="button" className={`turn__speaker${unnamed ? " turn__speaker--unnamed" : ""}`}
                    style={!unnamed && color ? { color } : undefined} aria-haspopup={onSpeaker ? "dialog" : undefined}
                    title={onSpeaker ? "Исправить спикера реплики" : undefined}
                    onClick={(e) => {
                      if (onSelect && (e.ctrlKey || e.metaKey || e.shiftKey)) return;
                      if (onSpeaker) onSpeaker(i, e.currentTarget);
                      else onNameSpeaker?.(t.speaker);
                    }}>{t.speaker}</button>
                )}
                {t.uncertain && <span className="turn__flag">(нахлёст)</span>}
              </div>
              {/* Реплика, найденная только по спикеру, — совпадение целиком. */}
              <p className="turn__text" data-hit={mark && !mark.ranges.length ? mark.first : undefined}>
                {mark?.ranges.length ? <Highlight text={text} ranges={mark.ranges} firstHit={mark.first} /> : text}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
});
