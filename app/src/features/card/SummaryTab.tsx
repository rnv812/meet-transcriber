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
 * (`onAskAgent`): ссылка на пункт уходит в поле ввода вкладки «Агент». Над
 * итогами — «Спросить агента» об итогах целиком (тот же путь, без пунктов).
 *
 * Вид — макет Atlas Aurora: строка «Итоги собрал <модель> · <когда>», справа
 * «Копировать», «Переделать…» и ✦ «Спросить агента» (`btn--aurora`).
 */

import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { AgentRequest } from "../../lib/agentRef";
import { Copy } from "lucide-react";
import { ApiError, getLiveDraft, getSummary, makeSummary, type Endpoint } from "../../lib/api";
import { dayLabel, errorText } from "../../lib/format";
import { JiraLinks } from "../../lib/jira";
import { anyModelReady, llmLabel, modelChoices, modelReady, retryText } from "../../lib/llm";
import { Markdown, type ItemAction } from "../../lib/markdown";
import { isActiveJob, modelJobsOf } from "../../lib/status";
import type { AssistantInfo, Job, LiveDraft, Summary } from "../../lib/types";
import { AgentMark } from "../../ui/AgentMark";
import { AskAgentButton } from "../../ui/AskAgent";
import { Button } from "../../ui/Button";
import { useConfirm } from "../../ui/ConfirmDialog";
import { EmptyState } from "../../ui/EmptyState";
import { Tip } from "../../ui/Tip";
import { ProviderHint, ThinkingStage, noModelText, noProvider, useLostJobs } from "./assistant";
import { ModelSplitButton } from "./modelPick";

const COPIED_MS = 2000;

/** Таблицы итогов — карточка Aurora с таблицей `.tbl` (макет «Итоги»: «Задачи» — Кто / Что / Срок). */
const SUMMARY_TABLES = { wrap: "card summary-table", table: "tbl" };

const TASKS_HEAD = /^#{1,6}\s*(?:задачи|поручения|action items)\s*:?\s*$/i;
const LIST_ITEM = /^\s*(?:[-*+]|\d+[.)])\s+(.*)$/;
/** Похоже на срок: день недели, месяц, число, «завтра», «конец недели»… */
const DATEISH = /(понедельник|вторник|сред[уаыеи]|четверг|пятниц|суббот|воскресен|завтра|сегодня|недел|месяц|квартал|январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр|\d)/i;
const cell = (s: string) => s.replace(/\|/g, "/").trim() || "—";
const unbold = (s: string) => s.replace(/^\*\*(.+)\*\*$/, "$1").replace(/^__(.+)__$/, "$1").trim();

/** Пункт задачи «Кто: что (срок)» → [кто, что, срок]; не названо — «—». */
function taskRow(text: string): [string, string, string] {
  let who = "";
  let what = text.trim();
  const named = /^(\*\*[^*]{1,40}\*\*|[^:—–]{1,40}?)\s*(?::|\s[—–-])\s+(.+)$/.exec(what);
  if (named) { who = unbold(named[1]!); what = named[2]!.trim(); }
  let when = "";
  const paren = /\s*\(((?:до|срок:?|к)\s+[^)]+|[^)]*\d[^)]*)\)\s*\.?$/i.exec(what);
  if (paren && DATEISH.test(paren[1]!)) {
    when = paren[1]!.replace(/^срок:?\s*/i, "");
    what = what.slice(0, paren.index).trim();
  } else {
    const tail = /[,;]?\s+((?:до|к)\s+[^,;]+?)\.?$/i.exec(what);
    if (tail && DATEISH.test(tail[1]!)) { when = tail[1]!; what = what.slice(0, tail.index).trim(); }
  }
  return [cell(who), cell(what), cell(when)];
}

/**
 * Раздел «Задачи» списком («- Анна: план к среде») — таблицей «Кто | Что | Срок», как просит
 * промпт итогов и как в макете; раздел уже таблицей, прозой или без раздела — как есть.
 */
export function tasksAsTable(markdown: string): string {
  const lines = markdown.split("\n");
  const at = lines.findIndex((l) => TASKS_HEAD.test(l.trim()));
  if (at < 0) return markdown;
  let end = at + 1;
  while (end < lines.length && !/^#{1,6}\s/.test(lines[end]!)) end++;
  const body = lines.slice(at + 1, end);
  const filled = body.filter((l) => l.trim());
  if (!filled.length || !filled.every((l) => LIST_ITEM.test(l))) return markdown;
  const rows = filled.map((l) => taskRow(LIST_ITEM.exec(l)![1]!));
  const table = ["| Кто | Что | Срок |", "|---|---|---|", ...rows.map((r) => `| ${r.join(" | ")} |`)];
  const lead = body.slice(0, body.findIndex((l) => l.trim()));
  const trail = body.slice(body.length - [...body].reverse().findIndex((l) => l.trim()));
  return [...lines.slice(0, at + 1), ...lead, ...table, ...trail, ...lines.slice(end)].join("\n");
}

/** «Итоги собрал Claude Code · 14 сен 11:41»; модель неизвестна — «Итоги собраны · …»; ничего — null. */
export function summaryByline(summary: Summary): string | null {
  const who = llmLabel(summary.llm);
  const at = typeof summary.created_at === "number" && summary.created_at > 0
    // «Сегодня 11:41» в середине строки — со строчной.
    ? dayLabel(new Date(summary.created_at * 1000).toISOString()).replace(/^(Сегодня|Вчера)/, (w) => w.toLowerCase())
    : "";
  if (!who && !at) return null;
  return [who ? `Итоги собрал ${who}` : "Итоги собраны", at].filter(Boolean).join(" · ");
}

export function SummaryTab({ endpoint, id, folder, jobs, assistant, onOpenSettings, onAskAgent, onTime }: {
  /** Время «[мм:сс]» в итогах — чипом; нажатие ведёт к этой реплике в «Расшифровке». Нет — время текстом. */
  onTime?: (seconds: number) => void;
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
    const byline = summaryByline(summary);
    main = (
      <>
        <div className="assist__toolbar">
          <Tip content={summary.llm ? "Какая модель и когда сделала итоги" : null}>
            <span className="assist__when">{byline}</span>
          </Tip>
          <Button variant="ghost" icon={Copy} onClick={() => copy(summary.markdown)} disabled={busy}>
            {copied ? "Скопировано" : "Копировать"}
          </Button>
          <ModelSplitButton label="Переделать…" choices={choices} disabled={!canMake} pickDisabled={!canPick}
            reason={reason} onRun={(p) => void remake(p)} />
          {confirmNode}
          {onAskAgent && (
            <Tip content="Спросить агента об итогах: откроется вкладка «Агент»">
              <Button variant="aurora"
                onClick={() => onAskAgent({ kind: "meeting-summary", refs: [], about: "summary.md в папке встречи" })}>
                <AgentMark size={16} />Спросить агента
              </Button>
            </Tip>
          )}
        </div>
        {hint}
        <Markdown source={tasksAsTable(summary.markdown)} className="assist__md" itemAction={askItem} jira={jira}
          tables={SUMMARY_TABLES} onTime={onTime} />
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
          <h2 className="assist__draft-title">Черновик из живого режима</h2>
          <p className="muted assist__draft-note">
            Сводка, которую ассистент вёл во время встречи. Итоги модель сверит с полной расшифровкой.
          </p>
          <Markdown source={tasksAsTable(draft.markdown)} className="assist__md" itemAction={askItem} jira={jira}
            tables={SUMMARY_TABLES} onTime={onTime} />
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
    <div className="assist assist--summary">
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
