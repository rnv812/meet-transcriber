/**
 * Вкладка «Ассистент» готовой записи: чат с агентом-участником после встречи.
 *
 * - **Журнал встречи** — та же лента, что во время встречи (`LiveChat`):
 *   сообщения агента с его кнопками, реакции, копирование, источники.
 * - **«Продолжить разговор»** — та же строка ввода (`ChatComposer`):
 *   сообщение уходит `continueChat` и ставит задачу ответа; кнопка агента —
 *   так же, своей надписью. Вложения после встречи — файл, перетаскивание,
 *   Ctrl+V; их разбирает резидент с теми же пределами, что во время встречи.
 * - **Ход ответа:** пока задача ответа ждёт или идёт, а пузыря ответа ещё нет —
 *   «Ассистент думает…» (с ходом задачи, если резидент его сообщает); текст
 *   ответа идёт событиями `chat.updated` (`partial`), запись — перечитыванием
 *   `GET /recordings/{id}/chat` по тому же событию. «Стоп» снимает задачу.
 * - **Старые встречи** (до 0.3.6) — прежние подсказки и вопросы только для
 *   чтения, под заголовком «Подсказки (старый ассистент)».
 * - **Чата нет** — «Спросить ассистента о встрече».
 *
 * Писать нельзя, как и у резидента: агент-участник выключен в настройках
 * (`assist.participant`), идёт живой режим этой записи или модель не
 * подключена — лента видна, строка ввода недоступна с причиной.
 */

import { MessageSquare } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Endpoint } from "../../lib/api";
import { clock, errorText } from "../../lib/format";
import { Markdown } from "../../lib/markdown";
import { isModelProgress } from "../../lib/progress";
import type { AssistantInfo, ChatUpdatedEvent, Job, LegacyAssistant, RecordingChat } from "../../lib/types";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";
import { JobProgress } from "../../ui/JobProgress";
import { ChatComposer } from "../../live/ChatComposer";
import { LiveChat } from "../../live/LiveChat";
import { recordingChatBackend } from "../../live/chatBackend";
import { KIND_LABEL } from "../../live/liveModel";
import { useChat } from "../../live/useChat";
import { noModelText, noProvider } from "./assistant";
import "../../live/live.css";

/** Быстрые вопросы после встречи (над пустым полем). */
export const AFTER_QUESTIONS = ["Кратко итоги", "Какие решения приняли?", "Что мне сделать?"];
/** Кто видит картинки (как `llm.VISION_PROVIDERS` у резидента). */
const VISION = new Set(["claude-code", "codex"]);
const ACTIVE = new Set(["queued", "running"]);

const norm = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();

/** Задача ответа этой записи, которая ждёт или идёт. */
export function activeChatJob(jobs: Job[], folder: string): Job | null {
  const mine = norm(folder);
  return jobs.find((j) => j.kind === "chat" && ACTIVE.has(j.state) && norm(j.folder) === mine) ?? null;
}

function Thinking({ job }: { job: Job }) {
  if (job.state === "running" && isModelProgress(job)) {
    return <div className="assist__stage assist__stage--progress" role="status"><JobProgress job={job} size="sm" /></div>;
  }
  return (
    <div className="assist__stage assist-chat__thinking" role="status">
      <span className="assist__pulse" aria-hidden="true" />
      Ассистент думает…
    </div>
  );
}

/** Прежний ассистент встречи (до 0.3.6): подсказки и вопросы — только чтение. */
function LegacyView({ legacy }: { legacy: LegacyAssistant }) {
  const hints = legacy.hints ?? [];
  const qa = legacy.qa ?? [];
  return (
    <section className="assist-legacy" aria-label="Подсказки (старый ассистент)">
      <h3 className="assist-legacy__title">Подсказки (старый ассистент)</h3>
      <p className="assist-legacy__note muted">Встреча записана до чата с ассистентом — это только для чтения.</p>
      {hints.length > 0 && (
        <ul className="assist-legacy__hints">
          {hints.map((h) => (
            <li key={h.id} className="assist-legacy__hint">
              <span className={`assist-legacy__kind live-hint--${h.kind}`}>{KIND_LABEL[h.kind] ?? h.kind}</span>
              {typeof h.source_t === "number" && <span className="assist-legacy__time num">{clock(h.source_t)}</span>}
              <span className="assist-legacy__text">{h.text}</span>
              {h.why && <span className="assist-legacy__why muted">{h.why}</span>}
            </li>
          ))}
        </ul>
      )}
      {qa.length > 0 && (
        <>
          <h4 className="assist-legacy__sub">Вопросы</h4>
          <ul className="qa__list">
            {qa.map((item, k) => (
              <li key={`${item.at}:${k}`} className="qa__item">
                <div className="qa__q">{item.q}</div>
                <Markdown source={item.a} className="md" />
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export function AssistantTab({ endpoint, id, folder, jobs, event = null, assistant = null, onOpenSettings }: {
  endpoint: Endpoint;
  /** Id записи (имя папки) — как `id` у события `chat.updated`. */
  id: string;
  folder: string;
  jobs: Job[];
  /** Последнее `chat.updated` этой записи: перечитать ленту или показать текст ответа. */
  event?: ChatUpdatedEvent | null;
  assistant?: AssistantInfo | null;
  onOpenSettings?: (section: string) => void;
}) {
  const [info, setInfo] = useState<RecordingChat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const jobRef = useRef<Job | null>(null);
  const backend = useMemo(() => recordingChatBackend(id, () => jobRef.current, setInfo), [id]);
  const chat = useChat(endpoint, backend);
  const chatRef = useRef(chat);
  chatRef.current = chat;

  // Перечитать ленту: один запрос за раз, просьбы за время запроса — ещё один после.
  const loading = useRef({ busy: false, again: false });
  const reload = useCallback(async () => {
    const st = loading.current;
    if (st.busy) { st.again = true; return; }
    st.busy = true;
    try {
      do {
        st.again = false;
        try {
          chatRef.current.sink.onChatSnapshot(await backend.get(endpoint), true);
          setError(null);
        } catch (e) {
          setError(errorText(e));
        }
      } while (st.again);
    } finally {
      st.busy = false;
    }
  }, [backend, endpoint]);
  useEffect(() => { void reload(); }, [reload]);

  // `chat.updated`: текст ответа, который пишется, — сразу; иначе (и если такой записи
  // окно ещё не видело) — перечитать ленту.
  useEffect(() => {
    if (!event || event.id !== id) return;
    const partial = event.partial;
    if (partial) chatRef.current.sink.onChatPartial(partial);
    if (!partial || !chatRef.current.state.byId[partial.id]) void reload();
  }, [event, id, reload]);

  const listed = info?.job && ACTIVE.has(info.job.state) && !jobs.some((j) => j.id === info.job!.id) ? info.job : null;
  const job = activeChatJob(jobs, folder) ?? listed;
  jobRef.current = job;
  // Задача кончилась (и событие могло потеряться) — перечитать.
  const jobId = job?.id ?? null;
  const lastJob = useRef(jobId);
  useEffect(() => {
    if (lastJob.current && !jobId) void reload();
    lastJob.current = jobId;
  }, [jobId, reload]);

  const enabled = info?.enabled !== false;
  const reason = !enabled ? "Чат с ассистентом выключен в настройках"
    : info?.live ? "Идёт живой режим этой записи — пишите ассистенту в панели встречи"
    : noProvider(assistant) ? noModelText(assistant) : null;
  const vision = !assistant?.provider || VISION.has(assistant.provider);
  const hasChat = chat.items.length > 0;
  const thinking = job && !chat.writing ? job : null;

  return (
    <div className="assist assist-chat" data-chat-drop="">
      {error && (
        <div className="assist__error" role="alert">
          Чат ассистента не загрузился: {error}{" "}
          <button type="button" className="link-btn" onClick={() => void reload()}>Повторить</button>
        </div>
      )}
      {info?.legacy && <LegacyView legacy={info.legacy} />}
      {!info && !error && <p className="muted assist-chat__loading">Загружаю чат ассистента…</p>}
      {info && (hasChat || asking || thinking) ? (
        <div className="chat-ws__main assist-chat__main">
          <LiveChat chat={chat} disabled={!!reason}
            empty="Спросите ассистента о встрече: он видит расшифровку, итоги и то, что вы приложите." />
          {thinking && <Thinking job={thinking} />}
          <div className="assist-chat__continue" role="group" aria-label="Продолжить разговор">
            <span className="assist-chat__label">Продолжить разговор</span>
            <ChatComposer chat={chat} disabledReason={reason} vision={vision} quick={AFTER_QUESTIONS}
              placeholder="Спросить о встрече…" autoFocus={asking && !hasChat} />
          </div>
        </div>
      ) : info ? (
        <EmptyState
          title={info.legacy ? "Чата с ассистентом у этой встречи нет" : "Ассистент на этой встрече не писал"}
          hint={reason ?? "Ассистент ответит по расшифровке и итогам встречи; можно приложить файлы."}
          action={reason && !enabled && onOpenSettings ? (
            <Button variant="link" onClick={() => onOpenSettings("assistant")}>Открыть настройки</Button>
          ) : (
            <Button icon={MessageSquare} disabled={!!reason} onClick={() => setAsking(true)}>
              Спросить ассистента о встрече
            </Button>
          )} />
      ) : null}
    </div>
  );
}
