import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { type Endpoint, importFile } from "../../lib/api";
import { errorText } from "../../lib/format";
import { inTauri, overChatDrop, pickMedia } from "../../lib/shell";
import { IconButton } from "../../ui/IconButton";

export const BROWSER_HINT = "Импорт — из приложения или перетаскиванием в окно приложения";

/**
 * Импорт файлов: выбор файла, перетаскивание в окно (подписка оболочки — одна на
 * всё время жизни) и ошибки по каждому файлу. Общий для зоны импорта над списком и
 * пустой библиотеки на всё окно (LibraryEmpty): где он смонтирован, туда и
 * импортирует брошенный в окно файл. `dragDrop: false` — только выбор файла:
 * брошенный файл принимает зона над списком, смонтированная рядом (LibraryHome).
 */
export function useImportFiles(endpoint: Endpoint | null, onImported?: () => void,
  { dragDrop = true }: { dragDrop?: boolean } = {}) {
  const [errors, setErrors] = useState<string[]>([]);
  const [over, setOver] = useState(false);

  const importPaths = useCallback(
    async (paths: string[]) => {
      if (!endpoint) return;
      const failed: string[] = [];
      for (const path of paths) {
        try {
          await importFile(endpoint, path);
        } catch (cause) {
          const name = path.split(/[\\/]/).pop() || path;
          failed.push(`${name}: ${errorText(cause)}`);
        }
      }
      setErrors(failed); // удачный импорт стирает ошибки прошлого раза
      onImported?.();
    },
    [endpoint, onImported],
  );

  // Подписка на перетаскивание одна на всё время жизни: актуальный обработчик — через ref.
  const importRef = useRef(importPaths);
  importRef.current = importPaths;

  useEffect(() => {
    if (!inTauri() || !dragDrop) return;
    let off: (() => void) | undefined;
    let dead = false;
    void (async () => {
      const { getCurrentWebview } = await import("@tauri-apps/api/webview");
      const un = await getCurrentWebview().onDragDropEvent((e) => {
        const p = e.payload;
        // Над чатом ассистента (карточка идущей записи) файлы — вложения в чат, не импорт.
        const scale = window.devicePixelRatio || 1;
        const inChat = p.type !== "leave" && !!p.position && overChatDrop(p.position.x / scale, p.position.y / scale);
        if (inChat) setOver(false);
        else if (p.type === "enter" || p.type === "over") setOver(true);
        else if (p.type === "leave") setOver(false);
        else if (p.type === "drop") {
          setOver(false);
          void importRef.current(p.paths);
        }
      });
      if (dead) un();
      else off = un;
    })();
    return () => {
      dead = true;
      off?.();
    };
  }, [dragDrop]);

  const choose = async () => {
    if (!inTauri()) {
      setErrors([BROWSER_HINT]);
      return;
    }
    const path = await pickMedia();
    if (path) await importPaths([path]);
  };

  /** Перетаскивание в браузере: путей у файлов нет — подсказка вместо импорта. */
  const browserDrop = {
    onDragOver: (e: DragEvent) => { if (!inTauri()) e.preventDefault(); },
    onDrop: (e: DragEvent) => {
      if (inTauri()) return;
      e.preventDefault();
      setErrors([BROWSER_HINT]);
    },
  };

  return { errors, clearErrors: () => setErrors([]), over, choose, browserDrop };
}

/** Ошибки импорта по файлам, закрываются «×». */
export function ImportErrors({ errors, onClose, className = "" }: {
  errors: string[]; onClose: () => void; className?: string;
}) {
  if (!errors.length) return null;
  return (
    <div className={`import__error import__error--box ${className}`.trim()} role="alert">
      <div className="import__lines">
        {errors.map((line) => (
          <div key={line}>{line}</div>
        ))}
      </div>
      <IconButton icon={X} size="xs" label="Скрыть ошибки импорта" onClick={onClose} />
    </div>
  );
}

export function ImportZone({ endpoint, onImported }: { endpoint: Endpoint | null; onImported?: () => void }) {
  const { errors, clearErrors, over, choose, browserDrop } = useImportFiles(endpoint, onImported);

  return (
    <div className="import">
      <div
        className={`import__zone${over ? " import__zone--over" : ""}`}
        // В браузере файлы не дают путей: drop показывает подсказку.
        {...browserDrop}
      >
        Перетащите аудио или видео ·{" "}
        <button type="button" className="link" onClick={() => void choose()}>
          выбрать файл
        </button>
      </div>
      <ImportErrors errors={errors} onClose={clearErrors} />
    </div>
  );
}
