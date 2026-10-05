/**
 * Данные панели записи: снимок резидента, последняя запись, кто ответит
 * ассистенту.
 *
 * Пока панель на экране — поток событий резидента (`/events`, тот же
 * `openEvents`, что у окна): первым сообщением он присылает снимок, на
 * `record.*`, `live.*` и `job.done` снимок и последняя запись перечитываются.
 * Спрятанная панель (событие оболочки `tray-panel`) поток закрывает и не
 * делает ни одного запроса; показанная — открывает заново и сразу получает
 * свежий снимок. Своего опроса нет.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type Endpoint, NoResidentError, getAssistant, getRecentRecordings, getState, openEvents, resolveEndpoint,
} from "../lib/api";
import { onTrayPanel, trayPanelVisible } from "../lib/shell";
import { stateChanging } from "../state/useResident";
import type { AssistantInfo, BusEvent, Recording, Snapshot } from "../lib/types";
import { latestSaved, stoppedBetween } from "./trayModel";

const RECONNECT_MS = 2000;
/** Столько свежих папок берём, чтобы среди них нашлась не идущая запись. */
const RECENT_LIMIT = 3;

/**
 * После каких событий перечитать снимок: те же, что у окна (`stateChanging`),
 * и подмена устройства или пропавший звук собеседников — панель показывает
 * их у таймера. Только перечнем: `record.level` приходит дважды в секунду, и
 * `/state` на каждый был бы опросом 2 раза в секунду.
 */
const PANEL_STATE_EVENTS = new Set(["record.device_fallback", "record.device_pinned", "record.system_audio"]);
const changesState = (e: BusEvent) => stateChanging(e) || PANEL_STATE_EVENTS.has(e.kind);
const changesLibrary = (e: BusEvent) =>
  e.kind === "record.stopped" || e.kind === "record.discarded" || e.kind === "live.stopped"
  || e.kind === "job.done" || e.kind === "recording.updated";

export type TrayData = {
  visible: boolean;
  /** Растёт при каждом показе: панель заново проигрывает появление. */
  shownTick: number;
  endpoint: Endpoint | null;
  online: boolean | null;
  snapshot: Snapshot | null;
  snapshotAt: number;
  recordings: Recording[];
  /** Остановку, которую панель увидела сама: какая запись и когда. */
  stopped: { id: string; at: number } | null;
  assistant: AssistantInfo | null;
  applySnapshot: (s: Snapshot) => void;
};

export function useTrayPanel(): TrayData {
  const [visible, setVisible] = useState(true);
  const [shownTick, setShownTick] = useState(0);
  const [endpoint, setEndpoint] = useState<Endpoint | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [snapshotAt, setSnapshotAt] = useState(0);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [stopped, setStopped] = useState<{ id: string; at: number } | null>(null);
  const [assistant, setAssistant] = useState<AssistantInfo | null>(null);
  const last = useRef<Snapshot | null>(null);

  const applySnapshot = useCallback((next: Snapshot) => {
    const id = stoppedBetween(last.current, next);
    if (id) setStopped({ id, at: Date.now() });
    last.current = next;
    setSnapshot(next);
    setSnapshotAt(Date.now());
    setOnline(true);
  }, []);

  // Показ и скрытие окна оболочкой. Подписка асинхронная — событие,
  // пришедшее до неё, потерялось бы: видно ли окно, спрашиваем и сами.
  useEffect(() => {
    let gone = false;
    let off: (() => void) | null = null;
    trayPanelVisible()
      .then((shown) => { if (!gone && shown === false) setVisible(false); })
      .catch(() => {});
    onTrayPanel((shown) => {
      setVisible(shown);
      if (shown) setShownTick((t) => t + 1);
    })
      .then((unlisten) => { if (gone) unlisten(); else off = unlisten; })
      .catch((cause) => console.warn("tray-panel:", cause));
    return () => { gone = true; off?.(); };
  }, []);

  // Адрес резидента: ищем, пока панель видна и его нет.
  useEffect(() => {
    if (!visible || endpoint) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const find = () => {
      resolveEndpoint()
        .then((found) => { if (!cancelled) setEndpoint(found); })
        .catch((cause) => {
          if (cancelled) return;
          if (!(cause instanceof NoResidentError)) console.warn("resolveEndpoint:", cause);
          setOnline(false);
          timer = setTimeout(find, RECONNECT_MS);
        });
    };
    find();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [visible, endpoint]);

  const refreshLibrary = useCallback((ep: Endpoint) => {
    getRecentRecordings(ep, RECENT_LIMIT)
      .then((r) => setRecordings(r.items))
      .catch(() => {});
  }, []);

  // Поток событий — только пока панель видна.
  useEffect(() => {
    if (!visible || !endpoint) return;
    let closed = false;
    let close: (() => void) | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let seq = 0;
    refreshLibrary(endpoint);
    getAssistant(endpoint).then((info) => { if (!closed) setAssistant(info); }).catch(() => {});
    const connect = () => {
      close = openEvents(endpoint, {
        onSnapshot: (s) => { seq++; if (!closed) applySnapshot(s); },
        onEvent: (e) => {
          if (changesLibrary(e)) refreshLibrary(endpoint);
          if (!changesState(e)) return;
          const mine = ++seq;
          getState(endpoint).then((s) => { if (!closed && mine === seq) applySnapshot(s); }).catch(() => {});
        },
        onError: () => {
          close?.();
          if (closed) return;
          setOnline(false);
          // Резидент перезапустился — у него новый порт и токен.
          resolveEndpoint()
            .then((found) => {
              if (closed) return;
              if (found.base !== endpoint.base || found.token !== endpoint.token) setEndpoint(found);
              else timer = setTimeout(connect, RECONNECT_MS);
            })
            .catch(() => { if (!closed) timer = setTimeout(connect, RECONNECT_MS); });
        },
      });
    };
    connect();
    return () => { closed = true; clearTimeout(timer); close?.(); };
  }, [visible, endpoint, applySnapshot, refreshLibrary]);

  return {
    visible, shownTick, endpoint, online, snapshot, snapshotAt, recordings, stopped, assistant,
    applySnapshot,
  };
}

/** Последняя сохранённая запись из данных панели. */
export const recentOf = (data: Pick<TrayData, "recordings" | "snapshot">) =>
  latestSaved(data.recordings, data.snapshot);
