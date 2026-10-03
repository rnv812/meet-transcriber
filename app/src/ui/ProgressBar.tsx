/**
 * Полоска прогресса — одна на всё окно (расшифровка, задачи модели, загрузка
 * модели и обновления, установка движка, правка спикеров).
 *
 * - Значение известно — полоска плавно догоняет его (rAF, экспонента), а не
 *   прыгает.
 * - Между событиями резидента (`extrapolate`) полоска не стоит: продлевается
 *   по темпу последних событий, но не дальше следующей известной отметки
 *   (`cap` — конец шага), не дольше пары обычных промежутков без новостей и
 *   никогда до полной (lib/progress `extrapolate`).
 * - В пределах этапа (`stageKey`) значение не убывает: запоздалое или
 *   округлённое вниз сообщение (и продление, ушедшее вперёд) не откатывает
 *   полоску назад. Новый этап — новый отсчёт, и его подпись говорит, что этап
 *   сменился.
 * - Значение неизвестно (null) — бегущий блик по пустой дорожке. Полная
 *   полоска означает «готово» и для «неизвестно» не используется никогда.
 * - «Меньше движения» в системе — без сглаживания и продления: полоска встаёт
 *   ровно на значения резидента.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { clamp01, easeToward, extrapolate as extrapolateAt, type Sample } from "../lib/progress";
import "./primitives.css";

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

/** Сглаженное значение идёт шагом 1/2000: каждый кадр не перерисовывает окно. */
const QUANT = 2000;

function reducedMotionQuery(): MediaQueryList | null {
  try {
    return typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  } catch {
    return null;
  }
}

/** Системная настройка «меньше движения» (и её смена на ходу). */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => !!reducedMotionQuery()?.matches);
  useEffect(() => {
    const mq = reducedMotionQuery();
    if (!mq || typeof mq.addEventListener !== "function") return;
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

export type SmoothOptions = {
  /** Продлевать между событиями по темпу хода (ход задач резидента). */
  extrapolate?: boolean;
  /** Следующая известная отметка (конец шага): продление не дальше неё. */
  cap?: number | null;
};

/**
 * Сглаженное значение 0…1 (или null). Монотонно в пределах `stageKey`.
 * Экспортируется для тестов и для мест, где нужна только цифра (бейдж списка
 * показывает то же значение, что полоска в карточке).
 */
export function useSmoothProgress(value: number | null, stageKey?: string | number | null,
  options: SmoothOptions = {}): number | null {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState<number | null>(value === null ? null : clamp01(value));
  const target = useRef<number | null>(shown);
  const stage = useRef(stageKey);
  const current = useRef<number | null>(shown);
  const frame = useRef<number | null>(null);
  const last = useRef<number | null>(null);
  const samples = useRef<Sample[]>(value === null ? [] : [{ v: clamp01(value), t: now() }]);
  const cap = useRef(options.cap);
  cap.current = options.cap;
  const extra = useRef(false);
  extra.current = !!options.extrapolate && !reduced;

  useEffect(() => {
    const stop = () => {
      if (frame.current !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame.current);
      frame.current = null;
    };
    const stageChanged = stage.current !== stageKey;
    stage.current = stageKey;
    if (value === null) {
      // Неизвестно: на новом этапе — блик; на том же — держим достигнутое.
      if (stageChanged || current.current === null) {
        stop();
        target.current = null;
        current.current = null;
        samples.current = [];
        setShown(null);
      }
      return;
    }
    const v = clamp01(value);
    if (stageChanged) {
      // Новый этап — свой отсчёт с нуля, без анимации отката назад.
      stop();
      current.current = 0;
      target.current = v;
      samples.current = [];
      setShown(0);
    } else if (target.current === null || current.current === null) {
      current.current = current.current ?? 0;
      target.current = v;
    } else {
      target.current = Math.max(target.current, v);
    }
    const lastSample = samples.current.at(-1);
    if (!lastSample || v > lastSample.v) samples.current = [...samples.current.slice(-19), { v, t: now() }];
    if (reduced || typeof requestAnimationFrame !== "function") {
      // «Меньше движения»: ровно значения резидента, без анимации и продления.
      stop();
      current.current = Math.max(current.current ?? 0, target.current);
      setShown(current.current);
      return;
    }
    if (frame.current !== null) return;
    last.current = null;
    const tick = (ts: number) => {
      const dt = last.current === null ? 16 : ts - last.current;
      last.current = ts;
      const base = target.current;
      if (base === null) { frame.current = null; return; }
      const at = now();
      const ahead = extra.current ? extrapolateAt(samples.current, at, cap.current) : null;
      const goal = Math.max(base, ahead ?? base);
      const from = current.current ?? 0;
      // Не назад: продление могло уйти дальше, чем потом сказал резидент.
      const next = Math.max(from, easeToward(from, goal, dt));
      current.current = next;
      setShown(next >= goal ? next : Math.round(next * QUANT) / QUANT);
      // Цель догнана — кадры нужны, только пока продление ещё растёт.
      const growing = ahead !== null && (extrapolateAt(samples.current, at + 250, cap.current) ?? 0) > ahead;
      frame.current = next >= goal && !growing ? null : requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
  }, [value, stageKey, reduced]);

  useEffect(() => () => {
    if (frame.current !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame.current);
    frame.current = null; // StrictMode монтирует дважды: второй проход должен запустить кадр заново
  }, []);

  return shown;
}

export function ProgressBar({ value, stageKey, label, detail, size = "md", ariaLabel, className = "", working = false,
  extrapolate = false, cap }: {
  /** 0…1; null — неизвестно (бегущий блик). */
  value: number | null;
  /** Смена ключа — новый этап: отсчёт заново. */
  stageKey?: string | number | null;
  /** Над полоской слева: «Этап 2 из 4 · Выравнивание». */
  label?: ReactNode;
  /**
   * Над полоской справа: «45 % · осталось ~4 мин». Функция — от показанного
   * значения: проценты те же, что у полоски, и идут вместе с ней.
   */
  detail?: string | null | ((shown: number | null) => string | null);
  size?: "sm" | "md";
  ariaLabel?: string;
  className?: string;
  /** Шкала известна, но текущий этап своего хода не сообщает: блик поверх — работа идёт. */
  working?: boolean;
  /** Продлевать между событиями по темпу (см. `useSmoothProgress`). */
  extrapolate?: boolean;
  /** Следующая известная отметка: продление не дальше неё. */
  cap?: number | null;
}) {
  const shown = useSmoothProgress(value, stageKey, { extrapolate, cap });
  const known = shown !== null;
  const pct = known ? Math.round(shown * 1000) / 10 : null;
  const right = typeof detail === "function" ? detail(shown) : detail;
  const head = label || right;
  return (
    <div className={`progressbar progressbar--${size} ${className}`.trim()}>
      {head && (
        <div className="progressbar__head">
          <span className="progressbar__label">{label}</span>
          {right && <span className="progressbar__detail num">{right}</span>}
        </div>
      )}
      <div className={`progressbar__track${known ? (working ? " progressbar__track--working" : "") : " progressbar__track--indeterminate"}`}
        role="progressbar" aria-label={ariaLabel ?? (typeof label === "string" ? label : undefined) ?? "Ход работы"}
        aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={value === null ? undefined : Math.round(clamp01(value) * 100)}
        aria-valuetext={value === null ? "выполняется" : undefined}>
        {known && <div className="progressbar__fill" style={{ width: `${pct}%` }} />}
      </div>
    </div>
  );
}
