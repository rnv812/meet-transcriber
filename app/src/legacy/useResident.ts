/**
 * Подписка на резидента для React: снимок состояния, события, живость связи.
 *
 * Два канала намеренно: SSE даёт мгновенные события (уровни дорожек дважды в
 * секунду, решения детектора), а редкий опрос `/state` страхует от молча
 * умершего потока — панель, показывающая вчерашнее состояние, хуже пустой.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  NoResidentError,
  type Endpoint,
  type RecordingCommand,
  getState,
  openEvents,
  resolveEndpoint,
  sendCommand,
  startResident,
} from "./api";
import {
  isJob,
  isLevel,
  isLog,
  isProgress,
  type BusEvent,
  type Job,
  type ProgressEvent,
  type Snapshot,
} from "./types";

const POLL_MS = 4000;
const RECONNECT_MS = 2000;
const LOG_TAIL = 40;

export type Resident = {
  endpoint: Endpoint | null;
  snapshot: Snapshot | null;
  /** Ступень расшифровки, пока она идёт (иначе null). */
  progress: ProgressEvent | null;
  /** Последняя задача резидента: расшифровка со своим состоянием и ошибкой. */
  job: Job | null;
  /** Папка записи, которая только что закончилась, — чтобы предложить, что
   *  делать дальше, а не оставлять человека перед пустой панелью. */
  finished: string | null;
  /** Последние строки журнала дежурного — для раскрытой панели. */
  log: string[];
  /** Есть ли живая связь: false — резидент не запущен или не отвечает. */
  connected: boolean;
  error: string | null;
  command: (name: RecordingCommand) => Promise<void>;
  launch: () => Promise<void>;
  refresh: () => Promise<void>;
};

export function useResident(): Resident {
  const [endpoint, setEndpoint] = useState<Endpoint | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [progress, setProgress] = useState<ProgressEvent | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [finished, setFinished] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Уровни приходят чаще всего остального; держим их в снимке, чтобы у панели
  // был один источник правды, но не роняем на них весь ререндер дерева.
  const levels = useRef<Record<string, number>>({});
  // Ссылка на refresh(), а не сам refresh: обработчик событий не должен
  // пересоздаваться при каждой смене endpoint — иначе SSE переподключался бы.
  const refreshRef = useRef<(() => Promise<void>) | null>(null);

  // Время последнего SSE-события: пока поток жив, разовый промах страховочного
  // опроса не должен гасить панель в «offline».
  const lastEventAt = useRef(0);

  const applyEvent = useCallback((event: BusEvent) => {
    lastEventAt.current = Date.now();
    if (event.kind === "record.discarded") {
      // Запись отменена и папка удалена — снимаем предложение её расшифровать
      // (recorder успел прислать record.stopped с той же папкой).
      const folder = event.folder;
      setFinished((current) =>
        typeof folder === "string" && current === folder ? null : current,
      );
      void refreshRef.current?.();
      return;
    }
    if (isLevel(event)) {
      levels.current = event.levels ?? {};
      setSnapshot((current) =>
        current ? { ...current, levels: levels.current } : current,
      );
      return;
    }
    if (isProgress(event)) {
      // Последняя ступень — «сборка транскрипта» с указанным файлом: на ней
      // работа кончилась, и держать шкалу дальше незачем.
      const finished = event.stage === "render" && event.done !== null;
      setProgress(finished ? null : event);
      return;
    }
    if (isLog(event)) {
      setLog((lines) => [...lines, event.text].slice(-LOG_TAIL));
      return;
    }
    if (isJob(event)) {
      setJob(event.job);
      return;
    }
    if (event.kind === "record.started") {
      setFinished(null);
      void refreshRef.current?.();
      return;
    }
    if (event.kind === "record.stopped") {
      const folder = event.folder;
      if (typeof folder === "string") setFinished(folder);
      void refreshRef.current?.();
    }
  }, []);

  // Ссылка на reresolve для refresh: определяется ниже, а нужна здесь.
  const reresolveRef = useRef<(() => Promise<Endpoint | null>) | null>(null);

  const refresh = useCallback(async () => {
    if (!endpoint) return;
    try {
      const next = await getState(endpoint);
      setSnapshot({ ...next, levels: next.levels ?? levels.current });
      setConnected(true);
      setError(null);
    } catch {
      // Опрос не прошёл. Но если SSE только что присылал события — поток жив,
      // это разовый промах, и гасить панель в «offline» рано (иначе она мигает
      // на каждом фоновом сбое). Перечитываем адрес только когда молчат оба
      // канала: резидент действительно мог перезапуститься на новом порту.
      if (Date.now() - lastEventAt.current > POLL_MS * 2) {
        setConnected(false);
        void reresolveRef.current?.();
      }
    }
  }, [endpoint]);
  refreshRef.current = refresh;

  // Перечитать адрес резидента и обновить его, только если он изменился.
  //
  // IMPORTANT: резидент при каждом запуске берёт новый порт и токен. Панель
  // нашла его один раз при старте, а после перезапуска дежурного стучалась на
  // мёртвый порт («Failed to fetch»). Поэтому при обрыве адрес перечитывается,
  // а сравнение по значению не даёт лишних переподключений, когда он тот же.
  const reresolve = useCallback(async (): Promise<Endpoint | null> => {
    try {
      const found = await resolveEndpoint();
      setEndpoint((current) =>
        current && current.base === found.base && current.token === found.token
          ? current
          : found,
      );
      return found;
    } catch (cause) {
      setConnected(false);
      if (!(cause instanceof NoResidentError)) setError(String(cause));
      return null;
    }
  }, []);
  reresolveRef.current = reresolve;

  // Поиск резидента: пока его нет, пробуем снова — дежурный мог запуститься
  // после панели (или мы сами его сейчас поднимем).
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const find = async () => {
      if (cancelled) return;
      const found = await reresolve();
      if (!found && !cancelled) timer = window.setTimeout(find, RECONNECT_MS);
    };
    void find();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [reresolve]);

  // Поток событий: при обрыве перечитываем адрес (резидент мог перезапуститься
  // на новом порту) и переподключаемся, не теряя показанного состояния.
  useEffect(() => {
    if (!endpoint) return;
    let closed = false;
    let close: (() => void) | null = null;
    let timer: number | undefined;
    const connect = () => {
      close = openEvents(endpoint, {
        onSnapshot: (next) => {
          setSnapshot({ ...next, levels: next.levels ?? levels.current });
          setConnected(true);
          setError(null);
        },
        onEvent: applyEvent,
        onError: () => {
          setConnected(false);
          close?.();
          if (!closed) {
            // Если адрес сменился, эффект перезапустится с новым endpoint;
            // если тот же — пробуем этот через паузу.
            void reresolve();
            timer = window.setTimeout(connect, RECONNECT_MS);
          }
        },
      });
    };
    connect();
    return () => {
      closed = true;
      if (timer) window.clearTimeout(timer);
      close?.();
    };
  }, [endpoint, applyEvent, reresolve]);

  // Страховочный опрос.
  useEffect(() => {
    if (!endpoint) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [endpoint, refresh]);

  const command = useCallback(
    async (name: RecordingCommand) => {
      if (!endpoint) return;
      try {
        const result = await sendCommand(endpoint, name);
        setSnapshot({ ...result, levels: result.levels ?? {} });
        setError(result.ok ? null : describeRefusal(result.action));
      } catch (cause) {
        setError(String(cause));
      }
    },
    [endpoint],
  );

  const launch = useCallback(async () => {
    setError("запускаю дежурного…");
    try {
      // Ответ приходит по факту: команда ждёт, пока резидент начнёт отвечать.
      setError(await startResident());
    } catch (cause) {
      setError(String(cause));
    }
  }, []);

  return {
    endpoint,
    snapshot,
    progress,
    job,
    finished,
    log,
    connected,
    error,
    command,
    launch,
    refresh,
  };
}

/** Отказ команды — не ошибка, а состояние: панель говорит это словами. */
function describeRefusal(action: string): string {
  switch (action) {
    case "not-recording":
      return "Записи нет";
    case "already-recording":
      return "Запись уже идёт";
    default:
      return `Не выполнено: ${action}`;
  }
}
