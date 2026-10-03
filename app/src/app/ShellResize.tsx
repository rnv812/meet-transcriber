import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  fitShell, LIST, listMax, loadShell, NAV, navCommit, navMax, saveShell, type ShellFit, type ShellPrefs,
} from "../lib/panes";
import { isResizing, Splitter } from "../ui/Splitter";

/**
 * Ширины навигации и списка записей: разделители и CSS-переменные
 * `--nav-w`, `--list-w` и `data-nav-rail` (полоса значков) на `.app` —
 * родителе разделителей.
 * Состояние — здесь, а не в App: окно тянут за край — перерисовывается
 * только этот компонент.
 */
export const ShellResize = memo(function ShellResize({ list }: {
  /** Виден список записей (раздел «Записи»): у него свой разделитель. */
  list: boolean;
}) {
  // Разделители — прямо в `.app`: его и правим (ссылка на него появляется позже эффектов).
  const handle = useRef<HTMLDivElement>(null);
  const app = { get current() { return handle.current?.parentElement ?? null; } };
  const [prefs, setPrefs] = useState<ShellPrefs>(loadShell);
  const win = useWindowWidth();
  const fit = fitShell(win, prefs);

  const apply = (f: ShellFit) => {
    const node = app.current;
    if (!node) return;
    node.style.setProperty("--nav-w", `${f.nav}px`);
    node.style.setProperty("--list-w", `${f.list}px`);
    node.toggleAttribute("data-nav-rail", f.rail);
  };
  useLayoutEffect(() => { if (!isResizing()) apply(fit); });

  const commit = (next: ShellPrefs) => { setPrefs(next); saveShell(next); };

  return (
    <>
      <Splitter handle={handle} label="Ширина навигации" className="shell-split shell-split--nav splitter--inside" panel="before"
        value={fit.rail ? NAV.rail : fit.nav} min={NAV.min} max={navMax(win, fit)} snap={{ below: NAV.snap, to: NAV.rail }}
        // Навигацию тянут — список ужимается так же, как потом при отпускании.
        onPreview={(w) => apply(fitShell(win, navCommit(w, win, prefs)))}
        onCommit={(w) => commit(navCommit(w, win, prefs))}
        onReset={() => commit({ ...prefs, nav: NAV.def, navMode: "auto" })} />
      {list && (
        <Splitter label="Ширина списка записей" className="shell-split shell-split--list" panel="before"
          value={fit.list} min={LIST.min} max={listMax(win, fit)}
          onPreview={(w) => app.current?.style.setProperty("--list-w", `${w}px`)}
          onCommit={(w) => commit({ ...prefs, list: w })}
          onReset={() => commit({ ...prefs, list: LIST.def })} />
      )}
    </>
  );
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
