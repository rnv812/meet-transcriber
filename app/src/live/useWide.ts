/**
 * Широкая ли раскладка: ширина элемента не меньше WIDE_PX. Панель меряет
 * своё окно, карточка — свою колонку; растянули окно — раскладка меняется
 * сразу (ResizeObserver). Без ResizeObserver — по ширине при отрисовке.
 */

import { type RefObject, useLayoutEffect, useState } from "react";

export const WIDE_PX = 720;

export function useWide(ref: RefObject<HTMLElement | null>, min = WIDE_PX): boolean {
  const [wide, setWide] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setWide(el.getBoundingClientRect().width >= min);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, min]);
  return wide;
}
