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
 * История вопросов живёт у ассистента (событие `qa`): её видят и панель, и
 * карточка, и вопрос в ней появляется сразу — с «Модель думает…». Ответ,
 * который ещё пишется, приходит кусками (`qa_partial`) и виден по мере
 * генерации; окно обновляет его не чаще 10 раз в секунду (PARTIAL_MS).
 *
 * Имя голоса приходит позже его первых строк (ассистенту нужно ~10 с речи):
 * событие `voices` — карта «ключ голоса → подпись» и номера спрятанных
 * строк-дублей, состояние целиком. Строки хранятся как пришли, а лента
 * показывает их через карту (`useMemo`); при каждом подключении ассистент
 * шлёт карту заново — переподключение и новый ассистент её не путают. Ключи
 * голосов несут метку сеанса ассистента, а номера спрятанных строк относятся
 * только к строкам того же ассистента (`session`): строка помнит, от какого
 * ассистента пришла.
 *
 * Действие с подсказкой (закрепить, скрыть) видно сразу, до ответа
 * ассистента: оно лежит поверх его состояния, пока следующее `state` не
 * пришло; не дошло — откатывается.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type Endpoint, liveAsk, liveHint, liveTask, openLiveEvents } from "../lib/api";
import { errorText } from "../lib/format";
import type { LiveCatchup, LiveHint, LiveLine, LiveQa, LiveQuick, LiveSummary } from "../lib/types";
import { EMPTY_SUMMARY } from "./liveModel";

export const MAX_LINES = 300;
const RETRY_MIN_MS = 1000;
const RETRY_MAX_MS = 10_000;
const NO_LINK = "Нет связи с ассистентом — переподключаюсь…";
/** Частичный ответ в окне — не чаще раза в 100 мс (≤ 10 обновлений в секунду). */
export const PARTIAL_MS = 100;

/** Строка ленты с её номером в потоке (`id:` события; null — без номера) и
 * меткой ассистента, от которого пришла (`session`). */
export type FeedLine = LiveLine & { id: number | null; session?: string };

/**
 * Новая строка в ленту. Строка догнанного начала встречи (`catchup`)
 * приходит позже живых, а стоит раньше: она встаёт перед первой живой
 * строкой (догонялка идёт по времени — порядок внутри сохраняется).
 * Переполнение срезает самые старые.
 */
export function addLine(cur: FeedLine[], line: FeedLine, max = MAX_LINES): FeedLine[] {
  let next: FeedLine[];
  if (line.catchup) {
    const at = cur.findIndex((l) => !l.catchup);
    next = at < 0 ? [...cur, line] : [...cur.slice(0, at), line, ...cur.slice(at)];
  } else {
    next = [...cur, line];
  }
  return next.length > max ? next.slice(next.length - max) : next;
}

export type AskOptions = { quick?: LiveQuick; since_t?: number };
/** `restore` — «Вернуть» сразу после «Скрыть» (ассистент помнит скрытую несколько секунд). */
export type HintAction = "pin" | "unpin" | "dismiss" | "restore";

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
  /** «Не отвлекать по умолчанию» из настроек; null — состояние ещё не пришло. */
  quietDefault: boolean | null;
  /** Ассистент включён посреди записи: ход догонялки начала встречи; null — её нет. */
  catchup: LiveCatchup | null;
  /** История вопросов (у ассистента); у ответа, который пишется, — `partial`. */
  qa: LiveQa[];
  /** Хоть одно `state` пришло: дальше новое — действительно новое. */
  loaded: boolean;
  /** Связи с ассистентом нет (переподключаемся), иначе null. */
  error: string | null;
  /** Запрос вопроса ушёл, ответа ещё нет. */
  asking: boolean;
  /** Вопрос не дошёл до ассистента (в историю он не попал). */
  askError: string | null;
  /** Действие с подсказкой не дошло: у какой подсказки и почему. */
  hintError: { id: string; text: string; action?: HintAction } | null;
  ask: (question: string, opts?: AskOptions) => Promise<void>;
  /** → false: ассистент ничего не изменил (подсказки уже нет); void — не дошло или без связи. */
  hint: (id: string, action: HintAction) => Promise<boolean | void>;
  setTask: (task: string) => Promise<void>;
};

/** Подписи голосов задним числом и спрятанные строки (`event: voices`). */
type Voices = { speakers: Record<string, string>; hidden: Set<number>; session?: string };
const NO_VOICES: Voices = { speakers: {}, hidden: new Set() };

/** Лента глазами человека: без спрятанных дублей, с подписями голосов на сейчас. */
export function voicedLines(lines: FeedLine[], voices: Voices): FeedLine[] {
  if (!voices.hidden.size && !Object.keys(voices.speakers).length) return lines;
  return lines
    .filter((l) => l.id === null || l.session !== voices.session || !voices.hidden.has(l.id))
    .map((l) => {
      const speaker = l.voice ? voices.speakers[l.voice] : undefined;
      return speaker !== undefined && speaker !== l.speaker ? { ...l, speaker } : l;
    });
}

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
  const [quietDefault, setQuietDefault] = useState<boolean | null>(null);
  const [catchup, setCatchup] = useState<LiveCatchup | null>(null);
  const [pending, setPending] = useState<Pending>({});
  const [qa, setQa] = useState<LiveQa[]>([]);
  const [partials, setPartials] = useState<Record<number, string>>({});
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [hintError, setHintError] = useState<{ id: string; text: string; action?: HintAction } | null>(null);
  const [voices, setVoices] = useState<Voices>(NO_VOICES);
  const lastId = useRef(-1);
  const session = useRef<string | undefined>(undefined);
  const askSeq = useRef(0);

  useEffect(() => {
    if (!ep || !active) return;
    let closed = false;
    let stream: { close: () => void } | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = RETRY_MIN_MS;
    // Куски ответа копятся здесь и уходят в состояние не чаще PARTIAL_MS.
    let latest: Record<number, string> = {};
    let partialTimer: ReturnType<typeof setTimeout> | undefined;
    let partialAt = 0;
    const flushPartials = () => {
      partialTimer = undefined;
      partialAt = Date.now();
      const snapshot = { ...latest };
      setPartials(snapshot);
    };
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
          setCatchup(s.catchup ?? null);
          setHintsEnabled(s.hints_enabled !== false);
          setQuietDefault(s.prefs?.quiet_default === true);
          setPending(unconfirmed); // принятые ассистентом действия уже в его состоянии
          setStatus(s.status ?? null);
          if (Array.isArray(s.qa)) setQa(s.qa);
          setLoaded(true);
        },
        onQa: (items) => {
          alive();
          setQa(items);
          // Готовый ответ пришёл в историю — его частичный текст больше не нужен.
          const pending = new Set(items.filter((it) => it.pending).map((it) => it.id));
          latest = Object.fromEntries(Object.entries(latest).filter(([id]) => pending.has(Number(id))));
          setPartials((cur) => Object.fromEntries(Object.entries(cur).filter(([id]) => pending.has(Number(id)))));
        },
        onQaPartial: ({ id, a }) => {
          alive();
          latest = { ...latest, [id]: a };
          if (partialTimer !== undefined) return;
          const wait = partialAt + PARTIAL_MS - Date.now();
          if (wait <= 0) flushPartials();
          else partialTimer = setTimeout(flushPartials, wait);
        },
        onLine: (line, id) => {
          alive();
          if (id !== null) {
            if (id <= lastId.current) return;
            lastId.current = id;
          }
          const from = session.current;
          setLines((cur) => addLine(cur, { ...line, id, ...(from ? { session: from } : {}) }));
        },
        onVoices: (v) => {
          alive();
          session.current = v.session;
          setVoices({ speakers: v.speakers, hidden: new Set(v.hidden), session: v.session });
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
      clearTimeout(partialTimer);
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
    setHintError(null);
    setPending((cur) => ({ ...cur, [id]: { action, done: false } }));
    try {
      const reply = await liveHint(ep, id, action);
      setPending((cur) => (cur[id] ? { ...cur, [id]: { action, done: true } } : cur));
      return reply?.changed !== false;
    } catch (e) {
      setPending((cur) => {
        const { [id]: _drop, ...rest } = cur;
        return rest;
      });
      setHintError({ id, text: errorText(e), action });
    }
  }, [ep]);

  const setTask = useCallback(async (task: string) => {
    if (!ep) return;
    await liveTask(ep, task);
  }, [ep]);

  const linesView = useMemo(() => voicedLines(lines, voices), [lines, voices]);

  const qaView = useMemo(
    () => qa.map((it) => (it.pending && partials[it.id] ? { ...it, partial: partials[it.id] } : it)),
    [qa, partials],
  );

  return {
    status, lines: linesView, digest, summary, hints: withPending(hints, pending), hintsEnabled, quietDefault, catchup, qa: qaView, loaded, error,
    asking, askError, hintError, ask, hint, setTask,
  };
}
