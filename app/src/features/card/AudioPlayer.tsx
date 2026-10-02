/**
 * Плеер записи внизу карточки — как у YouTube: тонкая полоса, разбитая на
 * главы встречи (с зазорами и подписями «1. Вступление»), над ней при
 * наведении — кривая важности (как «самые пересматриваемые»), пузырь «мм:сс ·
 * глава» с репликой в этот момент; ниже — пуск, громкость, время, текущая
 * глава «›» (список глав), скорость, «Главы», «Только важное», компактный вид.
 * Без анализа встречи — простая полоса и те же кнопки (без глав и кривой).
 *
 * Играет `track=playback` — резидент сводит обе стороны звонка (sys и mic) в
 * одну дорожку и кэширует её; импорт играется как есть. Реплика лишь
 * перематывает общий плеер (`seek`).
 *
 * Токен — в query (`audioUrl`): <audio> в WebView2 не умеет ставить заголовок.
 * `preload="none"`: открытая карточка файл не запрашивает — запрос идёт с
 * первым «▶». `release()` — отпустить файл перед удалением записи.
 *
 * Плавность: положение (`--pos`), наведение (`--hover`) и загруженное
 * (`--buf`) — CSS-переменные на полосе, их пишут requestAnimationFrame и
 * обработчики указателя напрямую, без состояния React на каждое движение мыши;
 * кривая — один путь SVG, пересчитывается только при смене анализа.
 *
 * Клавиши (как на YouTube, пока фокус не в поле ввода, не в терминале агента и
 * не в окне): Пробел/K — пуск и пауза, J/L — ±10 с, ←/→ — ±5 с, Shift+←/→ —
 * соседняя глава, Ctrl+←/→ — соседняя реплика, M — звук, 0–9 — к 0–90 %.
 * На полосе с фокусом ещё Home/End.
 *
 * Состояние — на одну запись: карточка монтирует плеер заново с `key={id}`.
 */

import {
  forwardRef, memo, useCallback, useEffect, useId, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState,
  type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent,
} from "react";
import { ChevronRight, ListVideo, Maximize2, Minimize2, Sparkles, X } from "lucide-react";
import {
  BAR_GAP_PX, barLayout, chapterAt, chapterJump, curvePath, curveValues, importantSpans, skipTarget, turnAt, turnJump,
  type ChapterView, type Span,
} from "../../lib/analysisView";
import { audioUrl, type Endpoint } from "../../lib/api";
import { clock, plural } from "../../lib/format";
import type { CurveMode } from "../../lib/markupPrefs";
import type { Turn } from "../../lib/speakers";
import { Avatar } from "../../ui/Avatar";
import { Popover } from "../../ui/Popover";
import type { PersonColor } from "./Turns";
import { PlayerKeysTip } from "./PlayerKeysTip";
import "./player.css";

export type AudioPlayerHandle = {
  /** Перемотать; `play` — и играть, `until` — остановиться на этой секунде (фраза спикера). */
  seek: (at: number, play?: boolean, until?: number) => void;
  /** Остановить и отпустить файл (перед удалением записи). */
  release: () => void;
};

export const SPEEDS = [1, 1.25, 1.5, 2];
export const SEEK_STEP_S = 5;
export const JUMP_STEP_S = 10;
/** Сколько символов реплики в пузыре над полосой. */
export const BUBBLE_TEXT_MAX = 60;

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
function VolumeIcon({ muted, level }: { muted: boolean; level: number }) {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path d="M2.5 6h2.5l3.5-3v10l-3.5-3H2.5z" fill="currentColor" />
      {muted || level === 0
        ? <path d="M11 6l4 4M15 6l-4 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        : (
          <>
            <path d="M11 5.5a3.5 3.5 0 0 1 0 5" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" />
            {level > 0.5 && <path d="M12.8 3.8a6 6 0 0 1 0 8.4" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" />}
          </>
        )}
    </svg>
  );
}

/** Где клавиши плеера не работают: поля ввода, терминал агента, окна и меню, список вкладок. */
const KEYS_IGNORED = [
  "input", "textarea", "select", "[contenteditable='']", "[contenteditable='true']", "[data-agent-terminal]", ".xterm",
  "[role=dialog]", "[aria-modal=true]", ".popover", ".menu", ".item-menu", "[role=menu]", "[role=listbox]",
  "[role=tablist]",
].join(", ");
/** На них пробел нажимает их самих. */
const PRESSABLE = "button, a, summary, [role=button], [role=tab], [role=checkbox], [role=switch], [role=menuitem]";

/** Клавиша плеера: что сделать; null — не наша (или нельзя забирать её у того, где фокус). */
export type PlayerKey =
  | { kind: "toggle" } | { kind: "seek"; by: number } | { kind: "chapter"; dir: 1 | -1 } | { kind: "turn"; dir: 1 | -1 }
  | { kind: "mute" } | { kind: "percent"; p: number };

export function playerKey(e: Pick<KeyboardEvent, "key" | "code" | "ctrlKey" | "shiftKey" | "altKey" | "metaKey"
  | "defaultPrevented" | "target">): PlayerKey | null {
  if (e.defaultPrevented || e.altKey || e.metaKey) return null;
  const target = e.target instanceof Element ? e.target : null;
  if (target?.closest(KEYS_IGNORED)) return null;
  const arrow = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
  if (e.ctrlKey) return arrow && !e.shiftKey ? { kind: "turn", dir: arrow as 1 | -1 } : null;
  if (arrow) return e.shiftKey ? { kind: "chapter", dir: arrow as 1 | -1 } : { kind: "seek", by: arrow * SEEK_STEP_S };
  if (e.shiftKey) return null;
  if (e.key === " " || e.code === "Space") return target?.closest(PRESSABLE) ? null : { kind: "toggle" };
  // По коду клавиши — и в русской раскладке.
  switch (e.code) {
    case "KeyK": return { kind: "toggle" };
    case "KeyJ": return { kind: "seek", by: -JUMP_STEP_S };
    case "KeyL": return { kind: "seek", by: JUMP_STEP_S };
    case "KeyM": return { kind: "mute" };
  }
  const digit = /^(?:Digit|Numpad)(\d)$/.exec(e.code);
  return digit ? { kind: "percent", p: Number(digit[1]) * 10 } : null;
}

const COMPACT_KEY = "meet.player.compact";
function readCompact(): boolean {
  try { return window.localStorage?.getItem(COMPACT_KEY) === "1"; } catch { return false; }
}
function writeCompact(v: boolean) {
  try { window.localStorage?.setItem(COMPACT_KEY, v ? "1" : "0"); } catch { /* хранилище недоступно */ }
}

/** Начало реплики для пузыря: первые BUBBLE_TEXT_MAX символов. */
export function bubbleText(text: string): string {
  const chars = Array.from(text.replace(/\s+/g, " ").trim());
  return chars.length <= BUBBLE_TEXT_MAX ? chars.join("") : `${chars.slice(0, BUBBLE_TEXT_MAX - 1).join("").trimEnd()}…`;
}

type BubbleHandle = { show: (t: number, turn: number, chapter: number) => void };

/**
 * Пузырь над полосой: «мм:сс · глава», аватар спикера и начало реплики.
 * Время пишется прямо в DOM; перерисовка — только когда сменилась реплика или глава.
 */
const Bubble = memo(forwardRef<BubbleHandle, {
  turns: Turn[]; chapters: ChapterView[]; people: Map<string, PersonColor>; endpoint: Endpoint;
  avatarVersion?: Record<string, number>;
}>(function Bubble({ turns, chapters, people, endpoint, avatarVersion }, ref) {
  const [at, setAt] = useState<{ turn: number; chapter: number }>({ turn: -1, chapter: -1 });
  const time = useRef<HTMLSpanElement>(null);
  useImperativeHandle(ref, () => ({
    show(t, turn, chapter) {
      if (time.current) time.current.textContent = clock(t);
      setAt((cur) => (cur.turn === turn && cur.chapter === chapter ? cur : { turn, chapter }));
    },
  }), []);
  const turn = turns[at.turn];
  const chapter = chapters[at.chapter];
  const person = turn ? people.get(turn.speaker) : undefined;
  return (
    <div className="pbar__bubble" aria-hidden="true">
      <div className="pbar__bubble-head">
        <span className="num" ref={time} />
        {chapter && <span className="pbar__bubble-chapter"> · {chapter.title}</span>}
      </div>
      {turn && turn.kind !== "break" && (
        <div className="pbar__bubble-turn">
          <Avatar name={turn.speaker} color={person?.color} hasAvatar={person?.has_avatar} endpoint={endpoint}
            version={avatarVersion?.[turn.speaker]} size={18} />
          <span className="pbar__bubble-text"><b>{turn.speaker}:</b> {bubbleText(turn.texts.join(" "))}</span>
        </div>
      )}
    </div>
  );
}));

const NO_TURNS: Turn[] = [];
const NO_CHAPTERS: ChapterView[] = [];
const NO_PEOPLE: PersonColor[] = [];

const frame = (fn: () => void): number =>
  (typeof requestAnimationFrame === "function" ? requestAnimationFrame(fn) : (fn(), 0));
const cancelFrame = (id: number) => { if (id && typeof cancelAnimationFrame === "function") cancelAnimationFrame(id); };

export const AudioPlayer = forwardRef<AudioPlayerHandle, {
  endpoint: Endpoint;
  id: string;
  /** Длительность из карточки записи — пока браузер не прочитал метаданные. */
  durationHint?: number | null;
  /** Дорожка загрузилась (true) или нет (false): без неё реплики не перематывают. */
  onAvailable?: (ok: boolean) => void;
  /** Реплики — для пузыря над полосой и Ctrl+←/→. */
  turns?: Turn[];
  /** Главы анализа: полоса делится на них. */
  chapters?: ChapterView[];
  /** Важность каждой реплики (анализ встречи): кривая над полосой и «Только важное»; null — нет. */
  importance?: number[] | null;
  curveMode?: CurveMode;
  /** Подписи глав под полосой. */
  barLabels?: boolean;
  people?: PersonColor[];
  avatarVersion?: Record<string, number>;
  /** Выбрали главу в списке: карточка показывает её в расшифровке. */
  onChapter?: (chapter: number) => void;
}>(function AudioPlayer({
  endpoint, id, durationHint, onAvailable, turns = NO_TURNS, chapters = NO_CHAPTERS, importance = null,
  curveMode = "hover", barLabels = true, people = NO_PEOPLE, avatarVersion, onChapter,
}, ref) {
  const el = useRef<HTMLAudioElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const bubble = useRef<BubbleHandle>(null);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState<number | null>(null);
  const [rate, setRate] = useState(1);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [failed, setFailed] = useState(false);
  /** Запросили воспроизведение, а данных ещё нет: резидент сводит дорожки или файл грузится. */
  const [loading, setLoading] = useState(false);
  const [released, setReleased] = useState(false);
  const [onlyImportant, setOnlyImportant] = useState(false);
  const [compact, setCompact] = useState(readCompact);
  /** Список глав открыт: у какой кнопки (название главы или «Главы»). */
  const [chaptersAnchor, setChaptersAnchor] = useState<HTMLElement | null>(null);
  const openChapters = (e: { currentTarget: HTMLElement }) => {
    const el = e.currentTarget;
    setChaptersAnchor((cur) => (cur === el ? null : el));
  };
  // Щелчок по той же кнопке закрывает список: окно не должно закрыться раньше по «щелчку снаружи» и открыться снова.
  const keepChapters = (e: { currentTarget: HTMLElement; stopPropagation: () => void }) => {
    if (chaptersAnchor === e.currentTarget) e.stopPropagation();
  };
  const [width, setWidth] = useState(0);
  const available = useRef(onAvailable);
  available.current = onAvailable;
  /** Где остановиться (прослушивание фразы); любая другая перемотка это снимает. */
  const stopAt = useRef<number | null>(null);
  /** «Только важное»: человек сам перемотал в неважное — играем до следующего важного фрагмента. */
  const free = useRef(false);
  const total = duration ?? (durationHint && durationHint > 0 ? durationHint : 0);
  // Кривая и фрагменты «Только важного» — по длительности записи: пересчёт, только когда она стала известна.
  const spans = useMemo(
    () => (importance && total > 0 ? importantSpans(turns, importance, total) : null), [importance, turns, total]);
  const curve = useMemo(
    () => (importance && total > 0 && curveMode !== "off" ? curveValues(turns, importance, total) : null),
    [importance, turns, total, curveMode]);
  const onlySpans = onlyImportant && spans?.length ? spans : null;
  const only = useRef<Span[] | null>(onlySpans);
  only.current = onlySpans;
  const gradient = useId();

  const totalRef = useRef(total);
  totalRef.current = total;
  const peopleMap = useMemo(() => new Map(people.map((p) => [p.name, p])), [people]);

  /** Положение на полосе — прямо в CSS-переменную, без перерисовки. */
  const paint = useCallback((t: number) => {
    const end = totalRef.current;
    bar.current?.style.setProperty("--pos", String(end > 0 ? Math.min(1, Math.max(0, t / end)) : 0));
  }, []);
  useLayoutEffect(() => { paint(current); }, [current, total, paint]);

  const start = useCallback(() => {
    const a = el.current;
    if (!a) return;
    if (a.readyState < 3) setLoading(true); // HAVE_FUTURE_DATA: иначе играет сразу
    // Отказ play() (нет данных, формат) придёт и событием error — его и показываем.
    void a.play?.()?.catch?.(() => {});
  }, []);

  /** «Только важное»: из неважного места — к следующему фрагменту (после последнего — пауза). */
  /** Полосу тянут мышью: «Только важное» не перехватывает перемотку, пока не отпустят. */
  const dragging = useRef(false);
  const skip = useCallback((a: HTMLAudioElement) => {
    const list = only.current;
    if (!list || a.paused || dragging.current) return;
    const target = skipTarget(list, a.currentTime);
    if (target === null) { free.current = false; return; }
    if (free.current) return;
    if (target === Infinity) { a.pause(); return; }
    a.currentTime = target;
    paint(target);
  }, [paint]);

  const seek = useCallback((at: number) => {
    const a = el.current;
    if (!a) return;
    stopAt.current = null;
    const end = Number.isFinite(a.duration) && a.duration > 0 ? a.duration : totalRef.current || Infinity;
    const t = Math.max(0, Math.min(at, end));
    a.currentTime = t;
    // Перемотали сами в неважное — не перескакивать, пока не дойдём до важного.
    free.current = only.current !== null && skipTarget(only.current, t) !== null;
    setCurrent(t);
    paint(t);
  }, [paint]);

  useImperativeHandle(ref, () => ({
    seek(at, play = false, until) {
      seek(at);
      if (play) {
        stopAt.current = until ?? null;
        start();
      }
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

  const playingRef = useRef(playing);
  playingRef.current = playing;
  const toggle = useCallback(() => {
    stopAt.current = null;
    if (playingRef.current) {
      el.current?.pause();
      return;
    }
    // «Только важное» доиграло последний фрагмент: пуск — снова с первого (как повтор на YouTube).
    const list = only.current;
    const a = el.current;
    if (list?.length && a && skipTarget(list, a.currentTime) === Infinity) seek(list[0]!.start);
    start();
  }, [start, seek]);

  const nextSpeed = () => {
    const next = SPEEDS[(SPEEDS.indexOf(rate) + 1) % SPEEDS.length] ?? 1;
    if (el.current) el.current.playbackRate = next;
    setRate(next);
  };

  const mutedRef = useRef(muted);
  mutedRef.current = muted;
  const toggleMute = useCallback(() => {
    const next = !mutedRef.current;
    if (el.current) el.current.muted = next;
    mutedRef.current = next;
    setMuted(next);
  }, []);

  const changeVolume = (v: number) => {
    const a = el.current;
    if (a) { a.volume = v; a.muted = v === 0; }
    setVolume(v);
    setMuted(v === 0);
  };

  // Пока играет — положение и «Только важное» каждый кадр (timeupdate — всего 4 раза в секунду).
  useEffect(() => {
    if (!playing || typeof requestAnimationFrame !== "function") return;
    let id = 0;
    const tick = () => {
      const a = el.current;
      if (a) {
        paint(a.currentTime);
        skip(a);
      }
      id = requestAnimationFrame(tick);
    };
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, [playing, paint, skip]);

  // Включили «Только важное» — и сразу из неважного места к важному.
  useEffect(() => {
    free.current = false;
    if (el.current) skip(el.current);
  }, [onlyImportant, skip]);

  // Ширина полосы — для подписей глав (только при изменении размера).
  useEffect(() => {
    const node = bar.current;
    if (!node) return;
    setWidth(node.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver((entries) => setWidth(entries[0]?.contentRect.width ?? 0));
    watch.observe(node);
    return () => watch.disconnect();
  }, [failed, compact]);

  const pieces = useMemo(() => barLayout(chapters, total, width), [chapters, total, width]);
  const path = useMemo(() => (curve?.length ? curvePath(curve) : ""), [curve]);
  const chapter = chapterAt(chapters, current);

  // --- указатель на полосе: наведение и перетаскивание без состояния React -------------------------
  const rect = useRef<DOMRect | null>(null);
  const fracAt = (clientX: number) => {
    const r = rect.current ?? bar.current?.getBoundingClientRect();
    if (!r || r.width <= 0) return 0;
    return Math.min(1, Math.max(0, (clientX - r.left) / r.width));
  };
  const hover = (frac: number) => {
    const node = bar.current;
    if (!node) return;
    node.style.setProperty("--hover", String(frac));
    const t = frac * totalRef.current;
    bubble.current?.show(t, turns.length ? turnAt(turns, t) : -1, chapterAt(chapters, t));
  };
  const scrub = useRef<{ id: number; t: number } | null>(null);
  const scrubTo = (frac: number) => {
    const t = frac * totalRef.current;
    paint(t);
    // Сама перемотка — не чаще раза за кадр.
    if (scrub.current) { scrub.current.t = t; return; }
    const job = { id: 0, t };
    scrub.current = job;
    job.id = frame(() => {
      if (scrub.current === job) scrub.current = null;
      if (el.current) el.current.currentTime = job.t;
    });
  };
  const onPointerEnter = (e: ReactPointerEvent<HTMLDivElement>) => {
    rect.current = e.currentTarget.getBoundingClientRect();
    e.currentTarget.classList.add("is-hover");
    hover(fracAt(e.clientX));
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!rect.current) rect.current = e.currentTarget.getBoundingClientRect();
    e.currentTarget.classList.add("is-hover");
    const frac = fracAt(e.clientX);
    hover(frac);
    if (dragging.current) scrubTo(frac);
  };
  const onPointerLeave = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (dragging.current) return;
    e.currentTarget.classList.remove("is-hover");
    rect.current = null;
  };
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || !totalRef.current) return;
    rect.current = e.currentTarget.getBoundingClientRect();
    dragging.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    e.currentTarget.classList.add("is-drag", "is-hover");
    const frac = fracAt(e.clientX);
    hover(frac);
    scrubTo(frac);
  };
  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    dragging.current = false;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    e.currentTarget.classList.remove("is-drag");
    if (scrub.current) { cancelFrame(scrub.current.id); scrub.current = null; }
    const r = rect.current;
    seek(fracAt(e.clientX) * totalRef.current);
    if (!r || e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) {
      e.currentTarget.classList.remove("is-hover");
      rect.current = null;
    }
  };

  // --- клавиши ------------------------------------------------------------------------------------
  const act = useCallback((k: PlayerKey) => {
    const now = el.current?.currentTime ?? 0;
    const end = totalRef.current;
    switch (k.kind) {
      case "toggle": toggle(); return;
      case "mute": toggleMute(); return;
      case "seek": seek(now + k.by); return;
      case "percent": if (end > 0) seek((end * k.p) / 100); return;
      case "chapter": {
        const to = chapterJump(chapters, now, k.dir);
        if (to !== null) seek(to);
        return;
      }
      case "turn": {
        const to = turnJump(turns, now, k.dir);
        if (to !== null) seek(to);
      }
    }
  }, [chapters, turns, seek, toggle, toggleMute]);

  useEffect(() => {
    if (failed) return;
    const onKey = (e: KeyboardEvent) => {
      const k = playerKey(e);
      if (!k) return;
      // Без глав и реплик Shift/Ctrl+стрелки — не наши.
      if (k.kind === "chapter" && !chapters.length) return;
      if (k.kind === "turn" && !turns.length) return;
      e.preventDefault();
      act(k);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [act, failed, chapters.length, turns.length]);

  const onBarKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const now = el.current?.currentTime ?? current;
    if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && !e.shiftKey) {
      e.preventDefault();
      seek(now + (e.key === "ArrowRight" ? SEEK_STEP_S : -SEEK_STEP_S));
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      seek(e.key === "Home" ? 0 : total);
    } else if (e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };

  const toggleCompact = () => setCompact((c) => { writeCompact(!c); return !c; });
  const pickChapter = (c: number) => {
    const ch = chapters[c];
    setChaptersAnchor(null);
    if (!ch) return;
    seek(c === 0 ? 0 : ch.start);
    onChapter?.(c);
  };

  if (failed) {
    return (
      <div className="player player--off" role="status">
        <span className="muted">Аудио недоступно</span>
      </div>
    );
  }

  const hasChapters = chapters.length > 0;
  const curveOn = !!path && curveMode !== "off";
  const labels = barLabels && hasChapters && !compact && pieces.some((p) => p.label);
  const fragments = onlySpans?.length ?? 0;
  const here = hasChapters ? chapters[chapter] : undefined;
  return (
    <div className={`player player--yt${compact ? " player--compact" : ""}${curveOn ? ` player--curve-${curveMode}` : ""}`}
      role="group" aria-label="Проигрыватель записи">
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
        onProgress={(e) => {
          const a = e.currentTarget;
          const end = totalRef.current;
          const got = a.buffered?.length ? a.buffered.end(a.buffered.length - 1) : 0;
          bar.current?.style.setProperty("--buf", String(end > 0 ? Math.min(1, got / end) : 0));
        }}
        onTimeUpdate={(e) => {
          const a = e.currentTarget;
          setCurrent(a.currentTime);
          if (stopAt.current !== null && a.currentTime >= stopAt.current) {
            stopAt.current = null;
            a.pause();
          }
          skip(a);
        }}
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
      <div className="player__bar">
        <div
          ref={bar}
          className="pbar"
          role="slider" tabIndex={0} aria-label="Позиция"
          aria-keyshortcuts="Space K J L ArrowLeft ArrowRight Shift+ArrowLeft Shift+ArrowRight M"
          aria-valuemin={0} aria-valuemax={Math.round(total)} aria-valuenow={Math.round(Math.min(current, total || current))}
          aria-valuetext={`${clock(current)} из ${clock(total)}${here ? `, глава «${here.title}»` : ""}`}
          onKeyDown={onBarKey}
          onPointerEnter={onPointerEnter} onPointerMove={onPointerMove} onPointerLeave={onPointerLeave}
          onPointerDown={onPointerDown} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
        >
          {curveOn && (
            <svg className="pbar__curve" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">
              <defs>
                <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stopColor="#fff" stopOpacity="0.42" />
                  <stop offset="1" stopColor="#fff" stopOpacity="0.06" />
                </linearGradient>
              </defs>
              <path d={path} fill={`url(#${gradient})`} />
            </svg>
          )}
          <div className="pbar__track">
            {pieces.map((p, k) => (
              <div key={p.n} className="pbar__seg" data-chapter={p.n || undefined}
                style={{
                  left: `${p.a * 100}%`,
                  // Зазор между главами — как у YouTube; у последней его нет.
                  width: k < pieces.length - 1 ? `calc(${(p.b - p.a) * 100}% - ${BAR_GAP_PX}px)` : `${(p.b - p.a) * 100}%`,
                  ["--a" as string]: p.a, ["--b" as string]: p.b,
                }}>
                <div className="pbar__buf" />
                <div className="pbar__hov" />
                <div className="pbar__fill" />
              </div>
            ))}
            {onlySpans?.map((s, k) => (
              <div key={k} className="pbar__span" aria-hidden="true"
                style={{ left: `${(s.start / (total || 1)) * 100}%`, width: `${((s.end - s.start) / (total || 1)) * 100}%` }} />
            ))}
          </div>
          <div className="pbar__knob" aria-hidden="true" />
          <Bubble ref={bubble} turns={turns} chapters={chapters} people={peopleMap} endpoint={endpoint}
            avatarVersion={avatarVersion} />
        </div>
        {labels && (
          <div className="pbar__labels" aria-hidden="true">
            {pieces.map((p) => (
              <span key={p.n} className="pbar__label" title={p.title}
                style={{ left: `${p.a * 100}%`, width: `${(p.b - p.a) * 100}%` }}>{p.label}</span>
            ))}
          </div>
        )}
      </div>
      <div className="player__left">
        <button type="button" className="player__play" onClick={toggle} aria-label={playing ? "Пауза" : "Воспроизвести"}
          title={playing ? "Пауза (K)" : "Воспроизвести (K)"}>
          {playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <span className="pvol">
          <button type="button" className="player__mute" onClick={toggleMute} aria-pressed={muted}
            aria-label={muted ? "Включить звук" : "Выключить звук"} title={muted ? "Включить звук (M)" : "Выключить звук (M)"}>
            <VolumeIcon muted={muted} level={volume} />
          </button>
          <input type="range" className="pvol__slider" aria-label="Громкость" min={0} max={1} step={0.05}
            value={muted ? 0 : volume} aria-valuetext={`${Math.round((muted ? 0 : volume) * 100)}%`}
            style={{ ["--vol" as string]: `${(muted ? 0 : volume) * 100}%` }}
            onChange={(e) => changeVolume(Number(e.target.value))} />
        </span>
        {loading ? (
          <span className="player__status" role="status">
            <span className="player__spinner" aria-hidden="true" />Подготовка аудио…
          </span>
        ) : (
          <span className="player__clock">
            <span className="player__time num">{clock(current)}</span>
            <span className="player__slash" aria-hidden="true">/</span>
            <span className="player__time num">{clock(total)}</span>
          </span>
        )}
        {here && (
          <button type="button" className="player__chapter" aria-haspopup="dialog"
            aria-expanded={chaptersAnchor?.classList.contains("player__chapter") ?? false}
            aria-label={`Глава ${here.n}: ${here.title}. Список глав`} title="Главы встречи" onMouseDown={keepChapters} onClick={openChapters}>
            <span className="player__chapter-title">{here.title}</span>
            <ChevronRight size={14} strokeWidth={2} aria-hidden="true" />
          </button>
        )}
        {fragments > 0 && (
          <span className="player__only" role="status">
            <span className="player__only-text">
              Только важное · {fragments} {plural(fragments, "фрагмент", "фрагмента", "фрагментов")}
            </span>
            <button type="button" className="player__only-off" aria-label="Выключить «Только важное»"
              title="Выключить «Только важное»" onClick={() => setOnlyImportant(false)}>
              <X size={12} strokeWidth={2.2} aria-hidden="true" />
            </button>
          </span>
        )}
      </div>
      <div className="player__right">
        <button type="button" className="player__speed num" onClick={nextSpeed}
          aria-label={`Скорость воспроизведения: ${speedText(rate)}`}>
          {speedText(rate)}
        </button>
        {hasChapters && (
          <button type="button" className="player__btn player__btn--chapters" aria-haspopup="dialog"
            aria-expanded={chaptersAnchor?.classList.contains("player__btn--chapters") ?? false}
            aria-label="Главы" title="Главы встречи" onMouseDown={keepChapters} onClick={openChapters}>
            <ListVideo size={15} strokeWidth={1.9} aria-hidden="true" />
            <span className="player__btn-text">Главы</span>
          </button>
        )}
        {spans && spans.length > 0 && (
          <button type="button" className="player__btn" aria-pressed={onlyImportant} aria-label="Только важное"
            title="Играть только важные фрагменты встречи" onClick={() => setOnlyImportant((v) => !v)}>
            <Sparkles size={14} strokeWidth={1.9} aria-hidden="true" />
            <span className="player__btn-text">Только важное</span>
          </button>
        )}
        <span className="player__keys"><PlayerKeysTip /></span>
        <button type="button" className="player__btn player__btn--icon" onClick={toggleCompact}
          aria-label={compact ? "Развернуть плеер" : "Компактный плеер"} title={compact ? "Развернуть плеер" : "Компактный плеер"}>
          {compact ? <Maximize2 size={14} strokeWidth={1.9} aria-hidden="true" />
            : <Minimize2 size={14} strokeWidth={1.9} aria-hidden="true" />}
        </button>
      </div>
      {chaptersAnchor && (
        <Popover anchor={chaptersAnchor} onClose={() => setChaptersAnchor(null)} label="Главы встречи" width={300}>
          <ol className="pchapters">
            {chapters.map((c, k) => (
              <li key={c.n}>
                <button type="button" className="pchapters__item" aria-current={k === chapter ? "true" : undefined}
                  onClick={() => pickChapter(k)}>
                  <span className="pchapters__n num">{c.n}</span>
                  <span className="pchapters__title">{c.title}</span>
                  <span className="pchapters__time num">{clock(k === 0 ? 0 : c.start)}</span>
                </button>
              </li>
            ))}
          </ol>
        </Popover>
      )}
    </div>
  );
});
