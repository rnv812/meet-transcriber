import { useEffect, useRef, useState } from "react";

import { noProvider } from "../features/card/assistant";
import { type Endpoint, getAssistant, liveStart, liveStop, recordingCommand } from "../lib/api";
import { clock, errorText } from "../lib/format";
import type { AssistantInfo, LiveStatus, Snapshot } from "../lib/types";
import { Button } from "../ui/Button";

const LOW_DISK_GB = 5;
const ERROR_MS = 6000;
const TICK_MS = 1000;
const NO_PROVIDER = "Подключите Claude Code или Codex в настройках";

/** Общая часть ответов `/live/start` и `/live/stop` — новое `snapshot.live`. */
const liveOf = (r: LiveStatus): LiveStatus => ({
  active: r.active, starting: r.starting, stopping: r.stopping,
  folder: r.folder, error: r.error, started_at: r.started_at,
});

/**
 * Кнопка записи и таймер REC.
 *
 * Снимок приходит раз в несколько секунд, поэтому часы тикают локально:
 * `elapsed_s` из снимка плюс время, прошедшее с его прихода (`snapshotAt`).
 * Ответ команды записи — тоже снимок: применяем его сразу, не дожидаясь опроса.
 *
 * «▾» рядом с «Начать запись» — меню с записью «С ассистентом» (живой режим).
 * При нём `snapshot.status` остаётся "idle", а состояние — в `snapshot.live`;
 * часы живого режима идут от `started_at` (стенное время резидента).
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
  const live = snapshot?.live;
  const liveActive = !!live?.active;
  const elapsedS = snapshot?.elapsed_s;
  useEffect(() => {
    const t = Date.now();
    setSeenAt(t);
    setNow(t);
  }, [elapsedS, recording]);
  useEffect(() => {
    if (!recording && !liveActive) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [recording, liveActive]);

  // Меню «▾»: кто ответит — спрашиваем при каждом открытии, настройки могли смениться.
  const [menu, setMenu] = useState(false);
  const [assistant, setAssistant] = useState<AssistantInfo | null>(null);
  const split = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!menu || !endpoint) return;
    let current = true;
    getAssistant(endpoint).then((info) => { if (current) setAssistant(info); }).catch(() => {});
    return () => { current = false; };
  }, [menu, endpoint]);
  useEffect(() => {
    if (!menu) return;
    const down = (e: MouseEvent) => {
      if (split.current && !split.current.contains(e.target as Node)) setMenu(false);
    };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [menu]);

  if (!endpoint || !snapshot || !online) return null;
  const low = snapshot.disk_free_gb !== null && snapshot.disk_free_gb < LOW_DISK_GB;
  const since = Math.max(0, now - (snapshotAt ?? seenAt)) / 1000;
  const run = (cmd: "start" | "stop") => {
    setError(null);
    recordingCommand(endpoint, cmd)
      .then((result) => onSnapshot?.(result))
      .catch((e) => setError(errorText(e)));
  };
  const runLive = (call: typeof liveStart | typeof liveStop) => {
    setMenu(false);
    setError(null);
    call(endpoint)
      .then((result) => onSnapshot?.({ ...snapshot, live: liveOf(result) }))
      .catch((e) => setError(errorText(e)));
  };
  const blocked = noProvider(assistant);

  let main;
  if (live?.stopping) {
    main = (
      <>
        <span className="rec-badge__live">Останавливаю…</span>
        <Button variant="danger" disabled>Стоп</Button>
      </>
    );
  } else if (live?.starting) {
    main = (
      <>
        <span className="muted">Ассистент запускается…</span>
        <Button variant="danger" onClick={() => runLive(liveStop)}>Стоп</Button>
      </>
    );
  } else if (live?.active) {
    const liveS = live.started_at == null ? 0 : now / 1000 - live.started_at;
    main = (
      <>
        <span className="rec-badge__live num">● REC {clock(liveS)} · ассистент</span>
        <Button variant="danger" onClick={() => runLive(liveStop)}>Стоп</Button>
      </>
    );
  } else if (recording) {
    main = (
      <>
        <span className="rec-badge__live num">● REC {clock(snapshot.elapsed_s + since)}</span>
        {snapshot.source === "auto" && <span className="badge">авто</span>}
        <Button variant="danger" onClick={() => run("stop")}>Стоп</Button>
      </>
    );
  } else {
    main = (
      <span className="split" ref={split}>
        <Button variant="primary" className="split__main" onClick={() => run("start")}>Начать запись</Button>
        <Button variant="primary" className="split__more" aria-label="Другие варианты записи"
          aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>▾</Button>
        {menu && (
          <div className="rec-menu" role="menu" aria-label="Варианты записи">
            <button type="button" role="menuitem" className="rec-menu__item" disabled={blocked}
              onClick={() => runLive(liveStart)}>
              С ассистентом
              <span className="rec-menu__note">дайджест и вопросы по ходу встречи</span>
            </button>
            {blocked && <div className="rec-menu__hint">{NO_PROVIDER}</div>}
          </div>
        )}
      </span>
    );
  }

  return (
    <div className="rec-badge">
      {error && <span className="import__error" role="alert">{error}</span>}
      {low && <span className="rec-badge__warn">Мало места: {snapshot.disk_free_gb} ГБ</span>}
      {main}
    </div>
  );
}
