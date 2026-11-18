/**
 * Фразы-образцы спикера — одинаково в панели «Спикеры», «Разделить спикера»
 * и «Переразделить»: ▶ играет образец целиком в общем плеере карточки (не
 * дольше `SAMPLE_MAX_S`), рядом — начало и длительность («00:12 · 4 с»), пока
 * образец играет — «Остановить». Плеер один, поэтому новый образец сменяет
 * прежний сам. Подходящей фразы нет — подпись без ▶.
 *
 * Что сейчас играет и как остановить — из `PhrasePlayback` (его даёт карточка
 * записи по `onPhrase` плеера); без него «Остановить» не появляется. У
 * играющего образца со словами (`words`) звучащее слово подсвечено по часам
 * плеера карточки (`PlayTime`).
 */

import { createContext, useContext, useMemo, useRef } from "react";
import { Play, Square } from "lucide-react";
import { clock } from "../../../lib/format";
import { NO_PHRASE, sampleCaption, sampleUntil } from "../../../lib/samples";
import { nfc } from "../../../lib/search";
import type { SampleWord, SpeakerPhrase } from "../../../lib/types";
import { wordSpans } from "../../../lib/words";
import { wordPieces } from "../../../ui/Highlight";
import { Icon } from "../../../ui/Icon";
import type { Phrase } from "../AudioPlayer";
import { useWordPaint } from "../playClock";

export type PhrasePlaybackValue = {
  /** Играющий фрагмент общего плеера; null — ничего не играет. */
  now: Phrase | null;
  /** Остановить его. */
  stop: () => void;
};

export const PhrasePlayback = createContext<PhrasePlaybackValue>({ now: null, stop: () => {} });

/** Текст образца; играет — по словам, звучащее подсвечено. */
function SampleText({ text, words }: { text: string; words?: SampleWord[] }) {
  const shown = useMemo(() => nfc(text), [text]);
  const spans = useMemo(() => (words?.length ? wordSpans(shown, words) : null), [shown, words]);
  const box = useRef<HTMLSpanElement>(null);
  useWordPaint(box, spans);
  return (
    <span ref={box} className="spk-phrase__text">
      {spans ? wordPieces(shown, 0, shown.length, spans, "s") : text}
    </span>
  );
}

export function SamplePhrases({ samples, label, playable, onPlay }: {
  samples: SpeakerPhrase[];
  /** Имя списка для диктора. */
  label: string;
  playable: boolean;
  onPlay: (start: number, until: number) => void;
}) {
  const { now, stop } = useContext(PhrasePlayback);
  if (!samples.length) return <p className="spk-row__nophrase muted">{NO_PHRASE}</p>;
  return (
    <ul className="spk-row__phrases" aria-label={label}>
      {samples.map((s) => {
        const until = sampleUntil(s);
        const on = now !== null && now.start === s.start && now.until === until;
        return (
          <li key={s.start} className="spk-phrase">
            {on ? (
              <button type="button" className="spk-phrase__play" data-on aria-label="Остановить" onClick={stop}>
                <Icon as={Square} size="sm" fill="currentColor" />
              </button>
            ) : (
              <button type="button" className="spk-phrase__play" disabled={!playable}
                aria-label={`Прослушать фразу с ${clock(s.start)}`}
                onClick={() => onPlay(s.start, until)}><Icon as={Play} size="sm" /></button>
            )}
            <span className="spk-phrase__time num muted">{sampleCaption(s)}</span>
            <SampleText text={s.text} words={on ? s.words : undefined} />
          </li>
        );
      })}
    </ul>
  );
}
