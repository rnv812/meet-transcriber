/**
 * macOS: разрешения «Микрофон» и «Запись экрана» — шаг мастера «Разрешения» и
 * «Настройки → Диагностика». Состояние спрашивает резидент у помощника
 * `meet-audiotap` (без системных вопросов); «Разрешить» — системный вопрос
 * (macOS относит его к Meet: помощника запускает резидент), «Открыть
 * настройки» — раздел «Конфиденциальность и безопасность».
 *
 * Состояние перечитывается, когда окно получает фокус: человек вернулся из
 * «Системных настроек» или закрыл системный вопрос.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  type Endpoint, type PermissionKind, type Permissions, getPermissions, requestPermission,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { type PrivacyPane, openPrivacyPane } from "../../lib/shell";
import { Button } from "../../ui/Button";

const ROWS: { kind: PermissionKind; label: string; hint: string; pane: PrivacyPane }[] = [
  { kind: "microphone", label: "Микрофон", hint: "Ваш голос на встрече", pane: "microphone" },
  {
    kind: "screen", label: "Запись экрана", pane: "screen_recording",
    hint: "Звук собеседников: macOS отдаёт его только с этим разрешением (в macOS 15 — «Запись экрана и системного звука»)",
  },
];

const STATE: Record<string, { text: string; tone: string }> = {
  granted: { text: "Разрешено", tone: "badge--fresh" },
  denied: { text: "Не разрешено", tone: "badge--error" },
  notDetermined: { text: "Ещё не спрашивали", tone: "badge--stale" },
  restricted: { text: "Ограничено системой", tone: "badge--error" },
};
const UNKNOWN = { text: "Неизвестно", tone: "badge--plain" };

/** Задать системный вопрос ещё можно: уже отказанный микрофон macOS не переспрашивает. */
function canRequest(kind: PermissionKind, state: string | null): boolean {
  if (state === "granted") return false;
  return !(kind === "microphone" && (state === "denied" || state === "restricted"));
}

export function PermissionRows({ endpoint }: { endpoint: Endpoint }) {
  const [perms, setPerms] = useState<Permissions | null>(null);
  const [asking, setAsking] = useState<PermissionKind | null>(null);
  // Дали «Запись экрана», а macOS применит её только после перезапуска Meet.
  const [restart, setRestart] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const live = useRef(true);

  const load = useCallback(() => getPermissions(endpoint)
    .then((next) => { if (live.current) { setPerms(next); setError(null); } })
    .catch((cause) => { if (live.current) setError(`Не удалось узнать разрешения: ${errorText(cause)}`); }),
  [endpoint]);

  useEffect(() => {
    live.current = true;
    void load();
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => {
      live.current = false;
      window.removeEventListener("focus", onFocus);
    };
  }, [load]);

  const ask = async (kind: PermissionKind) => {
    setAsking(kind);
    try {
      const got = await requestPermission(endpoint, kind);
      if (!live.current) return;
      setPerms({ supported: got.supported, microphone: got.microphone, screen: got.screen });
      if (kind === "screen" && got.state !== "granted") setRestart(true);
      setError(null);
    } catch (cause) {
      if (live.current) setError(`Не удалось спросить macOS: ${errorText(cause)}`);
    } finally {
      if (live.current) setAsking(null);
    }
  };

  const open = (pane: PrivacyPane) =>
    void openPrivacyPane(pane).catch((cause) => console.warn("open_privacy_pane:", cause));

  return (
    <>
      {ROWS.map((row) => {
        const state = perms ? perms[row.kind] : null;
        const shown = state ? STATE[state] ?? UNKNOWN : perms ? UNKNOWN : null;
        return (
          <div className="srow" role="group" aria-label={row.label} key={row.kind}>
            <div className="srow__text">
              <span className="srow__label">{row.label}</span>
              <span className="srow__hint">
                {row.kind === "screen" && restart && state !== "granted"
                  ? "macOS применит разрешение после перезапуска Meet"
                  : row.hint}
              </span>
            </div>
            <div className="srow__control">
              {shown && <span className={`badge ${shown.tone}`}>{shown.text}</span>}
              {perms && canRequest(row.kind, state) && (
                <Button onClick={() => void ask(row.kind)} disabled={asking !== null}>
                  {asking === row.kind ? "Ждём ответа…" : "Разрешить"}
                </Button>
              )}
              {perms && state !== "granted" && (
                <Button variant="ghost" onClick={() => open(row.pane)}>Открыть настройки</Button>
              )}
            </div>
          </div>
        );
      })}
      {error && <p className="muted" role="alert">{error}</p>}
    </>
  );
}
