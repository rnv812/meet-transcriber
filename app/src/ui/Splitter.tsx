import { useEffect, useLayoutEffect, useRef, type KeyboardEvent, type PointerEvent, type RefObject } from "react";
import { dragWidth, stepWidth } from "../lib/panes";
import "./splitter.css";

type Snap = { below: number; to: number };

/** Идёт перетаскивание какого-то разделителя: панели не переписывают ширину из состояния. */
export const isResizing = () => document.documentElement.classList.contains("is-resizing");

/**
 * Разделитель панелей: тонкая полоса, которую тянут мышью или двигают
 * клавишами (←/→ по 16 px, Home/End — к пределам); двойной щелчок — ширина по
 * умолчанию. Пока тянут, ширина меняется через `onPreview` (CSS-переменная,
 * без перерисовки React) не чаще кадра; отпустили — `onCommit` один раз.
 * Нажатие переводит фокус на разделитель: дальше можно стрелками.
 *
 * `panel`: «before» — панель слева от разделителя (тянут вправо — шире),
 * «after» — справа (тянут влево — шире). Само место полосы задаёт CSS
 * (`className`): разделитель — элемент нулевой ширины, область захвата —
 * его псевдоэлемент.
 *
 * `axis: "y"` — разделитель между панелями одна над другой: меняет высоту,
 * тянут по вертикали, клавиши ↑/↓; «before» — панель сверху, «after» — снизу.
 */
export function Splitter({
  label, value, min, max, panel, snap, onPreview, onCommit, onReset, className = "", handle, axis = "x",
}: {
  label: string;
  /** Ширина панели сейчас и её пределы (у навигации нижний предел — полоса значков, `snap.to`). */
  value: number;
  min: number;
  max: number;
  panel: "before" | "after";
  snap?: Snap;
  onPreview: (w: number) => void;
  onCommit: (w: number) => void;
  onReset: () => void;
  className?: string;
  /**
   * Ссылка на сам разделитель. Его родитель — область, где живут CSS-переменные:
   * ссылка на разделитель готова раньше эффектов, ссылка на родителя — позже.
   */
  handle?: RefObject<HTMLDivElement | null>;
  /** «x» — меняет ширину (по умолчанию), «y» — высоту. */
  axis?: "x" | "y";
}) {
  const own = useRef<HTMLDivElement>(null);
  const el = handle ?? own;
  const drag = useRef<{ at: number; w: number; last: number; shown: boolean } | null>(null);
  const frame = useRef<number | null>(null);
  const lo = snap ? snap.to : min;
  const y = axis === "y";
  const along = (e: PointerEvent<HTMLDivElement>) => (y ? e.clientY : e.clientX);
  const resizingClass = y ? ["is-resizing", "is-resizing--y"] : ["is-resizing"];
  // Ширину могли показать на время перетаскивания: после перерисовки — снова та, что в props.
  useLayoutEffect(() => { if (!drag.current) el.current?.setAttribute("aria-valuenow", String(value)); });

  const cancelFrame = () => {
    if (frame.current != null) (window.cancelAnimationFrame ?? clearTimeout)(frame.current);
    frame.current = null;
  };
  // Окно закрыли посреди перетаскивания — снять курсор со всего окна.
  useEffect(() => () => {
    cancelFrame();
    document.documentElement.classList.remove("is-resizing", "is-resizing--y");
  }, []);

  const show = (w: number) => {
    el.current?.setAttribute("aria-valuenow", String(w));
    onPreview(w);
  };

  const finish = () => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    cancelFrame();
    el.current?.removeAttribute("data-dragging");
    document.documentElement.classList.remove(...resizingClass);
    if (d.shown || d.last !== d.w) show(d.last);
    if (d.last !== d.w) onCommit(d.last);
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    drag.current = { at: along(e), w: value, last: value, shown: false };
    // preventDefault мог оставить фокус где был: фокус — на разделитель, стрелки работают сразу.
    e.currentTarget.focus({ preventScroll: true });
    try { e.currentTarget.setPointerCapture?.(e.pointerId); } catch { /* указатель уже отпущен */ }
    e.currentTarget.setAttribute("data-dragging", "");
    document.documentElement.classList.add(...resizingClass);
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const delta = (along(e) - d.at) * (panel === "before" ? 1 : -1);
    const w = dragWidth(d.w + delta, min, max, snap);
    if (w === d.last) return;
    d.last = w;
    if (frame.current != null) return;
    const raf = window.requestAnimationFrame ?? ((f: FrameRequestCallback) => window.setTimeout(() => f(0), 16));
    frame.current = raf(() => {
      frame.current = null;
      if (!drag.current) return;
      drag.current.shown = true;
      show(drag.current.last);
    });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    let next: number | null = null;
    // Стрелка в сторону панели — уже, от панели — шире (по высоте — так же: ↑/↓).
    const [toAfter, toBefore] = y ? ["ArrowDown", "ArrowUp"] : ["ArrowRight", "ArrowLeft"];
    const grow = panel === "before" ? toAfter : toBefore;
    const shrink = panel === "before" ? toBefore : toAfter;
    if (e.key === grow) next = stepWidth(value, 1, min, max, snap);
    else if (e.key === shrink) next = stepWidth(value, -1, min, max, snap);
    else if (e.key === "Home") next = lo;
    else if (e.key === "End") next = max;
    if (next == null) return;
    e.preventDefault();
    if (next === value) return;
    show(next);
    onCommit(next);
  };

  return (
    <div
      ref={el}
      role="separator"
      aria-orientation={y ? "horizontal" : "vertical"}
      aria-label={label}
      aria-valuenow={value}
      aria-valuemin={lo}
      aria-valuemax={max}
      tabIndex={0}
      title={`Потяните, чтобы изменить ${y ? "высоту" : "ширину"}. Двойной щелчок — как было`}
      className={`splitter splitter--${panel}${y ? " splitter--y" : ""} ${className}`.trim()}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finish}
      onPointerCancel={finish}
      onLostPointerCapture={finish}
      onKeyDown={onKeyDown}
      onDoubleClick={(e) => { e.preventDefault(); onReset(); }}
    />
  );
}
