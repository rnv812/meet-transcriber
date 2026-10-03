import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import { loadWidth, paneBounds, paneWidth, saveWidth, type PaneSpec } from "../lib/panes";
import { isResizing, Splitter } from "./Splitter";

/**
 * Разделитель панели внутри области (меню настроек, панель человека, панель
 * спикеров). Область — родительский элемент разделителя: на нём CSS-переменная
 * `cssVar` с шириной панели, ширина запоминается под именем `name`. Область
 * меняет ширину (окно, соседние панели) — панель ужимается в пределы,
 * запомненная ширина остаётся прежней.
 */
export function PaneResizer({ name, cssVar, spec, panel, label, className }: {
  name: string;
  cssVar: string;
  spec: PaneSpec;
  panel: "before" | "after";
  label: string;
  className?: string;
}) {
  const handle = useRef<HTMLDivElement>(null);
  const area = () => handle.current?.parentElement ?? null;
  const [want, setWant] = useState(() => loadWidth(name) ?? spec.def);
  const room = useRoom(handle);
  const width = paneWidth(spec, want, room);
  const { min, max } = paneBounds(spec, room);

  // Переменная ставится до отрисовки — без скачка; на время перетаскивания её ведёт разделитель.
  useLayoutEffect(() => {
    if (!isResizing()) area()?.style.setProperty(cssVar, `${width}px`);
  });
  // Разделитель ушёл (панель закрыли) — переменная тоже: CSS вернётся к своему значению.
  useLayoutEffect(() => {
    const node = area();
    return () => { node?.style.removeProperty(cssVar); };
  }, [cssVar]);

  return (
    <Splitter handle={handle} label={label} value={width} min={min} max={max} panel={panel} className={className}
      onPreview={(w) => area()?.style.setProperty(cssVar, `${w}px`)}
      onCommit={(w) => { setWant(w); saveWidth(name, w === spec.def ? null : w); }}
      onReset={() => { setWant(spec.def); saveWidth(name, null); }} />
  );
}

/** Ширина области (родителя разделителя); без ResizeObserver — по окну. 0 — ещё не измерена. */
function useRoom(handle: RefObject<HTMLElement | null>): number {
  const [room, setRoom] = useState(0);
  useLayoutEffect(() => {
    const node = handle.current?.parentElement;
    if (!node) return;
    const measure = () => setRoom(node.clientWidth);
    measure();
    if (typeof ResizeObserver !== "undefined") {
      const watch = new ResizeObserver(measure);
      watch.observe(node);
      return () => watch.disconnect();
    }
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [handle]);
  return room;
}
