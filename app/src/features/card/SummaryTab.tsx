/**
 * Вкладка «Итоги»: Markdown от модели, «Переделать», «В заметки», «Копировать».
 *
 * Итоги делает задача резидента (kind "summary"); её состояние приходит
 * списком задач карточки. Любая смена состояния задачи перечитывает итоги —
 * так `job.done` показывает свежий текст без отдельной подписки.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, getSummary, makeSummary, toNotes, type Endpoint } from "../../lib/api";
import { dayLabel, errorText } from "../../lib/format";
import { Markdown } from "../../lib/markdown";
import { isActiveJob, modelJobsOf } from "../../lib/status";
import type { AssistantInfo, Job, Summary } from "../../lib/types";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";
import { ProviderHint, ThinkingStage, noProvider } from "./assistant";

const COPIED_MS = 2000;

export function SummaryTab({ endpoint, id, folder, jobs, assistant, onOpenSettings }: {
  endpoint: Endpoint;
  id: string;
  /** Папка записи: по ней задачи модели относятся к этой записи. */
  folder: string;
  jobs: Job[];
  assistant: AssistantInfo | null;
  onOpenSettings?: (section: string) => void;
}) {
  /** undefined — грузится, null — итогов нет. */
  const [summary, setSummary] = useState<Summary | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [notesPath, setNotesPath] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const mine = modelJobsOf(folder, jobs, "summary");
  // Только что поставленная задача может ещё не дойти до списка — она и есть последняя.
  const latest = submitted && !mine.some((j) => j.id === submitted.id) ? submitted : mine.at(-1) ?? null;
  const thinking = latest !== null && isActiveJob(latest);
  const failed = latest?.state === "failed" ? latest : null;
  const sig = mine.map((j) => `${j.id}:${j.state}`).join(",");

  const load = useCallback(async () => {
    try {
      setSummary(await getSummary(endpoint, id));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setSummary(null);
      else setError(errorText(e));
    }
  }, [endpoint, id]);

  useEffect(() => { void load(); }, [load, sig]);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };
  const make = () => act(async () => { setSubmitted(await makeSummary(endpoint, id)); });
  const save = () => act(async () => { setNotesPath((await toNotes(endpoint, id)).path); });
  const copy = (markdown: string) => act(async () => {
    try {
      await navigator.clipboard.writeText(markdown);
    } catch {
      throw new Error("Не удалось скопировать");
    }
    setCopied(true);
  });

  const blocked = noProvider(assistant);
  const hint = blocked ? <ProviderHint onOpenSettings={onOpenSettings} /> : null;
  const canMake = !busy && !thinking && !blocked;

  let main;
  if (summary) {
    main = (
      <>
        <div className="assist__toolbar">
          <Button onClick={make} disabled={!canMake}>Переделать</Button>
          {assistant?.notes_dir && <Button onClick={save} disabled={busy}>В заметки</Button>}
          <Button onClick={() => copy(summary.markdown)} disabled={busy}>{copied ? "Скопировано" : "Копировать"}</Button>
          {typeof summary.created_at === "number" && (
            <span className="muted assist__when">{dayLabel(new Date(summary.created_at * 1000).toISOString())}</span>
          )}
        </div>
        {hint}
        {notesPath && (
          <div className="assist__saved">Сохранено в заметки: <code className="assist__path">{notesPath}</code></div>
        )}
        <Markdown source={summary.markdown} className="assist__md" />
      </>
    );
  } else if (summary === null && !thinking && !failed) {
    main = (
      <EmptyState
        title="Итогов пока нет"
        hint="Модель прочитает транскрипт и выпишет решения, задачи и сроки."
        action={<>
          <Button variant="primary" onClick={make} disabled={!canMake}>Сделать итоги</Button>
          {hint}
        </>}
      />
    );
  } else if (summary === undefined && !thinking && !error) {
    main = <EmptyState title="Загрузка…" />;
  }

  return (
    <div className="assist">
      {error && <div className="assist__error" role="alert">{error}</div>}
      {thinking && <ThinkingStage />}
      {failed && (
        <div className="assist__failed">
          <div className="assist__error">{failed.error || "Не удалось сделать итоги"}</div>
          <Button onClick={make} disabled={!canMake}>Повторить</Button>
          {!summary && hint}
        </div>
      )}
      {main}
    </div>
  );
}
