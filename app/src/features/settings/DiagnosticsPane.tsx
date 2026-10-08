import { useCallback, useEffect, useState } from "react";
import { Check, Copy, FolderOpen, RefreshCw } from "lucide-react";
import { type Endpoint, getDiagnostics } from "../../lib/api";
import { inTauri, openFolder } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { IconButton } from "../../ui/IconButton";
import { Loading } from "../../ui/Loading";
import { PathText } from "./Section";

export type Diagnostics = {
  watch_log?: string[];
  record_log?: string[];
  folder?: string | null;
  paths?: Record<string, string>;
  dev_mode?: boolean;
};

/** Подписи путей из `/diagnostics`; неизвестный ключ показывается как есть. */
const PATH_LABELS: Record<string, string> = {
  data_dir: "Папка данных",
  recordings: "Папка записей",
  config: "Файл настроек",
  watch_log: "Журнал автозаписи",
};

/** Папки можно открыть в проводнике (в приложении); файлы — только скопировать путь. */
const FOLDERS = new Set(["data_dir", "recordings"]);

function CopyPath({ path, label }: { path: string; label: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(path);
      setDone(true);
      window.setTimeout(() => setDone(false), 1500);
    } catch {
      /* буфер недоступен — путь виден текстом */
    }
  };
  return <IconButton icon={done ? Check : Copy} label={done ? "Скопировано" : `Копировать путь: ${label}`}
    tooltip={done ? "Скопировано" : "Копировать путь"} size="xs" onClick={() => void copy()} />;
}

export function DiagnosticsPane({ endpoint }: { endpoint: Endpoint }) {
  const [data, setData] = useState<Diagnostics | null>(null);
  const [tried, setTried] = useState(false);
  const load = useCallback(
    () => getDiagnostics(endpoint, 200).then((d) => setData(d as Diagnostics)).catch(() => setData(null))
      .finally(() => setTried(true)),
    [endpoint],
  );
  useEffect(() => { void load(); }, [load]);
  if (!data) return tried ? <p className="muted">Нет данных.</p> : <Loading label="Загружаю сведения…" />;
  return (
    <>
      {Object.entries(data.paths ?? {}).map(([name, path]) => {
        const label = PATH_LABELS[name] ?? name;
        return (
          <div className="srow" key={name}>
            <div className="srow__text"><span className="srow__label">{label}</span></div>
            <div className="srow__control">
              <span className="folder">
                <PathText path={path} />
                <CopyPath path={path} label={label} />
                {inTauri() && FOLDERS.has(name) && (
                  <IconButton icon={FolderOpen} label={`Открыть: ${label}`} tooltip="Открыть в проводнике" size="xs"
                    onClick={() => openFolder(path)} />
                )}
              </span>
            </div>
          </div>
        );
      })}
      <div className="srow">
        <div className="srow__text"><span className="srow__label">Режим</span></div>
        <div className="srow__control">
          <span className="tag">{data.dev_mode ? "Запуск из репозитория" : "Установленное приложение"}</span>
        </div>
      </div>
      <div className="diag__logs-head">
        <h3 className="shead">Журнал автозаписи</h3>
        <Button size="xs" variant="ghost" icon={RefreshCw} onClick={load}>Обновить журналы</Button>
      </div>
      <pre className="log">{(data.watch_log ?? []).slice(-120).join("\n") || "Журнал пуст"}</pre>
      <h3 className="shead">Журнал записи{data.folder ? ` — ${data.folder}` : ""}</h3>
      <pre className="log">{(data.record_log ?? []).join("\n") || "Журнал пуст"}</pre>
    </>
  );
}
