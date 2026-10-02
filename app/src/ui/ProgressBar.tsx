/**
 * Полоска прогресса — одна на всё окно (расшифровка, загрузка модели и
 * обновления, установка движка, правка спикеров).
 *
 * - Значение известно — полоска плавно догоняет его (rAF, экспонента), а не
 *   прыгает; между редкими сообщениями резидента движение не замирает рывком.
 * - В пределах этапа (`stageKey`) значение не убывает: запоздалое или
 *   округлённое вниз сообщение не откатывает полоску назад. Новый этап — новый
 *   отсчёт, и его подпись говорит, что этап сменился.
 * - Значение неизвестно (null) — бегущий блик по пустой дорожке. Полная
 *   полоска означает «готово» и для «неизвестно» не используется никогда.
 */

import { useEffect, useRef, useState } from "react";
import { clamp01, easeToward } from "../lib/progress";
import "./primitives.css";

/**
 * Сглаженное значение 0…1 (или null). Монотонно в пределах `stageKey`.
 * Экспортируется для тестов и для мест, где нужна только цифра.
 */
export function useSmoothProgress(value: number | null, stageKey?: string | number | null): number | null {
  const [shown, setShown] = useState<number | null>(value === null ? null : clamp01(value));
  const target = useRef<number | null>(shown);
  const stage = useRef(stageKey);
  const current = useRef<number | null>(shown);
  const frame = useRef<number | null>(null);
  const last = useRef<number | null>(null);

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
      setShown(0);
    } else if (target.current === null || current.current === null) {
      current.current = current.current ?? 0;
      target.current = v;
    } else {
      target.current = Math.max(target.current, v);
    }
    if (typeof requestAnimationFrame !== "function") {
      current.current = target.current;
      setShown(target.current);
      return;
    }
    if (frame.current !== null || current.current === target.current) return;
    last.current = null;
    const tick = (ts: number) => {
      const dt = last.current === null ? 16 : ts - last.current;
      last.current = ts;
      const goal = target.current;
      if (goal === null) { frame.current = null; return; }
      const next = easeToward(current.current ?? 0, goal, dt);
      current.current = next;
      setShown(next);
      frame.current = next === goal ? null : requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
  }, [value, stageKey]);

  useEffect(() => () => {
    if (frame.current !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame.current);
    frame.current = null; // StrictMode монтирует дважды: второй проход должен запустить кадр заново
  }, []);

  return shown;
}

export function ProgressBar({ value, stageKey, label, detail, size = "md", ariaLabel, className = "", working = false }: {
  /** 0…1; null — неизвестно (бегущий блик). */
  value: number | null;
  /** Смена ключа — новый этап: отсчёт заново. */
  stageKey?: string | number | null;
  /** Над полоской слева: «Этап 2 из 4 · Выравнивание». */
  label?: string | null;
  /** Над полоской справа: «45 % · осталось ~4 мин». */
  detail?: string | null;
  size?: "sm" | "md";
  ariaLabel?: string;
  className?: string;
  /** Шкала известна, но текущий этап своего хода не сообщает: блик поверх — работа идёт. */
  working?: boolean;
}) {
  const shown = useSmoothProgress(value, stageKey);
  const known = shown !== null;
  const pct = known ? Math.round(shown * 1000) / 10 : null;
  const head = label || detail;
  return (
    <div className={`progressbar progressbar--${size} ${className}`.trim()}>
      {head && (
        <div className="progressbar__head">
          <span className="progressbar__label">{label}</span>
          {detail && <span className="progressbar__detail num">{detail}</span>}
        </div>
      )}
      <div className={`progressbar__track${known ? (working ? " progressbar__track--working" : "") : " progressbar__track--indeterminate"}`}
        role="progressbar" aria-label={ariaLabel ?? label ?? "Ход работы"}
        aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={value === null ? undefined : Math.round(clamp01(value) * 100)}
        aria-valuetext={value === null ? "выполняется" : undefined}>
        {known && <div className="progressbar__fill" style={{ width: `${pct}%` }} />}
      </div>
    </div>
  );
}
