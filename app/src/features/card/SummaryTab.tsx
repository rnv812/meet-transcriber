/**
 * Вкладка «Итоги»: Markdown от модели, «Переделать», «Копировать». Выгрузка в
 * базу знаний — кнопкой карточки «В базу знаний» (итоги уходят вместе с ней).
 *
 * Итоги делает задача резидента (kind "summary"); её состояние приходит
 * списком задач карточки. Любая смена состояния задачи перечитывает итоги —
 * так `job.done` показывает свежий текст без отдельной подписки.
 *
 * Пока итогов нет, а запись шла с ассистентом, — «Черновик из живого
 * режима»: сводка, которую ассистент вёл во время встречи. Задача итогов
 * получает её же и сверяет с полной расшифровкой.
 *
 * У каждого пункта и строки таблицы — ✦ «Спросить агента об этом пункте»
 * (`onAskAgent`): ссылка на пункт уходит в поле ввода вкладки «Агент».
 */

import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { AgentRequest } from "../../lib/agentRef";
import { ApiError, getLiveDraft, getSummary, makeSummary, type Endpoint } from "../../lib/api";
import { dayLabel, errorText } from "../../lib/format";
import { JiraLinks } from "../../lib/jira";
import { anyModelReady, llmLabel, modelChoices, modelReady, retryText } from "../../lib/llm";
import { Markdown, type ItemAction } from "../../lib/markdown";
import { isActiveJob, modelJobsOf } from "../../lib/status";
import type { AssistantInfo, Job, LiveDraft, Summary } from "../../lib/types";
import { AskAgentButton } from "../../ui/AskAgent";
import { Button } from "../../ui/Button";
import { useConfirm } from "../../ui/ConfirmDialog";
import { EmptyState } from "../../ui/EmptyState";
import { ProviderHint, ThinkingStage, noModelText, noProvider, useLostJobs } from "./assistant";
import { ModelSplitButton } from "./modelPick";

const COPIED_MS = 2000;

export function SummaryTab({ endpoint, id, folder, jobs, assistant, onOpenSettings, onAskAgent }: {
  endpoint: Endpoint;
  id: string;
  /** Папка записи: по ней задачи модели относятся к этой записи. */
  folder: string;
  jobs: Job[];
  assistant: AssistantInfo | null;
  onOpenSettings?: (section: string) => void;
  /** ✦ у пунктов: спросить агента. Нет — кнопок нет. */
  onAskAgent?: (request: AgentRequest) => void;
}) {
  // Ключи задач Jira в итогах — ссылки (настройки карточки).
  const jira = useContext(JiraLinks);
  const askItem = useMemo<ItemAction | undefined>(() => onAskAgent && ((text, section) => (
    <AskAgentButton label={`Спросить агента об этом пункте: ${text.length > 80 ? `${text.slice(0, 79)}…` : text}`}
      title="Спросить агента об этом пункте"
      onClick={() => onAskAgent({ kind: "summary", refs: [{ text, section }] })} />
  )), [onAskAgent]);
  /** undefined — грузится, null — итогов нет. */
  const [summary, setSummary] = useState<Summary | null | undefined>(undefined);
  /** Ошибка чтения итогов — уходит с первым удачным чтением. */
  const [loadError, setLoadError] = useState<string | null>(null);
  /** Ошибка действия (сделать, копировать). */
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [draft, setDraft] = useState<LiveDraft | null>(null);
  const seq = useRef(0);
  const target = useRef({ endpoint, id });
  target.current = { endpoint, id };

  const mine = modelJobsOf(folder, jobs, "summary");
  // Только что поставленная задача может ещё не дойти до списка — она и есть последняя.
  const latest = submitted && !mine.some((j) => j.id === submitted.id) ? submitted : mine.at(-1) ?? null;
  const thinking = latest !== null && isActiveJob(latest);
  const failed = latest?.state === "failed" ? latest : null;
  const sig = mine.map((j) => `${j.id}:${j.state}`).join(",");

  // Применяется только последний ответ и только для той записи, что показана сейчас.
  const load = useCallback(async () => {
    const mine = ++seq.current;
    const fresh = () => mine === seq.current && target.current.endpoint === endpoint && target.current.id === id;
    try {
      const data = await getSummary(endpoint, id);
      if (!fresh()) return;
      setSummary(data);
      setLoadError(null);
    } catch (e) {
      if (!fresh()) return;
      if (e instanceof ApiError && e.status === 404) {
        setSummary(null);
        setLoadError(null);
      } else {
        setLoadError(errorText(e));
      }
    }
  }, [endpoint, id]);

  // Итогов нет — черновик из живого режима, если запись шла с ассистентом.
  const noSummary = summary === null;
  useEffect(() => {
    if (!noSummary) return;
    let live = true;
    getLiveDraft(endpoint, id).then((d) => { if (live) setDraft(d); }).catch(() => { if (live) setDraft(null); });
    return () => { live = false; };
  }, [endpoint, id, noSummary]);

  // Другая запись — всё своё сначала.
  useEffect(() => {
    setDraft(null);
    setSummary(undefined);
    setLoadError(null);
    setError(null);
    setSubmitted(null);
  }, [endpoint, id]);
  useEffect(() => { void load(); }, [load, sig]);
  useEffect(() => () => { seq.current++; }, []);
  // Поставленная задача пропала из списка или так и не появилась — не ждём её.
  useLostJobs(submitted ? [submitted.id] : [], jobs, () => {
    setSubmitted(null);
    void load();
  });

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
  /** `provider` — модель, выбранная стрелкой у кнопки; нет — модель по умолчанию. */
  const make = (provider?: string) => act(async () => {
    setSubmitted(await (provider ? makeSummary(endpoint, id, provider) : makeSummary(endpoint, id)));
  });
  const [confirmNode, confirm] = useConfirm();
  /** «Переделать…» заменяет готовые итоги — сначала спросить. */
  const remake = async (provider?: string) => {
    const ok = await confirm({
      title: "Переделать итоги?", confirmLabel: "Переделать", danger: false,
      message: "Текущие итоги будут заменены новыми. Если встреча уже выгружена в базу знаний, выгрузка обновится.",
    });
    if (ok) await make(provider);
  };
  const copy = (markdown: string) => act(async () => {
    try {
      await navigator.clipboard.writeText(markdown);
    } catch {
      throw new Error("Не удалось скопировать");
    }
    setCopied(true);
  });

  const blocked = noProvider(assistant);
  const hint = blocked ? <ProviderHint onOpenSettings={onOpenSettings} info={assistant} /> : null;
  const canMake = !busy && !thinking && !blocked;
  const choices = modelChoices(assistant);
  // Модель по умолчанию недоступна — запирается только основное нажатие:
  // стрелкой можно выбрать другую доступную включённую модель.
  const canPick = !busy && !thinking && anyModelReady(choices);
  const reason = blocked ? noModelText(assistant) : null;
  const makeButton = (
    <ModelSplitButton label="Сделать итоги" variant="primary" choices={choices} disabled={!canMake}
      pickDisabled={!canPick} reason={reason} onRun={(p) => void make(p)} />
  );
  // «Повторить» после сбоя — той же моделью, что упавшая задача (не моделью по умолчанию).
  const retryBy = failed?.provider;
  const canRetry = !busy && !thinking && (retryBy ? modelReady(choices, retryBy) : !blocked);

  let main;
  if (summary) {
    main = (
      <>
        <div className="assist__toolbar">
          <ModelSplitButton label="Переделать…" choices={choices} disabled={!canMake} pickDisabled={!canPick}
            reason={reason} onRun={(p) => void remake(p)} />
          {confirmNode}
          <Button onClick={() => copy(summary.markdown)} disabled={busy}>{copied ? "Скопировано" : "Копировать"}</Button>
          {summary.llm && (
            <span className="muted assist__when" title="Какая модель сделала итоги">Итоги: {llmLabel(summary.llm)}</span>
          )}
          {typeof summary.created_at === "number" && (
            <span className="muted assist__when">{dayLabel(new Date(summary.created_at * 1000).toISOString())}</span>
          )}
        </div>
        {hint}
        <Markdown source={summary.markdown} className="assist__md" itemAction={askItem} jira={jira} />
      </>
    );
  } else if (summary === null && draft) {
    main = (
      <>
        {!thinking && (
          <div className="assist__toolbar">
            {makeButton}
            {hint}
          </div>
        )}
        <section className="assist__draft" aria-label="Черновик из живого режима">
          <h3 className="assist__draft-title">Черновик из живого режима</h3>
          <p className="muted assist__draft-note">
            Сводка, которую ассистент вёл во время встречи. Итоги модель сверит с полной расшифровкой.
          </p>
          <Markdown source={draft.markdown} className="assist__md" itemAction={askItem} jira={jira} />
        </section>
      </>
    );
  } else if (summary === null && !thinking && !failed) {
    main = (
      <EmptyState
        title="Итогов пока нет"
        hint="Модель прочитает расшифровку и выделит решения, задачи и сроки."
        action={<>
          {makeButton}
          {hint}
        </>}
      />
    );
  } else if (summary === undefined && !thinking && !error && !loadError) {
    main = <EmptyState title="Загрузка…" />;
  }

  return (
    <div className="assist">
      {loadError && <div className="assist__error" role="alert">{loadError}</div>}
      {error && <div className="assist__error" role="alert">{error}</div>}
      {thinking && <ThinkingStage job={latest} />}
      {failed && (
        <div className="assist__failed">
          <div className="assist__error">{failed.error || "Не удалось сделать итоги"}</div>
          <Button onClick={() => void make(retryBy)} disabled={!canRetry}>{retryText(retryBy)}</Button>
          {!summary && hint}
        </div>
      )}
      {main}
    </div>
  );
}
