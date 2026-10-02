/**
 * Живой ассистент глазами окна: лента строк, сводка, подсказки и вопросы.
 *
 * Поток `/live/events`. Обычный обрыв браузер чинит сам и переподключается с
 * Last-Event-ID — сервер досылает только пропущенные строки. Отказ (409:
 * ассистент ещё грузится или уже кончился) EventSource не повторяет — тогда
 * открываем поток заново с растущей паузой, пока режим идёт (`active`).
 * Новый поток начинает с хвоста ленты: строки с номером не больше последнего
 * показанного отбрасываем. Номера сквозные в пределах одного живого режима, а
 * хук живёт в окне одной записи — новый режим придёт в новый экземпляр.
 *
 * История вопросов живёт у ассистента (`qa` в `state`): её видят и панель, и
 * карточка, и вопрос в ней появляется сразу — с «Модель думает…».
 *
 * Действие с подсказкой (закрепить, скрыть) видно сразу, до ответа
 * ассистента: оно лежит поверх его состояния, пока следующее `state` не
 * пришло; не дошло — откатывается.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { type Endpoint, liveAsk, liveHint, liveTask, openLiveEvents } from "../lib/api";
import { errorText } from "../lib/format";
import type { LiveHint, LiveLine, LiveQa, LiveQuick, LiveSummary } from "../lib/types";
import { EMPTY_SUMMARY } from "./liveModel";

export const MAX_LINES = 300;
const RETRY_MIN_MS = 1000;
const RETRY_MAX_MS = 10_000;
const NO_LINK = "Нет связи с ассистентом — переподключаюсь…";

/** Строка ленты с её номером в потоке (`id:` события; null — без номера). */
export type FeedLine = LiveLine & { id: number | null };

export type AskOptions = { quick?: LiveQuick; since_t?: number };
export type HintAction = "pin" | "unpin" | "dismiss";

export type Live = {
  /** Тихий статус ассистента («Подсказки временно недоступны»), null — всё в порядке. */
  status: string | null;
  lines: FeedLine[];
  /** Сводка встречи, Markdown. */
  digest: string;
  summary: LiveSummary;
  /** Активные подсказки (скрытые сюда не попадают). */
  hints: LiveHint[];
  /** false — режим «Только сводка». */
  hintsEnabled: boolean;
  /** История вопросов (у ассистента). */
  qa: LiveQa[];
  /** Хоть одно `state` пришло: дальше новое — действительно новое. */
  loaded: boolean;
  /** Связи с ассистентом нет (переподключаемся), иначе null. */
  error: string | null;
  /** Запрос вопроса ушёл, ответа ещё нет. */
  asking: boolean;
  /** Вопрос не дошёл до ассистента (в историю он не попал). */
  askError: string | null;
  ask: (question: string, opts?: AskOptions) => Promise<void>;
  hint: (id: string, action: HintAction) => Promise<void>;
  setTask: (task: string) => Promise<void>;
};

/** Действие с подсказкой, которое ассистент ещё не подтвердил своим `state`. */
type Pending = Record<string, { action: HintAction; done: boolean }>;

function withPending(hints: LiveHint[], pending: Pending): LiveHint[] {
  return hints
    .filter((h) => pending[h.id]?.action !== "dismiss")
    .map((h) => {
      const action = pending[h.id]?.action;
      return action === "pin" ? { ...h, pinned: true } : action === "unpin" ? { ...h, pinned: false } : h;
    });
}

/** Ассистент принял действие — следующее `state` уже его отражает. */
const unconfirmed = (cur: Pending): Pending =>
  Object.fromEntries(Object.entries(cur).filter(([, p]) => !p.done));

export function useLive(ep: Endpoint | null, active = true): Live {
  const [lines, setLines] = useState<FeedLine[]>([]);
  const [digest, setDigest] = useState("");
  const [summary, setSummary] = useState<LiveSummary>(EMPTY_SUMMARY);
  const [hints, setHints] = useState<LiveHint[]>([]);
  const [hintsEnabled, setHintsEnabled] = useState(true);
  const [pending, setPending] = useState<Pending>({});
  const [qa, setQa] = useState<LiveQa[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const lastId = useRef(-1);
  const askSeq = useRef(0);

  useEffect(() => {
    if (!ep || !active) return;
    let closed = false;
    let stream: { close: () => void } | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = RETRY_MIN_MS;
    const alive = () => {
      delay = RETRY_MIN_MS;
      setError(null);
    };
    const connect = () => {
      stream = openLiveEvents(ep, {
        onState: (s) => {
          alive();
          setDigest(s.digest ?? "");
          setSummary(s.summary ?? EMPTY_SUMMARY);
          setHints(Array.isArray(s.hints) ? s.hints : []);
          setHintsEnabled(s.hints_enabled !== false);
          setPending(unconfirmed); // принятые ассистентом действия уже в его состоянии
          setStatus(s.status ?? null);
          setQa(Array.isArray(s.qa) ? s.qa : []);
          setLoaded(true);
        },
        onLine: (line, id) => {
          alive();
          if (id !== null) {
            if (id <= lastId.current) return;
            lastId.current = id;
          }
          setLines((cur) => {
            const next = [...cur, { ...line, id }];
            return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
          });
        },
        onError: (gaveUp) => {
          if (!gaveUp || closed) return; // браузер переподключится сам
          stream?.close();
          setError(NO_LINK);
          timer = setTimeout(connect, delay);
          delay = Math.min(delay * 2, RETRY_MAX_MS);
        },
      });
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      stream?.close();
      setError(null); // режим кончился — переподключаться больше некуда
    };
  }, [ep, active]);

  // Ответ после размонтирования или устаревший (задан новый вопрос) не применяем.
  useEffect(() => () => { askSeq.current++; }, []);

  const ask = useCallback(async (question: string, opts: AskOptions = {}) => {
    if (!ep) return;
    const mine = ++askSeq.current;
    setAsking(true);
    setAskError(null);
    try {
      await liveAsk(ep, question, opts);
    } catch (e) {
      if (mine === askSeq.current) setAskError(errorText(e));
    } finally {
      if (mine === askSeq.current) setAsking(false);
    }
  }, [ep]);

  const hint = useCallback(async (id: string, action: HintAction) => {
    if (!ep) return;
    setPending((cur) => ({ ...cur, [id]: { action, done: false } }));
    try {
      await liveHint(ep, id, action);
      setPending((cur) => (cur[id] ? { ...cur, [id]: { action, done: true } } : cur));
    } catch (e) {
      setPending((cur) => {
        const { [id]: _drop, ...rest } = cur;
        return rest;
      });
      setAskError(errorText(e));
    }
  }, [ep]);

  const setTask = useCallback(async (task: string) => {
    if (!ep) return;
    await liveTask(ep, task);
  }, [ep]);

  return {
    status, lines, digest, summary, hints: withPending(hints, pending), hintsEnabled, qa, loaded, error,
    asking, askError, ask, hint, setTask,
  };
}
