/**
 * Плеер записи внизу карточки: пуск/пауза, перемотка, время, скорость, звук.
 *
 * Играет `track=playback` — резидент сводит обе стороны звонка (sys и mic) в
 * одну дорожку и кэширует её; импорт играется как есть. Раньше каждая реплика
 * играла «свою» дорожку, выбранную по имени спикера: после переименования
 * владельца микрофона его реплики уходили на дорожку собеседников и звучала
 * тишина. Теперь реплика лишь перематывает общий плеер (`seek`).
 *
 * Токен — в query (`audioUrl`): <audio> в WebView2 не умеет ставить заголовок.
 * `preload="none"`: открытая карточка файл не запрашивает (и не заставляет
 * резидент сводить дорожки каждой просмотренной записи) — запрос идёт с первым
 * «▶». Обычно сведение уже готово: резидент делает его в фоне после
 * расшифровки; иначе, пока оно идёт, плеер показывает «Подготовка аудио…».
 * `release()` — отпустить файл перед удалением записи (на Windows открытый
 * файл не удалить).
 *
 * Состояние — на одну запись: карточка монтирует плеер заново с `key={id}`.
 *
 * Клавиатура внутри плеера: пробел — пуск/пауза, ←/→ — на 5 секунд,
 * Home/End — в начало/конец.
 */

import { forwardRef, useCallback, useImperativeHandle, useRef, useState, type KeyboardEvent } from "react";
import { audioUrl, type Endpoint } from "../../lib/api";
import { clock } from "../../lib/format";

export type AudioPlayerHandle = {
  seek: (at: number, play?: boolean) => void;
  /** Остановить и отпустить файл (перед удалением записи). */
  release: () => void;
};

export const SPEEDS = [1, 1.25, 1.5, 2];
export const SEEK_STEP_S = 5;

const speedText = (rate: number) => `${String(rate).replace(".", ",")}×`;

function PlayIcon() {
  return <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor" /></svg>;
}
function PauseIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <rect x="3.5" y="2.5" width="3" height="11" rx="1" fill="currentColor" />
      <rect x="9.5" y="2.5" width="3" height="11" rx="1" fill="currentColor" />
    </svg>
  );
}
function VolumeIcon({ muted }: { muted: boolean }) {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M2.5 6h2.5l3.5-3v10l-3.5-3H2.5z" fill="currentColor" />
      {muted
        ? <path d="M11 6l4 4M15 6l-4 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        : <path d="M11 5.5a3.5 3.5 0 0 1 0 5M12.8 3.8a6 6 0 0 1 0 8.4" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" />}
    </svg>
  );
}

export const AudioPlayer = forwardRef<AudioPlayerHandle, {
  endpoint: Endpoint;
  id: string;
  /** Длительность из карточки записи — пока браузер не прочитал метаданные. */
  durationHint?: number | null;
  /** Дорожка загрузилась (true) или нет (false): без неё реплики не перематывают. */
  onAvailable?: (ok: boolean) => void;
}>(function AudioPlayer({ endpoint, id, durationHint, onAvailable }, ref) {
  const el = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState<number | null>(null);
  const [rate, setRate] = useState(1);
  const [muted, setMuted] = useState(false);
  const [failed, setFailed] = useState(false);
  /** Запросили воспроизведение, а данных ещё нет: резидент сводит дорожки или файл грузится. */
  const [loading, setLoading] = useState(false);
  const [released, setReleased] = useState(false);
  const available = useRef(onAvailable);
  available.current = onAvailable;

  const total = duration ?? (durationHint && durationHint > 0 ? durationHint : 0);

  const start = useCallback(() => {
    const a = el.current;
    if (!a) return;
    if (a.readyState < 3) setLoading(true); // HAVE_FUTURE_DATA: иначе играет сразу
    // Отказ play() (нет данных, формат) придёт и событием error — его и показываем.
    void a.play?.()?.catch?.(() => {});
  }, []);

  const seek = useCallback((at: number) => {
    const a = el.current;
    if (!a) return;
    const end = Number.isFinite(a.duration) && a.duration > 0 ? a.duration : total || Infinity;
    const t = Math.max(0, Math.min(at, end));
    a.currentTime = t;
    setCurrent(t);
  }, [total]);

  useImperativeHandle(ref, () => ({
    seek(at, play = false) {
      seek(at);
      if (play) start();
    },
    release() {
      const a = el.current;
      if (!a) return;
      a.pause?.();
      a.removeAttribute("src");
      a.load?.();
      setReleased(true);
    },
  }), [seek, start]);

  const toggle = () => {
    if (playing) el.current?.pause();
    else start();
  };

  const nextSpeed = () => {
    const next = SPEEDS[(SPEEDS.indexOf(rate) + 1) % SPEEDS.length] ?? 1;
    if (el.current) el.current.playbackRate = next;
    setRate(next);
  };

  const toggleMute = () => {
    const next = !muted;
    if (el.current) el.current.muted = next;
    setMuted(next);
  };

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const onButton = (e.target as HTMLElement).tagName === "BUTTON";
    const now = el.current?.currentTime ?? current;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      seek(now + (e.key === "ArrowRight" ? SEEK_STEP_S : -SEEK_STEP_S));
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      seek(e.key === "Home" ? 0 : total);
    } else if (e.key === " " && !onButton) {
      // На кнопке пробел нажимает её саму — как везде.
      e.preventDefault();
      toggle();
    }
  };

  if (failed) {
    return (
      <div className="player player--off" role="status">
        <span className="muted">Аудио недоступно</span>
      </div>
    );
  }

  const progress = total > 0 ? Math.min(100, (current / total) * 100) : 0;
  return (
    <div className="player" role="group" aria-label="Проигрыватель записи" onKeyDown={onKey}>
      <audio
        ref={el}
        src={released ? undefined : audioUrl(endpoint, id, "playback")}
        preload="none"
        onLoadedMetadata={(e) => {
          const d = e.currentTarget.duration;
          if (Number.isFinite(d) && d > 0) setDuration(d);
          available.current?.(true);
        }}
        onDurationChange={(e) => {
          const d = e.currentTarget.duration;
          if (Number.isFinite(d) && d > 0) setDuration(d);
        }}
        onTimeUpdate={(e) => setCurrent(e.currentTarget.currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => { setPlaying(false); setLoading(false); }}
        onWaiting={() => setLoading(true)}
        onCanPlay={() => setLoading(false)}
        onPlaying={() => setLoading(false)}
        onEnded={() => setPlaying(false)}
        onError={() => {
          if (released) return; // src убрали сами перед удалением
          setFailed(true); setPlaying(false); setLoading(false); available.current?.(false);
        }}
      />
      <button type="button" className="player__play" onClick={toggle} aria-label={playing ? "Пауза" : "Воспроизвести"}>
        {playing ? <PauseIcon /> : <PlayIcon />}
      </button>
      {loading ? (
        <span className="player__status" role="status">
          <span className="player__spinner" aria-hidden="true" />Подготовка аудио…
        </span>
      ) : <span className="player__time num">{clock(current)}</span>}
      <input
        type="range" className="player__seek" aria-label="Позиция"
        min={0} max={total || 0} step="any" value={Math.min(current, total || current)}
        aria-valuetext={`${clock(current)} из ${clock(total)}`}
        style={{ ["--progress" as string]: `${progress}%` }}
        onChange={(e) => seek(Number(e.target.value))}
      />
      <span className="player__time num">{clock(total)}</span>
      <button type="button" className="player__speed num" onClick={nextSpeed}
        aria-label={`Скорость воспроизведения: ${speedText(rate)}`}>
        {speedText(rate)}
      </button>
      <button type="button" className="player__mute" onClick={toggleMute} aria-pressed={muted}
        aria-label={muted ? "Включить звук" : "Выключить звук"}>
        <VolumeIcon muted={muted} />
      </button>
    </div>
  );
});
