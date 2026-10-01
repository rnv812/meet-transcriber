import { memo } from "react";
import { clock } from "../../lib/format";
import type { Range } from "../../lib/search";
import { NO_SPEAKER, isUnnamed, type Turn } from "../../lib/speakers";
import { Highlight } from "../../ui/Highlight";

export type PersonColor = { name: string; color: string; has_avatar: boolean };

/** Подсветка поиска: что выделить в реплике и номер её первого совпадения. */
export type TurnMarks = Map<number, { ranges: Range[]; first: number }>;

/** Плоский список без компонента на реплику: 2 часа записи — около тысячи блоков. */
export const Turns = memo(function Turns({
  turns, colors, playable, onPlay, onNameSpeaker, marks,
}: {
  turns: Turn[];
  colors: Map<string, string>;
  playable: boolean;
  onPlay: (turn: Turn) => void;
  onNameSpeaker?: (label: string, anchor: HTMLElement) => void;
  marks?: TurnMarks;
}) {
  return (
    <div className="turns">
      {turns.map((t, i) => {
        const unnamed = isUnnamed(t.speaker);
        const color = colors.get(t.speaker);
        const text = t.texts.join(" ");
        const mark = marks?.get(i);
        return (
          <div className={mark ? "turn turn--found" : "turn"} key={i}>
            {playable ? (
              <button type="button" className="turn__time num" onClick={() => onPlay(t)}>
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
                    style={!unnamed && color ? { color } : undefined}
                    onClick={(e) => onNameSpeaker?.(t.speaker, e.currentTarget)}>{t.speaker}</button>
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
