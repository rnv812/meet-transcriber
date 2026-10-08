import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { fitList, LIST, listMax, loadList, saveList } from "../lib/panes";
import { isResizing, Splitter } from "../ui/Splitter";

/**
 * Ширина списка записей: разделитель и CSS-переменная `--list-w` на `.app` —
 * родителе разделителя. Рейка разделов — постоянной ширины (lib/panes RAIL),
 * разделителя у неё нет.
 * Состояние — здесь, а не в App: окно тянут за край — перерисовывается
 * только этот компонент.
 */
export const ShellResize = memo(function ShellResize({ list }: {
  /** Виден список записей (раздел «Записи»): у него свой разделитель. */
  list: boolean;
}) {
  // Разделитель — прямо в `.app`: его и правим (ссылка на него появляется позже эффектов).
  const handle = useRef<HTMLDivElement>(null);
  const app = { get current() { return handle.current?.parentElement ?? null; } };
  const [want, setWant] = useState<number>(loadList);
  const win = useWindowWidth();
  const width = fitList(win, want);

  const apply = (w: number) => app.current?.style.setProperty("--list-w", `${w}px`);
  useLayoutEffect(() => { if (!isResizing()) apply(width); });

  const commit = (w: number) => { setWant(w); saveList(w); };

  // Списка нет (не «Записи») — нет и разделителя; вернулись — эффект выше ставит ширину заново.
  return list ? (
    <Splitter handle={handle} label="Ширина списка записей" className="shell-split shell-split--list" panel="before"
      value={width} min={LIST.min} max={listMax(win)}
      onPreview={apply}
      onCommit={commit}
      onReset={() => commit(LIST.def)} />
  ) : null;
});

function useWindowWidth(): number {
  const [w, setW] = useState(() => window.innerWidth);
  useEffect(() => {
    let frame = 0;
    const on = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => { frame = 0; setW(window.innerWidth); });
    };
    window.addEventListener("resize", on);
    return () => { window.removeEventListener("resize", on); if (frame) window.cancelAnimationFrame(frame); };
  }, []);
  return w;
}
