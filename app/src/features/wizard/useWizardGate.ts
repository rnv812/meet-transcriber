/**
 * Показ мастера первого запуска (см. gate.ts): состояние движка от оболочки,
 * флаг «пройден», открытие вручную («Установить», «Запустить мастер»).
 */

import { useCallback, useEffect, useState } from "react";
import { type Endpoint, getSettings, patchSettings } from "../../lib/api";
import { type EngineStatus, engineStatus, markWizardDone, residentStatus } from "../../lib/shell";
import type { ResidentStatus } from "../../state/useResident";
import { readLocalDone, shouldAutoShow, writeLocalDone } from "./gate";

export type WizardStep = "hardware" | "engine" | "hf" | "models" | "devices" | "done";

/** Оболочка считает резидента живым (в dev — резидент из .venv репозитория). */
const SHELL_ALIVE = ["running", "external"];

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
  refreshEngine: () => Promise<void>;
};

export function useWizardGate(status: ResidentStatus, endpoint: Endpoint | null): WizardGate {
  const [engine, setEngine] = useState<EngineStatus | null | undefined>(undefined);
  const [shell, setShell] = useState<string | null | undefined>(undefined);
  const [done, setDone] = useState(readLocalDone);
  const [wizard, setWizard] = useState<WizardStep | null>(null);

  const refreshEngine = useCallback(async () => {
    try {
      setEngine(await engineStatus());
    } catch (cause) {
      console.warn("engine_status:", cause);
      setEngine(null);
    }
  }, []);

  useEffect(() => {
    void refreshEngine();
    residentStatus().then(setShell).catch(() => setShell(null));
  }, [refreshEngine]);

  // «connecting» — ещё не знаем: решаем, только когда окно резидента не нашло.
  const reachable = status !== "offline" || (shell != null && SHELL_ALIVE.includes(shell));

  useEffect(() => {
    if (!engine || shell === undefined) return;
    if (shouldAutoShow({ installed: engine.installed, reachable, wizardDone: done })) {
      setWizard((cur) => cur ?? "hardware");
    }
  }, [engine, shell, reachable, done]);

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
    markWizardDone().catch((cause) => console.warn("mark_wizard_done:", cause));
  }, []);

  return {
    engine,
    wizard,
    engineMissing: engine?.installed === false && !reachable,
    open: setWizard,
    close,
    refreshEngine,
  };
}
