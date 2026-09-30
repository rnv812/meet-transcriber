import { forwardRef, useImperativeHandle, useRef } from "react";
import { audioUrl, type Endpoint } from "../../lib/api";

export type Track = "sys" | "mic" | "source";
export type AudioPlayerHandle = { play: (track: Track, at: number) => void };

/**
 * Один общий <audio> на карточку: реплики перематывают его, а не создают свои.
 * Без ожидания loadedmetadata: currentTime и play() сразу после смены src — браузер сам
 * применит позицию, когда данные придут (детерминированно и без отложенных слушателей).
 */
export const AudioPlayer = forwardRef<AudioPlayerHandle, { endpoint: Endpoint; id: string }>(
  function AudioPlayer({ endpoint, id }, ref) {
    const el = useRef<HTMLAudioElement>(null);

    useImperativeHandle(ref, () => ({
      play(track, at) {
        const a = el.current;
        if (!a) return;
        if (a.dataset.track !== track) {
          a.dataset.track = track;
          a.src = audioUrl(endpoint, id, track);
          a.load?.();
        }
        a.currentTime = at;
        void a.play?.()?.catch?.(() => {});
      },
    }), [endpoint, id]);

    return <audio ref={el} className="card__audio" controls preload="metadata" />;
  },
);
