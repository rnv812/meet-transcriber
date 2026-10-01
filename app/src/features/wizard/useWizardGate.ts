/**
 * Показ мастера первого запуска (см. gate.ts): состояние движка от оболочки,
 * флаг «пройден», открытие вручную («Установить», «Запустить мастер»).
 *
 * В приложении «резидента нет из-за движка» — это ровно `resident_status`
 * "engine-missing": в релизе оно однозначно, в dev не бывает никогда. В
 * браузере (dev) оболочки нет — там решает только то, что окно резидента не
 * нашло (и `engine_status` там null, так что сам мастер не откроется).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { type Endpoint, getSettings, patchSettings } from "../../lib/api";
import { type EngineStatus, engineStatus, markWizardDone, residentStatus } from "../../lib/shell";
import type { ResidentStatus } from "../../state/useResident";
import { readLocalDone, shouldAutoShow, writeLocalDone } from "./gate";

export type WizardStep = "hardware" | "engine" | "hf" | "models" | "devices" | "done";

/** Как часто перечитывать `resident_status`, пока мастер открыт или движка нет. */
export const SHELL_POLL_MS = 2000;

export type WizardGate = {
  /** undefined — ещё спрашиваем; null — оболочки нет (браузер) или она не ответила. */
  engine: EngineStatus | null | undefined;
  /** Открытый мастер и шаг, с которого он начат; null — закрыт. */
  wizard: WizardStep | null;
  /** Движка нет и резидента нет: вместо «Сервис не запущен» — «Движок не установлен». */
  engineMissing: boolean;
  open: (start: WizardStep) => void;
  /** «Пропустить» или «Готово»: сам мастер больше не откроется. */
  close: () => void;
  /** Установка из мастера началась: резидент поднимется — мастер не убирать. */
  installStarted: () => void;
  refreshEngine: () => Promise<void>;
};

export function useWizardGate(status: ResidentStatus, endpoint: Endpoint | null): WizardGate {
  const [engine, setEngine] = useState<EngineStatus | null | undefined>(undefined);
  const [shell, setShell] = useState<string | null | undefined>(undefined);
  const [done, setDone] = useState(readLocalDone);
  const [wizard, setWizard] = useState<WizardStep | null>(null);
  /** Мастер открылся сам и в нём ничего не начинали — его можно убрать. */
  const [auto, setAuto] = useState(false);
  const inFlight = useRef<Promise<void> | null>(null);

  // Один запрос за раз: вход на шаг и фокус окна приходят почти одновременно,
  // а engine_status идёт до 5 с (nvidia-smi).
  const refreshEngine = useCallback(() => {
    inFlight.current ??= engineStatus()
      .then(setEngine)
      .catch((cause) => {
        console.warn("engine_status:", cause);
        setEngine((cur) => (cur === undefined ? null : cur));
      })
      .finally(() => { inFlight.current = null; });
    return inFlight.current;
  }, []);

  const readShell = useCallback(() => {
    residentStatus().then(setShell).catch(() => setShell(null));
  }, []);

  useEffect(() => {
    void refreshEngine();
    readShell();
  }, [refreshEngine, readShell]);

  const residentMissing = status === "offline" && (shell === null || shell === "engine-missing");
  const engineMissing = engine?.installed === false && residentMissing;

  useEffect(() => {
    if (!engine || shell === undefined || wizard !== null) return;
    if (shouldAutoShow({ installed: engine.installed, reachable: !residentMissing, wizardDone: done })) {
      setWizard("hardware");
      setAuto(true);
    }
  }, [engine, shell, residentMissing, done, wizard]);

  // Пока мастер открыт или движка нет — следим за оболочкой: резидент мог
  // подняться сам (MEET_RESIDENT, внешний резидент).
  const watch = (wizard !== null || engineMissing) && shell != null;
  useEffect(() => {
    if (!watch) return;
    const timer = setInterval(readShell, SHELL_POLL_MS);
    return () => clearInterval(timer);
  }, [watch, readShell]);

  // Сам открывшийся и нетронутый мастер убирается, если резидент ожил.
  useEffect(() => {
    if (auto && wizard !== null && !residentMissing) {
      setWizard(null);
      setAuto(false);
    }
  }, [auto, wizard, residentMissing]);

  // Пропуск без резидента — дописать в его настройки, когда он появится.
  useEffect(() => {
    if (!endpoint || !done) return;
    let live = true;
    getSettings(endpoint)
      .then((s) => {
        const ui = s.ui as { wizard_done?: unknown } | undefined;
        if (live && ui?.wizard_done !== true) return patchSettings(endpoint, { ui: { wizard_done: true } });
      })
      .catch((cause) => console.warn("ui.wizard_done:", cause));
    return () => { live = false; };
  }, [endpoint, done]);

  const close = useCallback(() => {
    writeLocalDone();
    setDone(true);
    setWizard(null);
    setAuto(false);
    markWizardDone().catch((cause) => console.warn("mark_wizard_done:", cause));
  }, []);

  const open = useCallback((start: WizardStep) => {
    setAuto(false);
    setWizard(start);
  }, []);

  const installStarted = useCallback(() => setAuto(false), []);

  return { engine, wizard, engineMissing, open, close, installStarted, refreshEngine };
}
