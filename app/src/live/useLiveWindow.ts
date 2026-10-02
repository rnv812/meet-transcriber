/**
 * Окно плавающей панели: свёрнута или развёрнута, на весь экран, поверх всех
 * окон. Размеры и место помнит оболочка (`live_panel.rs`, `live_window.json`):
 * развёрнутая панель встаёт в последний размер, который выбрал человек,
 * поэтому страница просит только вид, а не высоту.
 *
 * Вид меняется и снаружи — человек растянул свёрнутую панель за край или
 * развернул её системой (Win+↑, к верху экрана): оболочка присылает событие
 * `live-window`. В браузере (dev) оболочки нет — вид живёт только в странице.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { inTauri, invoke, onLiveWindow } from "../lib/shell";

export type LiveView = { expanded: boolean; maximized: boolean; pinned: boolean };

export const DEFAULT_VIEW: LiveView = { expanded: false, maximized: false, pinned: true };

const isView = (value: unknown): value is LiveView =>
  !!value && typeof value === "object" && typeof (value as LiveView).expanded === "boolean";

export function useLiveWindow() {
  const [view, setView] = useState<LiveView>(DEFAULT_VIEW);
  const current = useRef(view);
  current.current = view;

  useEffect(() => {
    if (!inTauri()) return;
    let gone = false;
    let unlisten: (() => void) | null = null;
    invoke<LiveView>("live_window_state")
      .then((v) => { if (!gone && isView(v)) setView(v); })
      .catch((cause) => console.warn("live_window_state:", cause));
    onLiveWindow((v) => { if (isView(v)) setView(v); })
      .then((off) => { if (gone) off(); else unlisten = off; })
      .catch((cause) => console.warn("live-window:", cause));
    return () => { gone = true; unlisten?.(); };
  }, []);

  /** Сразу показать новый вид; оболочка ответит итоговым, отказала — вернуть прежний. */
  const change = useCallback((cmd: string, args: Record<string, unknown>, next: LiveView) => {
    const before = current.current;
    setView(next);
    if (!inTauri()) return;
    invoke<LiveView>(cmd, args)
      .then((v) => { if (isView(v)) setView(v); })
      .catch((cause) => {
        console.warn(`${cmd}:`, cause);
        setView(before);
      });
  }, []);

  const setExpanded = useCallback((expanded: boolean) =>
    change("live_set_expanded", { expanded }, { ...current.current, expanded, maximized: false }), [change]);
  const setMaximized = useCallback((maximized: boolean) =>
    change("live_set_maximized", { maximized }, { ...current.current, maximized }), [change]);
  const setPinned = useCallback((pinned: boolean) =>
    change("live_set_pinned", { pinned }, { ...current.current, pinned }), [change]);
  const startDrag = useCallback(() => {
    if (!inTauri()) return;
    invoke("live_start_drag").catch((cause) => console.warn("live_start_drag:", cause));
  }, []);

  return { view, setExpanded, setMaximized, setPinned, startDrag };
}
