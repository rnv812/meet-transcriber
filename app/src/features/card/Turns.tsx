import { memo } from "react";
import { clock } from "../../lib/format";
import { isUnnamed, type Turn } from "../../lib/speakers";

export type PersonColor = { name: string; color: string; has_avatar: boolean };

/** Плоский список без компонента на реплику: 2 часа записи — около тысячи блоков. */
export const Turns = memo(function Turns({
  turns, colors, playable, onPlay, onNameSpeaker,
}: {
  turns: Turn[];
  colors: Map<string, string>;
  playable: boolean;
  onPlay: (turn: Turn) => void;
  onNameSpeaker?: (label: string) => void;
}) {
  return (
    <div className="turns">
      {turns.map((t, i) => {
        const unnamed = isUnnamed(t.speaker);
        const color = colors.get(t.speaker);
        return (
          <div className="turn" key={i}>
            {playable ? (
              <button type="button" className="turn__time num" onClick={() => onPlay(t)}>
                {`▶ ${clock(t.start)}`}
              </button>
            ) : (
              <span className="turn__time num">{clock(t.start)}</span>
            )}
            <div className="turn__body">
              <div className="turn__head">
                {unnamed ? (
                  <button type="button" className="turn__speaker turn__speaker--unnamed"
                    onClick={() => onNameSpeaker?.(t.speaker)}>{t.speaker}</button>
                ) : (
                  <span className="turn__speaker" style={color ? { color } : undefined}>{t.speaker}</span>
                )}
                {t.uncertain && <span className="turn__flag">(нахлёст)</span>}
              </div>
              <p className="turn__text">{t.texts.join(" ")}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
});
