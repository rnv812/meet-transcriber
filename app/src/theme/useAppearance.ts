import { useCallback, useEffect, useState } from "react";
import { type Endpoint, getSettings } from "../lib/api";
import { onAppearance, shareAppearance } from "../lib/shell";
import {
  type Appearance, appearanceFromSettings, applyAppearance, readCached, systemPrefersDark, writeCached,
} from "./appearance";

/**
 * Оформление окна: кеш прошлого запуска → настройки резидента → выбор из
 * других окон (событие оболочки). «Системная» тема следит за ОС на лету.
 * `preview` — применить сразу и разослать (раздел «Оформление»); записывает
 * в настройки сам раздел.
 */
export function useAppearance(endpoint: Endpoint | null) {
  const [appearance, setAppearance] = useState<Appearance>(() => readCached());
  const [systemDark, setSystemDark] = useState(systemPrefersDark);

  useEffect(() => {
    applyAppearance(document.documentElement, appearance, systemDark);
  }, [appearance, systemDark]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: { matches: boolean }) => setSystemDark(e.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (!endpoint) return;
    let alive = true;
    getSettings(endpoint)
      .then((raw) => {
        if (!alive) return;
        const next = appearanceFromSettings(raw);
        writeCached(next);
        setAppearance(next);
      })
      .catch(() => {
        // Резидент не ответил: остаётся кеш прошлого запуска.
      });
    return () => { alive = false; };
  }, [endpoint]);

  useEffect(() => {
    let stop: (() => void) | null = null;
    let alive = true;
    void onAppearance((next) => {
      writeCached(next);
      setAppearance(next);
    }).then((unlisten) => {
      if (alive) stop = unlisten;
      else unlisten();
    });
    return () => { alive = false; stop?.(); };
  }, []);

  const preview = useCallback((next: Appearance) => {
    writeCached(next);
    setAppearance(next);
    void shareAppearance(next);
  }, []);

  return { appearance, preview };
}
