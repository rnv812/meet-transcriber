/**
 * Положение всплывающих окон, меню и подсказок у якоря — одно правило для всех.
 *
 * Окно всегда целиком в окне приложения с отступом 8 px от краёв:
 * - по вертикали — под якорем; снизу не влезает — над ним; не влезает ни там,
 *   ни там — прижато к краю так, чтобы верх был виден;
 * - по горизонтали — левым краем к левому краю якоря (`align: "start"`) или
 *   правым к правому (`"end"`, для кнопок у правого края: окно раскрывается
 *   влево); не влезает — на другую сторону; не влезает и там — сдвиг к краю.
 *
 * Якорь — прямоугольник элемента или точка (контекстное меню под указателем:
 * left = right, top = bottom). Позиция пересчитывается, когда окно меняет
 * размер, когда меняется размер окна приложения и при прокрутке.
 */

import { useCallback, useLayoutEffect, useRef, useState, type RefObject } from "react";

export const GAP = 6;
export const MARGIN = 8;

export type AnchorRect = { left: number; right: number; top: number; bottom: number };
export type Size = { width: number; height: number };
export type Align = "start" | "end";
export type Placement = { left: number; top: number };

function horizontal(anchor: AnchorRect, width: number, view: number, align: Align): number {
  const max = view - width - MARGIN;
  if (max <= MARGIN) return MARGIN; // шире окна: левый край виден
  const start = anchor.left;
  const end = anchor.right - width;
  const fits = (x: number) => x >= MARGIN && x <= max;
  const [first, second] = align === "end" ? [end, start] : [start, end];
  if (fits(first)) return first;
  if (fits(second)) return second;
  return Math.max(MARGIN, Math.min(first, max));
}

function vertical(anchor: AnchorRect, height: number, view: number, gap: number): number {
  const below = anchor.bottom + gap;
  const above = anchor.top - gap - height;
  if (below + height <= view - MARGIN) return below;
  if (above >= MARGIN) return above;
  return Math.max(MARGIN, Math.min(below, view - height - MARGIN));
}

/** Где показать окно размера `box` у якоря `anchor` в окне `view`. */
export function placeFloating(anchor: AnchorRect, box: Size, view: Size,
  { align = "start", gap = GAP }: { align?: Align; gap?: number } = {}): Placement {
  return {
    left: horizontal(anchor, box.width, view.width, align),
    top: vertical(anchor, box.height, view.height, gap),
  };
}

/** Точка (указатель мыши) как якорь нулевого размера. */
export const pointAnchor = (x: number, y: number): AnchorRect => ({ left: x, right: x, top: y, bottom: y });

/** Якорь окна: элемент, ссылка на элемент или прямоугольник (точка). */
export type Anchor = HTMLElement | RefObject<HTMLElement | null> | AnchorRect;

function rectOf(anchor: Anchor): AnchorRect | null {
  if (anchor instanceof HTMLElement) return anchor.getBoundingClientRect();
  if ("current" in anchor) return anchor.current?.getBoundingClientRect() ?? null;
  return anchor;
}

/**
 * Положение (`position: fixed`) окна `box` у якоря. `anchor` — элемент, ссылка
 * на него, точка или null (окно закрыто). До первого замера — null: окно
 * прозрачно (см. floatingStyle), чтобы не мелькнуть в углу.
 */
export function useFloating(
  anchor: Anchor | null,
  box: RefObject<HTMLElement | null>,
  { align = "start", gap = GAP, width }: { align?: Align; gap?: number; width?: number } = {},
): Placement | null {
  const [pos, setPos] = useState<Placement | null>(null);

  const place = useCallback(() => {
    const el = box.current;
    const a = anchor && rectOf(anchor);
    if (!a || !el) return;
    const r = el.getBoundingClientRect();
    const next = placeFloating(a, { width: width ?? r.width, height: r.height },
      { width: window.innerWidth, height: window.innerHeight }, { align, gap });
    setPos((p) => (p && p.left === next.left && p.top === next.top ? p : next));
  }, [anchor, box, align, gap, width]);

  useLayoutEffect(() => {
    if (!anchor) { setPos(null); return; }
    place();
    window.addEventListener("resize", place);
    // Прокрутка любого контейнера двигает якорь: окно идёт за ним.
    window.addEventListener("scroll", place, true);
    // Содержимое выросло (раскрылся список, подтверждение) — не уйти за край.
    const watch = typeof ResizeObserver === "undefined" || !box.current ? null : new ResizeObserver(place);
    if (watch && box.current) watch.observe(box.current);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      watch?.disconnect();
    };
  }, [anchor, box, place]);

  return pos;
}

/**
 * Стиль окна по положению: до замера — прозрачно в углу. Именно opacity, а не
 * visibility: hidden — меню при открытии ставит фокус на первый пункт, а
 * скрытый элемент фокус не принимает.
 */
export const floatingStyle = (pos: Placement | null) =>
  pos ? { left: pos.left, top: pos.top } : { left: 0, top: 0, opacity: 0 };

/**
 * «Нажатие внутри» по дереву React, а не DOM. Подсказка «?» выносится порталом
 * в body (внутри стекла — backdrop-filter — `position: fixed` считается от
 * стекла, а не от окна), и для окна вокруг неё `contains()` её уже не видит.
 * Корню вешается `onMouseDownCapture={mark}`: React доставляет событие и через
 * портал, раньше обработчика на document; тот спрашивает `inside(e)`.
 */
export function useTreeInside() {
  const last = useRef<Event | null>(null);
  const mark = useCallback((e: { nativeEvent: Event }) => { last.current = e.nativeEvent; }, []);
  const inside = useCallback((e: Event) => last.current === e, []);
  return { mark, inside };
}
