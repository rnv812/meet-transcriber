/**
 * Строка сессии в плавающем окне ассистента (0.6.2): показана или скрыта —
 * настройка `ui.live_session_bar` (по умолчанию показана), общая для всех
 * встреч и перезапусков. Читается `GET /settings` (и снова, когда окно
 * получает фокус: переключатель есть и в настройках главного окна), пишется
 * `PATCH /settings`. До ответа — последнее известное значение этого окна
 * (`SESSION_BAR_KEY`), чтобы скрытая строка не мелькала при каждом открытии.
 */

import { useCallback, useEffect, useState } from "react";

import { type Endpoint, getSettings, patchSettings } from "../lib/api";

export const SESSION_BAR_KEY = "meet.live.session-bar";

function cached(): boolean {
  try {
    return window.localStorage?.getItem(SESSION_BAR_KEY) !== "0";
  } catch {
    return true;
  }
}

function remember(shown: boolean): void {
  try {
    window.localStorage?.setItem(SESSION_BAR_KEY, shown ? "1" : "0");
  } catch { /* хранилище недоступно: значение — из настроек */ }
}

/** `ui.live_session_bar` из ответа `GET /settings`; нет — показана. */
export function sessionBarShown(settings: Record<string, unknown> | null | undefined): boolean {
  const ui = settings?.ui as { live_session_bar?: unknown } | undefined;
  return ui?.live_session_bar !== false;
}

export function useSessionBar(endpoint: Endpoint | null): { shown: boolean; setShown: (shown: boolean) => void; error: string | null } {
  const [shown, setLocal] = useState(cached);
  const [error, setError] = useState<string | null>(null);
  const base = endpoint?.base ?? null;
  const token = endpoint?.token ?? null;

  useEffect(() => {
    if (base === null) return;
    let alive = true;
    const read = () => {
      getSettings({ base, token })
        .then((s) => {
          if (!alive) return;
          const next = sessionBarShown(s);
          setLocal(next);
          remember(next);
        })
        .catch(() => {});
    };
    read();
    window.addEventListener("focus", read);
    return () => { alive = false; window.removeEventListener("focus", read); };
  }, [base, token]);

  const setShown = useCallback((next: boolean) => {
    setLocal(next);
    remember(next);
    setError(null);
    if (base === null) return;
    patchSettings({ base, token }, { ui: { live_session_bar: next } }).catch(() => {
      setError(next ? "Строку сессии не удалось вернуть" : "Строку сессии не удалось скрыть");
    });
  }, [base, token]);

  return { shown, setShown, error };
}
