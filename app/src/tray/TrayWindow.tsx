import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { trayPanelFit, trayPanelHide, trayPanelOpen } from "../lib/shell";
import { useAppearance } from "../theme/useAppearance";
import { type OpenTarget, TrayPanel } from "./TrayPanel";
import { isJustStopped } from "./trayModel";
import { recentOf, useTrayPanel } from "./useTrayPanel";

/** Как часто панель пересматривает «только что остановлена» (метка стареет сама). */
const STALE_CHECK_MS = 30_000;

/**
 * Окно `tray-panel` оболочки: стекло (на macOS — системное, `?glass=native`;
 * иначе страница рисует фон сама), Esc прячет панель, высота окна следует за
 * содержимым (`tray_panel_fit`).
 */
export function TrayWindow() {
  const data = useTrayPanel();
  useAppearance(data.endpoint);
  const box = useRef<HTMLDivElement>(null);
  const [glass] = useState(() =>
    new URLSearchParams(window.location.search).get("glass") === "native" ? "native" : "css");
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") void trayPanelHide(); };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);

  useEffect(() => {
    if (!data.visible) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), STALE_CHECK_MS);
    return () => clearInterval(t);
  }, [data.visible, data.snapshotAt]);

  // Высота окна — по содержимому. ResizeObserver есть в WebKit и Chromium.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const fit = () => void trayPanelFit(Math.ceil(el.getBoundingClientRect().height));
    fit();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(fit);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Фокус — на панель при каждом показе: Esc и Tab работают сразу. Не на
  // кнопку: Enter не должен остановить запись случайно.
  useEffect(() => {
    if (data.visible) box.current?.focus({ preventScroll: true });
  }, [data.visible, data.shownTick]);

  const open = useCallback((target: OpenTarget) => { void trayPanelOpen(target); }, []);
  const recent = recentOf(data);

  return (
    <div ref={box} className="tp" data-glass={glass} role="dialog" aria-label="Запись Meet" tabIndex={-1}
      // Сияние дышит и точка пульсирует только на экране.
      data-recording={(data.visible && (data.snapshot?.status === "recording" || !!data.snapshot?.live?.active)) || undefined}>
      <div className="tp__aura" aria-hidden="true" />
      {/* Ключ — номер показа: появление проигрывается при каждом открытии. */}
      <div key={data.shownTick} className="tp__body">
        <TrayPanel
          endpoint={data.endpoint}
          snapshot={data.snapshot}
          snapshotAt={data.snapshotAt}
          online={data.online}
          recent={recent}
          justStopped={isJustStopped(recent, data.snapshot, data.stopped, now)}
          assistant={data.assistant}
          visible={data.visible}
          onSnapshot={data.applySnapshot}
          onOpen={open}
        />
      </div>
    </div>
  );
}
