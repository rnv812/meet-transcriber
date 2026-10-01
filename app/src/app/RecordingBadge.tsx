import { useEffect, useState } from "react";

import { type Endpoint, recordingCommand } from "../lib/api";
import { clock, errorText } from "../lib/format";
import type { Snapshot } from "../lib/types";
import { Button } from "../ui/Button";

const LOW_DISK_GB = 5;
const ERROR_MS = 6000;
const TICK_MS = 1000;

/**
 * Кнопка записи и таймер REC.
 *
 * Снимок приходит раз в несколько секунд, поэтому часы тикают локально:
 * `elapsed_s` из снимка плюс время, прошедшее с его прихода (`snapshotAt`).
 * Ответ команды записи — тоже снимок: применяем его сразу, не дожидаясь опроса.
 */
export function RecordingBadge({ endpoint, snapshot, snapshotAt, online = true, onSnapshot }: {
  endpoint: Endpoint | null;
  snapshot: Snapshot | null;
  /** Date.now() прихода снимка; без него — момент, когда бейдж его увидел. */
  snapshotAt?: number;
  /** Резидент на связи: иначе снимок устарел и кнопка ничего не сделает. */
  online?: boolean;
  onSnapshot?: (s: Snapshot) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [seenAt, setSeenAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), ERROR_MS);
    return () => clearTimeout(t);
  }, [error]);
  const recording = snapshot?.status === "recording";
  const elapsedS = snapshot?.elapsed_s;
  useEffect(() => {
    const t = Date.now();
    setSeenAt(t);
    setNow(t);
  }, [elapsedS, recording]);
  useEffect(() => {
    if (!recording) return;
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [recording]);

  if (!endpoint || !snapshot || !online) return null;
  const low = snapshot.disk_free_gb !== null && snapshot.disk_free_gb < LOW_DISK_GB;
  const since = Math.max(0, now - (snapshotAt ?? seenAt)) / 1000;
  const run = (cmd: "start" | "stop") => {
    setError(null);
    recordingCommand(endpoint, cmd)
      .then((result) => onSnapshot?.(result))
      .catch((e) => setError(errorText(e)));
  };
  return (
    <div className="rec-badge">
      {error && <span className="import__error" role="alert">{error}</span>}
      {low && <span className="rec-badge__warn">Мало места: {snapshot.disk_free_gb} ГБ</span>}
      {recording ? (
        <>
          <span className="rec-badge__live num">● REC {clock(snapshot.elapsed_s + since)}</span>
          {snapshot.source === "auto" && <span className="badge">авто</span>}
          <Button variant="danger" onClick={() => run("stop")}>Стоп</Button>
        </>
      ) : (
        <Button variant="primary" onClick={() => run("start")}>Начать запись</Button>
      )}
    </div>
  );
}
