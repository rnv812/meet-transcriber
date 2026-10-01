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
import { ProviderHint, ThinkingStage, noProvider } from "./assistant";

type Pending = { key: number; q: string; jobId: string | null; error: string | null };

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
  const seq = useRef(0);

  const mine = modelJobsOf(folder, jobs, "ask");
  const mineRef = useRef(mine);
  mineRef.current = mine;
  const byId = new Map(mine.map((j) => [j.id, j]));

  const load = useCallback(async () => {
    // Вопросы, чьи задачи уже готовы, есть в ответе — их «ожидание» снимаем.
    const done = new Set(mineRef.current.filter((j) => j.state === "done").map((j) => j.id));
    try {
      const data = await getQa(endpoint, id);
      setItems(data.items);
      setLoadError(null);
      setPending((ps) => ps.filter((p) => !(p.jobId && done.has(p.jobId))));
    } catch (e) {
      setLoadError(errorText(e));
    }
  }, [endpoint, id]);

  const sig = mine.map((j) => `${j.id}:${j.state}`).join(",");
  useEffect(() => { void load(); }, [load, sig]);
  // Задача успела закончиться раньше, чем пришёл её id: перечитать ещё раз.
  const stale = pending.some((p) => p.jobId !== null && byId.get(p.jobId)?.state === "done");
  useEffect(() => { if (stale) void load(); }, [stale, load]);

  const stateOf = (p: Pending): { thinking: boolean; error: string | null } => {
    if (p.error) return { thinking: false, error: p.error };
    const job = p.jobId ? byId.get(p.jobId) : undefined;
    if (job?.state === "failed") return { thinking: false, error: job.error || "Не удалось получить ответ" };
    if (job?.state === "cancelled") return { thinking: false, error: "Вопрос отменён" };
    return { thinking: true, error: null };
  };

  const blocked = noProvider(assistant);
  const waiting = mine.some(isActiveJob) || pending.some((p) => stateOf(p).thinking);
  const disabled = waiting || blocked;

  const submit = async () => {
    const question = text.trim();
    if (!question || disabled) return;
    const key = ++seq.current;
    setPending((ps) => [...ps, { key, q: question, jobId: null, error: null }]);
    setText("");
    const patch = (change: Partial<Pending>) =>
      setPending((ps) => ps.map((p) => (p.key === key ? { ...p, ...change } : p)));
    try {
      patch({ jobId: (await ask(endpoint, id, question)).id });
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
