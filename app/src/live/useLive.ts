/**
 * Живой ассистент глазами окна: лента строк, дайджест и вопросы.
 *
 * Поток `/live/events`. Обычный обрыв браузер чинит сам и переподключается с
 * Last-Event-ID — сервер досылает только пропущенные строки. Отказ (409:
 * ассистент ещё грузится или уже кончился) EventSource не повторяет — тогда
 * открываем поток заново с растущей паузой, пока режим идёт (`active`).
 * Новый поток начинает с хвоста ленты: строки с номером не больше последнего
 * показанного отбрасываем. Номера сквозные в пределах одного живого режима, а
 * хук живёт в окне одной записи — новый режим придёт в новый экземпляр.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { type Endpoint, liveAsk, liveTask, openLiveEvents } from "../lib/api";
import { errorText } from "../lib/format";
import type { LiveLine } from "../lib/types";

export const MAX_LINES = 300;
const RETRY_MIN_MS = 1000;
const RETRY_MAX_MS = 10_000;
const NO_LINK = "Нет связи с ассистентом — переподключаюсь…";

/** Последний вопрос: ждём ответа, ответ или ошибка. */
/** Строка ленты с её номером в потоке (`id:` события; null — без номера). */
export type FeedLine = LiveLine & { id: number | null };

export type LiveReply = { pending: boolean; question: string | null; answer: string | null; error: string | null };

export type Live = {
  /** Статус дайджестера из `state` (что он сейчас делает), null — нет. */
  status: string | null;
  lines: FeedLine[];
  /** Дайджест встречи, Markdown. */
  digest: string;
  /** Связи с ассистентом нет (переподключаемся), иначе null. */
  error: string | null;
  reply: LiveReply;
  ask: (question: string) => Promise<void>;
  setTask: (task: string) => Promise<void>;
};

const IDLE: LiveReply = { pending: false, question: null, answer: null, error: null };

export function useLive(ep: Endpoint | null, active = true): Live {
  const [lines, setLines] = useState<FeedLine[]>([]);
  const [digest, setDigest] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reply, setReply] = useState<LiveReply>(IDLE);
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
          setStatus(s.status ?? null);
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

  const ask = useCallback(async (question: string) => {
    if (!ep) return;
    const mine = ++askSeq.current;
    setReply({ pending: true, question, answer: null, error: null });
    try {
      const { answer } = await liveAsk(ep, question);
      if (mine === askSeq.current) setReply({ pending: false, question, answer, error: null });
    } catch (e) {
      if (mine === askSeq.current) setReply({ pending: false, question, answer: null, error: errorText(e) });
    }
  }, [ep]);

  const setTask = useCallback(async (task: string) => {
    if (!ep) return;
    await liveTask(ep, task);
  }, [ep]);

  return { status, lines, digest, error, reply, ask, setTask };
}
