import { useCallback, useEffect, useState } from "react";

import { type Endpoint, importFile } from "../../lib/api";
import { inTauri, pickMedia } from "../../lib/shell";

const BROWSER_HINT = "Импорт — из приложения или перетаскиванием в окно приложения";

export function ImportZone({ endpoint, onImported }: { endpoint: Endpoint | null; onImported?: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);

  const importPaths = useCallback(
    async (paths: string[]) => {
      if (!endpoint) return;
      setError(null);
      for (const path of paths) {
        try {
          await importFile(endpoint, path);
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      }
      onImported?.();
    },
    [endpoint, onImported],
  );

  useEffect(() => {
    if (!inTauri()) return;
    let off: (() => void) | undefined;
    let dead = false;
    void (async () => {
      const { getCurrentWebview } = await import("@tauri-apps/api/webview");
      const un = await getCurrentWebview().onDragDropEvent((e) => {
        const p = e.payload;
        if (p.type === "enter" || p.type === "over") setOver(true);
        else if (p.type === "leave") setOver(false);
        else if (p.type === "drop") {
          setOver(false);
          void importPaths(p.paths);
        }
      });
      if (dead) un();
      else off = un;
    })();
    return () => {
      dead = true;
      off?.();
    };
  }, [importPaths]);

  const choose = async () => {
    if (!inTauri()) {
      setError(BROWSER_HINT);
      return;
    }
    const path = await pickMedia();
    if (path) await importPaths([path]);
  };

  return (
    <div className="import">
      <div
        className={`import__zone${over ? " import__zone--over" : ""}`}
        // В браузере файлы не дают путей: drop показывает подсказку.
        onDragOver={(e) => {
          if (!inTauri()) e.preventDefault();
        }}
        onDrop={(e) => {
          if (inTauri()) return;
          e.preventDefault();
          setError(BROWSER_HINT);
        }}
      >
        Перетащите аудио или видео сюда · или{" "}
        <button type="button" className="link" onClick={() => void choose()}>
          выбрать файл
        </button>
      </div>
      {error && (
        <div className="import__error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
