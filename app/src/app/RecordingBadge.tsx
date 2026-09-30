import { useEffect, useState } from "react";

import { type Endpoint, recordingCommand } from "../lib/api";
import { clock } from "../lib/format";
import type { Snapshot } from "../lib/types";
import { Button } from "../ui/Button";

const LOW_DISK_GB = 5;
const ERROR_MS = 6000;

export function RecordingBadge({ endpoint, snapshot }: { endpoint: Endpoint | null; snapshot: Snapshot | null }) {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), ERROR_MS);
    return () => clearTimeout(t);
  }, [error]);
  if (!endpoint || !snapshot) return null;
  const recording = snapshot.status === "recording";
  const low = snapshot.disk_free_gb !== null && snapshot.disk_free_gb < LOW_DISK_GB;
  const run = (cmd: "start" | "stop") => {
    setError(null);
    recordingCommand(endpoint, cmd).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  };
  return (
    <div className="rec-badge">
      {error && <span className="import__error" role="alert">{error}</span>}
      {low && <span className="rec-badge__warn">Мало места: {snapshot.disk_free_gb} ГБ</span>}
      {recording ? (
        <>
          <span className="rec-badge__live num">● REC {clock(snapshot.elapsed_s)}</span>
          {snapshot.source === "auto" && <span className="badge">авто</span>}
          <Button variant="danger" onClick={() => run("stop")}>Стоп</Button>
        </>
      ) : (
        <Button variant="primary" onClick={() => run("start")}>Начать запись</Button>
      )}
    </div>
  );
}
