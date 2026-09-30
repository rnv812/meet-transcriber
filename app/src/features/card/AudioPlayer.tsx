import { forwardRef, useImperativeHandle, useRef } from "react";
import { audioUrl, type Endpoint } from "../../lib/api";

export type Track = "sys" | "mic" | "source";
export type AudioPlayerHandle = { play: (track: Track, at: number) => void };

/** Один общий <audio> на карточку: реплики перематывают его, а не создают свои. */
export const AudioPlayer = forwardRef<AudioPlayerHandle, { endpoint: Endpoint; id: string }>(
  function AudioPlayer({ endpoint, id }, ref) {
    const el = useRef<HTMLAudioElement>(null);

    useImperativeHandle(ref, () => ({
      play(track, at) {
        const a = el.current;
        if (!a) return;
        const start = () => {
          a.currentTime = at;
          void a.play?.()?.catch?.(() => {});
        };
        if (a.dataset.track !== track) {
          a.dataset.track = track;
          a.src = audioUrl(endpoint, id, track);
          a.addEventListener("loadedmetadata", start, { once: true });
          a.load?.();
        } else {
          start();
        }
      },
    }), [endpoint, id]);

    return <audio ref={el} className="card__audio" controls preload="none" />;
  },
);
