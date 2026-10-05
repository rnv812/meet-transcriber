/**
 * Связь с резидентом: SSE даёт мгновенные события, редкий опрос `/state`
 * страхует от молча умершего потока. Резидент при каждом запуске берёт новый
 * порт и токен, поэтому при обрыве адрес перечитывается.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { type Endpoint, NoResidentError, getState, openEvents, resolveEndpoint } from "../lib/api";
import { type BusEvent, type Snapshot, isLevel } from "../lib/types";

const POLL_MS = 4000;
const RECONNECT_MS = 2000;

/**
 * События, после которых снимок устарел сразу, а не к следующему опросу.
 * `live.*` — запись с ассистентом: её папка появляется в списке после
 * `live.started`, а встаёт в расшифровку после `live.stopped`. `recording.*` —
 * запись изменилась вне задач: обрезка ожидания после звонка началась
 * (`recording.processing`) или закончилась, выгрузка в базу знаний, объединение
 * завершено (`recording.updated`).
 */
export const stateChanging = (e: BusEvent) =>
  e.kind === "record.started" || e.kind === "record.stopped" || e.kind === "record.discarded"
  || e.kind.startsWith("live.") || e.kind.startsWith("recording.");

const refreshWorthy = (e: BusEvent) => e.kind.startsWith("job.") || stateChanging(e);
/** После них меняется само содержимое библиотеки, а не только прогресс задачи. */
const CONTENT_JOB_EVENTS = new Set(["job.queued", "job.done", "job.failed"]); // отмена приходит как job.failed
const contentChanging = (e: BusEvent) => CONTENT_JOB_EVENTS.has(e.kind) || stateChanging(e);

export type ResidentStatus = "connecting" | "online" | "offline";

export type Resident = {
  status: ResidentStatus;
  endpoint: Endpoint | null;
  snapshot: Snapshot | null;
  /** Когда пришёл снимок (Date.now()): от него тикает локальный таймер записи. */
  snapshotAt: number;
  /** Применить снимок, пришедший не из потока (ответ команды записи). */
  applySnapshot: (s: Snapshot) => void;
  /** Последнее событие шины (кроме уровней). */
  lastEvent: BusEvent | null;
  /** Растёт на каждое событие, после которого библиотеку надо перечитать.
   *  Счётчик, а не слот: пачка событий не затирает друг друга. */
  libraryTick: number;
  /** Как libraryTick, но без прогресса задач: только когда меняется содержимое
   *  библиотеки (поиск по тексту повторяется по нему). */
  contentTick: number;
  /** Растёт на каждое `job.done`: после расшифровки меняется база людей.
   *  Отдельно от libraryTick — тот растёт и на каждый прогресс задачи. */
  doneTick: number;
};

export function useResident(): Resident {
  const [endpoint, setEndpoint] = useState<Endpoint | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [snapshotAt, setSnapshotAt] = useState(0);
  const [lastEvent, setLastEvent] = useState<BusEvent | null>(null);
  const [libraryTick, setLibraryTick] = useState(0);
  const [doneTick, setDoneTick] = useState(0);
  const [contentTick, setContentTick] = useState(0);
  const [status, setStatus] = useState<ResidentStatus>("connecting");
  const levels = useRef<Record<string, number>>({});
  const lastEventAt = useRef(0);

  const reresolve = useCallback(async () => {
    try {
      const found = await resolveEndpoint();
      setEndpoint((cur) => (cur && cur.base === found.base && cur.token === found.token ? cur : found));
      return true;
    } catch (cause) {
      setStatus("offline");
      if (!(cause instanceof NoResidentError)) console.warn("resolveEndpoint:", cause);
      return false;
    }
  }, []);
  const reresolveRef = useRef(reresolve);
  reresolveRef.current = reresolve;

  const apply = useCallback((next: Snapshot) => {
    setSnapshot({ ...next, levels: next.levels ?? levels.current });
    setSnapshotAt(Date.now());
    setStatus("online");
  }, []);

  // Поиск резидента: пока его нет, пробуем снова.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const find = async () => {
      if (cancelled) return;
      const ok = await reresolve();
      if (!ok && !cancelled) timer = setTimeout(find, RECONNECT_MS);
    };
    void find();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [reresolve]);

  // Поток событий с переподключением.
  useEffect(() => {
    if (!endpoint) return;
    let closed = false;
    let close: (() => void) | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      close = openEvents(endpoint, {
        onSnapshot: apply,
        onEvent: (event) => {
          lastEventAt.current = Date.now();
          if (isLevel(event)) {
            levels.current = event.levels ?? {};
            setSnapshot((cur) => (cur ? { ...cur, levels: levels.current } : cur));
            return;
          }
          setLastEvent(event);
          if (refreshWorthy(event)) setLibraryTick((t) => t + 1);
          if (contentChanging(event)) setContentTick((t) => t + 1);
          if (event.kind === "job.done") setDoneTick((t) => t + 1);
          if (stateChanging(event)) {
            getState(endpoint).then((s) => { if (!closed) apply(s); }).catch(() => {});
          }
        },
        onError: () => {
          setStatus("offline");
          close?.();
          if (!closed) {
            void reresolveRef.current();
            timer = setTimeout(connect, RECONNECT_MS);
          }
        },
      });
    };
    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      close?.();
    };
  }, [endpoint, apply]);

  // Страховочный опрос.
  useEffect(() => {
    if (!endpoint) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const next = await getState(endpoint);
        if (!cancelled) apply(next);
      } catch {
        // Пока SSE жив, разовый промах опроса панель не гасит.
        if (!cancelled && Date.now() - lastEventAt.current > POLL_MS * 2) {
          setStatus("offline");
          void reresolveRef.current();
        }
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [endpoint, apply]);

  return { status, endpoint, snapshot, snapshotAt, applySnapshot: apply, lastEvent, libraryTick, contentTick, doneTick };
}
