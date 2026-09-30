import { type Endpoint, recordingCommand } from "../lib/api";
import { clock } from "../lib/format";
import type { Snapshot } from "../lib/types";
import { Button } from "../ui/Button";

const LOW_DISK_GB = 5;

export function RecordingBadge({ endpoint, snapshot }: { endpoint: Endpoint | null; snapshot: Snapshot | null }) {
  if (!endpoint || !snapshot) return null;
  const recording = snapshot.status === "recording";
  const low = snapshot.disk_free_gb !== null && snapshot.disk_free_gb < LOW_DISK_GB;
  const run = (cmd: "start" | "stop") => void recordingCommand(endpoint, cmd).catch((e) => console.warn(cmd, e));
  return (
    <div className="rec-badge">
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
