import { useLayoutEffect, useRef, useState } from "react";
import { clamp, loadWidth, paneBounds, paneWidth, saveWidth, type PaneSpec } from "../lib/panes";
import { isResizing, Splitter } from "./Splitter";

type Pick = (handle: HTMLElement) => HTMLElement | null;

const px = (w: number) => `${w}px`;

/**
 * Разделитель панели внутри области (меню настроек, панель человека, панель
 * спикеров, области ассистента). Область — родительский элемент разделителя
 * (или `area`): на ней CSS-переменная `cssVar` с размером панели, размер
 * запоминается под именем `name`. Область меняет размер (окно, соседние
 * панели) — панель ужимается в пределы, запомненный размер остаётся прежним.
 *
 * `axis: "y"` — панели одна над другой: разделитель меняет высоту, область
 * меряется по высоте.
 *
 * Без `spec.def` размер по умолчанию задаёт CSS (например, доля области):
 * пока человек не потянул, переменной нет, а разделитель показывает размер
 * самой панели — соседнего элемента со стороны `panel` (или `pane`).
 * Двойной щелчок снимает переменную — снова как задумано в CSS.
 */
export function PaneResizer({
  name, cssVar, spec, panel, label, className, axis = "x", cssValue = px, area: areaOf, pane: paneOf,
}: {
  name: string;
  cssVar: string;
  spec: PaneSpec;
  panel: "before" | "after";
  label: string;
  className?: string;
  axis?: "x" | "y";
  /** Что записать в переменную при размере `w` px (по умолчанию «`w`px»). */
  cssValue?: (w: number) => string;
  /** Где живёт переменная и чей размер — «область» (по умолчанию родитель разделителя). */
  area?: Pick;
  /** Сама панель (по умолчанию соседний элемент со стороны `panel`): её размер, пока не тянули. */
  pane?: Pick;
}) {
  const handle = useRef<HTMLDivElement>(null);
  const picks = useRef({ areaOf, paneOf, panel });
  picks.current = { areaOf, paneOf, panel };
  const area = () => {
    const h = handle.current;
    if (!h) return null;
    return picks.current.areaOf ? picks.current.areaOf(h) : h.parentElement;
  };
  const pane = () => {
    const h = handle.current;
    if (!h) return null;
    if (picks.current.paneOf) return picks.current.paneOf(h);
    const next = picks.current.panel === "after" ? h.nextElementSibling : h.previousElementSibling;
    return next instanceof HTMLElement ? next : null;
  };
  const fluid = spec.def == null;
  const [want, setWant] = useState<number | null>(() => loadWidth(name) ?? spec.def ?? null);
  const { room, size } = useSizes(area, fluid ? pane : null, axis);
  const { min, max } = paneBounds(spec, room);
  const width = want == null ? null : paneWidth(spec, want, room);
  const value = width ?? clamp(size, min, max);

  // Переменная ставится до отрисовки — без скачка; на время перетаскивания её ведёт разделитель.
  useLayoutEffect(() => {
    if (isResizing()) return;
    const node = area();
    if (width == null) node?.style.removeProperty(cssVar);
    else node?.style.setProperty(cssVar, cssValue(width));
  });
  // Разделитель ушёл (панель закрыли) — переменная тоже: CSS вернётся к своему значению.
  useLayoutEffect(() => {
    const node = area();
    return () => { node?.style.removeProperty(cssVar); };
  }, [cssVar]);

  return (
    <Splitter handle={handle} label={label} value={value} min={min} max={max} panel={panel} className={className}
      axis={axis}
      onPreview={(w) => area()?.style.setProperty(cssVar, cssValue(w))}
      onCommit={(w) => { setWant(w); saveWidth(name, w === spec.def ? null : w); }}
      onReset={() => { setWant(spec.def ?? null); saveWidth(name, null); }} />
  );
}

/**
 * Размер области по оси и — если нужен — самой панели; следит ResizeObserver,
 * без него — окно. 0 — ещё не измерено.
 */
function useSizes(
  area: () => HTMLElement | null,
  pane: (() => HTMLElement | null) | null,
  axis: "x" | "y",
): { room: number; size: number } {
  const [sizes, setSizes] = useState({ room: 0, size: 0 });
  const measured = !!pane;
  const find = useRef<{ area: typeof area; pane: typeof pane }>({ area, pane });
  find.current = { area, pane };
  useLayoutEffect(() => {
    const node = find.current.area();
    if (!node) return;
    const own = find.current.pane?.() ?? null;
    const measure = () => {
      const room = axis === "y" ? node.clientHeight : node.clientWidth;
      const size = own ? (axis === "y" ? own.offsetHeight : own.offsetWidth) : 0;
      setSizes((prev) => (prev.room === room && prev.size === size ? prev : { room, size }));
    };
    measure();
    if (typeof ResizeObserver !== "undefined") {
      const watch = new ResizeObserver(measure);
      watch.observe(node);
      if (own) watch.observe(own);
      return () => watch.disconnect();
    }
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [axis, measured]);
  return sizes;
}
