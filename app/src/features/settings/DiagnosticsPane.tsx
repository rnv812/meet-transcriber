import { useEffect, useState } from "react";
import { type Endpoint, getDiagnostics } from "../../lib/api";

export type Diagnostics = {
  watch_log?: string[];
  record_log?: string[];
  folder?: string | null;
  paths?: Record<string, string>;
  dev_mode?: boolean;
};

export function DiagnosticsPane({ endpoint }: { endpoint: Endpoint }) {
  const [data, setData] = useState<Diagnostics | null>(null);
  const [tried, setTried] = useState(false);
  useEffect(() => {
    getDiagnostics(endpoint, 200).then((d) => setData(d as Diagnostics)).catch(() => setData(null)).finally(() => setTried(true));
  }, [endpoint]);
  if (!data) return <p className="muted">{tried ? "Нет данных." : "Загружаю…"}</p>;
  return (
    <>
      {Object.entries(data.paths ?? {}).map(([name, path]) => (
        <div className="srow" key={name}>
          <div className="srow__text"><span className="srow__label">{name}</span></div>
          <div className="srow__control"><code className="path">{path}</code></div>
        </div>
      ))}
      <div className="srow">
        <div className="srow__text"><span className="srow__label">Режим</span></div>
        <div className="srow__control">
          <span className="tag">{data.dev_mode ? "из репозитория" : "установленное приложение"}</span>
        </div>
      </div>
      <h3 className="shead">Журнал дежурного</h3>
      <pre className="log">{(data.watch_log ?? []).slice(-120).join("\n") || "пусто"}</pre>
      <h3 className="shead">Журнал записи{data.folder ? ` — ${data.folder}` : ""}</h3>
      <pre className="log">{(data.record_log ?? []).join("\n") || "записи нет"}</pre>
    </>
  );
}
