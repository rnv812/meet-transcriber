/**
 * Вкладка «Вопросы»: лента прошлых пар и поле вопроса.
 *
 * Вопрос — задача резидента (kind "ask"); ответ ляжет в `qa.jsonl`. Заданный
 * вопрос виден сразу со ступенью «Модель думает…»; когда его задача в списке
 * становится `done`, лента перечитывается и вопрос встаёт в неё с ответом.
 */

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ask, getQa, type Endpoint } from "../../lib/api";
import { dayLabel, errorText } from "../../lib/format";
import { Markdown } from "../../lib/markdown";
import { isActiveJob, modelJobsOf } from "../../lib/status";
import type { AssistantInfo, Job, QaItem } from "../../lib/types";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";
import { ProviderHint, ThinkingStage, noProvider, useLostJobs } from "./assistant";

type Pending = {
  key: number;
  q: string;
  jobId: string | null;
  error: string | null;
  /** Сколько раз этот же вопрос уже был в ленте, когда его задали. */
  before: number;
  /** Задача пропала из списка; ждём перечитывания ленты, чтобы решить, был ли ответ. */
  lost: boolean;
};

const LOST_TEXT = "Ответ не пришёл: задача пропала из очереди (резидент перезапускался?). Спросите ещё раз.";
const countOf = (items: QaItem[], q: string) => items.filter((it) => it.q === q).length;

export function QaTab({ endpoint, id, folder, jobs, assistant, onOpenSettings }: {
  endpoint: Endpoint;
  id: string;
  /** Папка записи: по ней задачи модели относятся к этой записи. */
  folder: string;
  jobs: Job[];
  assistant: AssistantInfo | null;
  onOpenSettings?: (section: string) => void;
}) {
  const [items, setItems] = useState<QaItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending[]>([]);
  const [text, setText] = useState("");
  const keys = useRef(0);
  const seq = useRef(0);
  const target = useRef({ endpoint, id });
  target.current = { endpoint, id };

  const mine = modelJobsOf(folder, jobs, "ask");
  const mineRef = useRef(mine);
  mineRef.current = mine;
  const byId = new Map(mine.map((j) => [j.id, j]));

  // Применяется только последний ответ и только для той записи, что показана сейчас.
  const load = useCallback(async () => {
    const mineSeq = ++seq.current;
    const fresh = () => mineSeq === seq.current && target.current.endpoint === endpoint && target.current.id === id;
    // Вопросы, чьи задачи уже готовы, есть в ответе — их «ожидание» снимаем.
    const done = new Set(mineRef.current.filter((j) => j.state === "done").map((j) => j.id));
    try {
      const data = await getQa(endpoint, id);
      if (!fresh()) return;
      setItems(data.items);
      setLoadError(null);
      setPending((ps) => ps.flatMap((p) => {
        if (p.jobId && done.has(p.jobId)) return [];
        if (!p.lost) return [p];
        // Задача пропала: ответ успел записаться — вопрос уже в ленте, иначе — ошибка.
        return countOf(data.items, p.q) > p.before ? [] : [{ ...p, lost: false, error: LOST_TEXT }];
      }));
    } catch (e) {
      if (!fresh()) return;
      setLoadError(errorText(e));
      setPending((ps) => ps.map((p) => (p.lost ? { ...p, lost: false, error: LOST_TEXT } : p)));
    }
  }, [endpoint, id]);

  // Другая запись — всё своё сначала.
  useEffect(() => {
    setItems(null);
    setLoadError(null);
    setPending([]);
  }, [endpoint, id]);
  const sig = mine.map((j) => `${j.id}:${j.state}`).join(",");
  useEffect(() => { void load(); }, [load, sig]);
  useEffect(() => () => { seq.current++; }, []);
  // Задача успела закончиться раньше, чем пришёл её id: перечитать ещё раз.
  const stale = pending.some((p) => p.jobId !== null && byId.get(p.jobId)?.state === "done");
  useEffect(() => { if (stale) void load(); }, [stale, load]);

  const stateOf = (p: Pending): { thinking: boolean; error: string | null } => {
    if (p.error) return { thinking: false, error: p.error };
    if (p.lost) return { thinking: true, error: null };
    const job = p.jobId ? byId.get(p.jobId) : undefined;
    if (job?.state === "failed") return { thinking: false, error: job.error || "Не удалось получить ответ" };
    if (job?.state === "cancelled") return { thinking: false, error: "Вопрос отменён" };
    return { thinking: true, error: null };
  };

  // Ждём ответа — следим, не пропала ли задача (перезапуск резидента, предел листинга).
  const tracked = pending.filter((p) => p.jobId !== null && !p.lost && stateOf(p).thinking).map((p) => p.jobId!);
  useLostJobs(tracked, jobs, (jobId) => {
    setPending((ps) => ps.map((p) => (p.jobId === jobId ? { ...p, lost: true } : p)));
    void load();
  });

  const blocked = noProvider(assistant);
  const waiting = mine.some(isActiveJob) || pending.some((p) => stateOf(p).thinking);
  const disabled = waiting || blocked;

  const submit = async () => {
    const question = text.trim();
    if (!question || disabled) return;
    const key = ++keys.current;
    const before = countOf(items ?? [], question);
    // Повтор вопроса, на который пришла ошибка, заменяет его в ленте.
    setPending((ps) => [
      ...ps.filter((p) => !(p.q === question && p.error)),
      { key, q: question, jobId: null, error: null, before, lost: false },
    ]);
    const patch = (change: Partial<Pending>) =>
      setPending((ps) => ps.map((p) => (p.key === key ? { ...p, ...change } : p)));
    try {
      patch({ jobId: (await ask(endpoint, id, question)).id });
      // Поле очищается только после того, как вопрос принят: при отказе текст остаётся.
      setText((cur) => (cur.trim() === question ? "" : cur));
    } catch (e) {
      patch({ error: errorText(e) });
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    void submit();
  };

  const empty = items !== null && items.length === 0 && pending.length === 0;

  return (
    <div className="assist qa">
      {loadError && <div className="assist__error" role="alert">{loadError}</div>}
      {items === null && !loadError && <EmptyState title="Загрузка…" />}
      {empty && (
        <EmptyState title="Вопросов пока не было" hint="Спросите что-нибудь о встрече — модель ответит по транскрипту." />
      )}
      {(items?.length || pending.length) ? (
        <ol className="qa__list">
          {(items ?? []).map((it, k) => (
            <li key={k} className="qa__item">
              <div className="qa__q">{it.q}</div>
              <Markdown source={it.a} className="qa__a" />
              <div className="qa__meta muted">
                {dayLabel(new Date(it.at * 1000).toISOString())}{it.provider ? ` · ${it.provider}` : ""}
              </div>
            </li>
          ))}
          {pending.map((p) => {
            const { thinking, error } = stateOf(p);
            return (
              <li key={`p${p.key}`} className="qa__item qa__item--pending">
                <div className="qa__q">{p.q}</div>
                {thinking ? <ThinkingStage /> : <div className="assist__error">{error}</div>}
              </li>
            );
          })}
        </ol>
      ) : null}
      <form className="qa__form" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        {blocked && <ProviderHint onOpenSettings={onOpenSettings} />}
        <div className="qa__row">
          <textarea
            className="qa__input" aria-label="Вопрос по встрече" rows={2}
            placeholder="Спросите о встрече. Enter — отправить, Shift+Enter — новая строка"
            value={text} disabled={disabled}
            onChange={(e) => setText(e.target.value)} onKeyDown={onKeyDown}
          />
          <Button type="submit" variant="primary" disabled={disabled || !text.trim()}>Спросить</Button>
        </div>
      </form>
    </div>
  );
}
