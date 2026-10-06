/**
 * Анализ встречи в карточке (M2): состояние (`GET /recordings/{id}/analysis`),
 * тихие строки «Анализ…», «Анализ устарел — Переанализировать», «Анализ не
 * удался — Повторить», какая модель разметила встречу («Анализ: Claude Code
 * (sonnet) · 06.10» — ушла ли встреча облачной модели), и «Предложить
 * название» с подтверждением.
 *
 * Сама разметка (типы реплик, главы, наблюдения) рисуется в «Расшифровке» и
 * плеере — это M3; здесь только состояние и действия.
 */

import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getAnalysis, patchRecording, suggestTitle, type Endpoint } from "../../lib/api";
import { errorText } from "../../lib/format";
import { llmLabel, originOf, provenance, retryText } from "../../lib/llm";
import type { Analysis, AnalysisFeature, AnalysisState, Job, Recording, TitleSuggestion } from "../../lib/types";
import { isModelProgress } from "../../lib/progress";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { JobProgress } from "../../ui/JobProgress";
import { CONFIRMS } from "./CardActions";
import { Popover } from "../../ui/Popover";
import "./analysis.css";

const norm = (p: string) => p.replace(/\\/g, "/").toLowerCase();

/** Задача анализа этой записи, которая ждёт или идёт. */
export function analysisJobOf(folder: string | null | undefined, jobs: Job[]): Job | null {
  if (!folder) return null;
  return jobs.find((j) => j.kind === "analyze" && norm(j.folder) === norm(folder)
    && (j.state === "queued" || j.state === "running")) ?? null;
}

/**
 * Состояние анализа записи. Перечитывается, когда меняется запись (`version` —
 * перечитанная карточка) и когда меняется задача анализа в очереди; пока задача
 * ждёт или идёт, состояние берётся из очереди сразу, без запроса.
 */
export function useAnalysis(endpoint: Endpoint, id: string, folder: string | null, jobs: Job[], version: unknown) {
  const [state, setState] = useState<AnalysisState | null>(null);
  const job = analysisJobOf(folder, jobs);
  const jobSig = folder
    ? jobs.filter((j) => j.kind === "analyze" && norm(j.folder) === norm(folder)).map((j) => `${j.id}:${j.state}`).join(",")
    : "";
  const current = useRef({ endpoint, id });
  current.current = { endpoint, id };

  const reload = useCallback(async () => {
    try {
      const got = await getAnalysis(endpoint, id);
      if (current.current.endpoint === endpoint && current.current.id === id) setState(got);
    } catch (e) {
      // Старый резидент без анализа (404) — просто «нет анализа».
      if (e instanceof ApiError && e.status === 404) setState({ state: "none" });
    }
  }, [endpoint, id]);

  useEffect(() => { setState(null); }, [endpoint, id]);
  useEffect(() => { void reload(); }, [reload, jobSig, version]);

  const shown: AnalysisState | null = job
    ? { ...(state ?? {}), state: job.state === "running" ? "running" : "queued", job, error: undefined }
    : state;
  return { state: shown, reload };
}

/** Части разметки в строке «Модель вернула анализ без …»: сначала то, что видно на полосе плеера. */
const MISSING_ORDER: AnalysisFeature[] = ["chapters", "importance", "types", "insights", "title", "category", "issues"];
const MISSING_NAME: Record<AnalysisFeature, string> = {
  chapters: "глав", importance: "оценок важности", types: "типов реплик", insights: "наблюдений",
  title: "названия", category: "категории", issues: "ссылок на задачи",
};
/** Короче этого встрече главы не положены (резидент просит 0–2 главы до 5 минут). */
const CHAPTERS_MIN_S = 300;
/** С этого числа сегментов пустая важность — сбой модели, а не ответ (как у резидента). */
const IMPORTANCE_MIN_SEGMENTS = 20;
/** Части в строке «… — только для части встречи». */
const PART_TITLE: Record<AnalysisFeature, string> = {
  chapters: "Главы", importance: "Оценки важности", types: "Типы реплик", insights: "Наблюдения",
  title: "Название", category: "Категория", issues: "Ссылки на задачи",
};
/** Причина в строке «часть встречи не разобрана» — коротко, целиком — в подсказке. */
const REASON_MAX = 140;

/**
 * Каких частей модель не дала. С 0.3.5 резидент пишет это сам (`missing`);
 * у анализа прежних версий — по виду: запрошены, а пусто (главы длинной
 * встречи, важность разговора).
 */
export function missingParts(a: Analysis | undefined, durationS?: number | null): AnalysisFeature[] {
  if (!a) return [];
  if (Array.isArray(a.missing)) return a.missing.filter((f) => f in MISSING_NAME);
  const asked = a.features ?? [];
  const out: AnalysisFeature[] = [];
  if (asked.includes("chapters") && !a.chapters?.length && (durationS ?? 0) >= CHAPTERS_MIN_S) out.push("chapters");
  if (asked.includes("importance") && a.importance && !Object.keys(a.importance).length
    && (a.segments ?? 0) >= IMPORTANCE_MIN_SEGMENTS) out.push("importance");
  return out;
}

/**
 * «Модель вернула анализ без глав и оценок важности — попробуйте другую
 * модель»; нечего сказать — null. У локальной модели (`local`) частая
 * причина — маленькое окно контекста: совет и про него.
 */
export function missingNote(parts: AnalysisFeature[], local = false): string | null {
  const names = MISSING_ORDER.filter((f) => parts.includes(f)).map((f) => MISSING_NAME[f]);
  if (!names.length) return null;
  const list = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} и ${names.at(-1)}`;
  const advice = local ? "увеличьте контекст модели (16K+) или попробуйте другую модель" : "попробуйте другую модель";
  return `Модель вернула анализ без ${list} — ${advice}`;
}

/** Разметила локальная модель (`openai-compatible`). */
const localModel = (a: Analysis | undefined) =>
  a?.llm?.provider === "openai-compatible" || !!a?.model?.startsWith("openai-compatible");

/**
 * Что сказать о неполном анализе: чего модель не дала, какие куски встречи
 * не разобрались (и почему), какие части есть только для части встречи,
 * обрезал ли сервер промпт. Всё — строками для карточки; пусто — анализ полный.
 */
export function analysisNotes(a: Analysis | undefined, durationS?: number | null): string[] {
  if (!a) return [];
  const notes: string[] = [];
  const missing = missingNote(missingParts(a, durationS), localModel(a));
  if (missing) notes.push(missing);
  const u = a.unparsed;
  if (u && u.parts > 0) {
    const reason = u.reason && u.reason.length > REASON_MAX ? `${u.reason.slice(0, REASON_MAX - 1)}…` : u.reason;
    notes.push(`Часть встречи не разобрана (${u.parts} из ${u.of} кусков)${reason ? `: ${reason}` : ""}`);
  }
  for (const f of MISSING_ORDER) {
    const share = a.partial?.[f];
    const [got, of] = (share ?? "").split("/");
    if (share && got && of) notes.push(`${PART_TITLE[f]} — только для части встречи (${got} из ${of} кусков)`);
  }
  if (a.context_cut) {
    // Окно Ollama ставит сам Meet: «увеличьте контекст» там не совет — нужна модель с бо́льшим окном.
    notes.push(`Модель видела только часть текста (~${a.context_cut.seen} из ~${a.context_cut.need} токенов) — `
      + (a.context_cut.capped ? "встреча длиннее окна, которое Meet запрашивает у Ollama (32K)"
        : a.context_cut.ollama ? "окно этой модели меньше нужного, возьмите модель с бо́льшим окном контекста"
          : "увеличьте контекст модели до 16K+"));
  }
  return notes;
}

/** Строки о неполном анализе; подробности (что не разобралось) — в подсказке. */
function AnalysisNotes({ analysis, durationS }: { analysis?: Analysis; durationS?: number | null }) {
  const notes = analysisNotes(analysis, durationS);
  if (!notes.length) return null;
  const title = [analysis?.unparsed?.reason, ...(analysis?.warnings ?? [])].filter(Boolean).join("\n") || undefined;
  return <>{notes.map((note) => <span key={note} className="analysis-status__missing" title={title}>{note}</span>)}</>;
}

/** Тихая строка о состоянии анализа под действиями карточки; нечего сказать — ничего. */
export function AnalysisStatus({ state, busy, onRun, durationS }: {
  state: AnalysisState | null;
  busy: boolean;
  /** Длительность встречи, с: у анализа прежних версий пустые главы короткой встречи — не сбой. */
  durationS?: number | null;
  /**
   * Переанализировать; нет — без кнопки (модель не подключена). После сбоя
   * выбранной модели — `onRun(её имя)`: повтор ею же.
   */
  onRun?: (provider?: string) => void;
}) {
  if (!state) return null;
  switch (state.state) {
    case "queued":
    case "running":
      // Резидент сообщает ход (окно, подшаг, проценты) — полоска; иначе тихий пульс.
      if (state.state === "running" && state.job && isModelProgress(state.job)) {
        return <div className="analysis-status" role="status"><JobProgress job={state.job} size="sm" /></div>;
      }
      return (
        <div className="analysis-status" role="status">
          <span className="analysis-chip" title={state.state === "queued" ? "Анализ встречи ждёт в очереди" : "Агент размечает встречу"}>
            <span className="analysis-chip__pulse" aria-hidden="true" />
            {state.state === "queued" ? "Анализ в очереди…" : "Анализ…"}
          </span>
        </div>
      );
    case "ready": {
      // Какая модель разметила встречу: видно, уходила ли она облачной модели.
      const line = provenance("Анализ", originOf(state.analysis), state.analysis?.created_at);
      const missing = <AnalysisNotes analysis={state.analysis} durationS={durationS} />;
      return line || analysisNotes(state.analysis, durationS).length ? (
        <div className="analysis-status analysis-status--origin">
          {line && <span className="muted" title="Какая модель разметила встречу">{line}</span>}
          {missing}
        </div>
      ) : null;
    }
    case "stale":
      return <StaleAnalysis busy={busy} onRun={onRun && (() => onRun())}
        origin={provenance("Анализ", originOf(state.analysis), state.analysis?.created_at)}
        missing={<AnalysisNotes analysis={state.analysis} durationS={durationS} />} />;
    case "failed":
      return (
        <div className="analysis-status" role="status">
          {/* Подробности (часто длинные и технические) — в подсказке, в строке — коротко. */}
          <span className="muted" title={state.error || undefined}>Анализ не удался</span>
          {onRun && (
            <button type="button" className="link-btn" onClick={() => onRun(state.provider)} disabled={busy}>
              {retryText(state.provider)}
            </button>
          )}
        </div>
      );
    default:
      return null;
  }
}

/** Текст разового предложения включить авто-анализ (обновившимся с 0.2.x). */
export const ANALYSIS_OFFER = "Анализ встречи: типы фраз, главы, важное и выводы. Текст встречи отправляется "
  + "выбранной модели (Claude Code, Codex или OpenCode). Включить автоматически после расшифровки?";

/**
 * Разовое предложение включить автоматический анализ — тому, кто обновился с
 * 0.2.x (там текст встреч без просьбы никуда не уходил). Ответ хранится в
 * настройках (`analysis.consent`), больше не спрашиваем.
 */
export function AnalysisOffer({ busy, onAnswer, onOpenSettings }: {
  busy: boolean;
  onAnswer: (answer: "granted" | "declined") => void;
  onOpenSettings?: () => void;
}) {
  return (
    <div className="card__banner card__banner--ok analysis-offer" role="region" aria-label="Предложение: анализ встречи">
      <span>
        {ANALYSIS_OFFER}
        <span className="muted analysis-offer__note">
          {" "}Можно изменить в{" "}
          {onOpenSettings
            ? <button type="button" className="link-btn" onClick={onOpenSettings}>настройках</button>
            : "настройках"}
        </span>
      </span>
      <span className="card__row">
        <Button variant="primary" onClick={() => onAnswer("granted")} disabled={busy}>Включить</Button>
        <Button onClick={() => onAnswer("declined")} disabled={busy}>Не сейчас</Button>
      </span>
    </div>
  );
}

/** «Анализ устарел» и «Переанализировать…» — с подтверждением: прежняя разметка будет заменена. */
function StaleAnalysis({ busy, onRun, origin, missing }: {
  busy: boolean; onRun?: () => void; origin?: string | null; missing?: ReactNode;
}) {
  const [asking, setAsking] = useState(false);
  return (
    <div className="analysis-status" role="status">
      <span className="muted" title={origin ?? undefined}>Анализ устарел: расшифровку изменили после него</span>
      {onRun && <button type="button" className="link-btn" onClick={() => setAsking(true)} disabled={busy}>Переанализировать…</button>}
      {missing}
      {asking && onRun && (
        <ConfirmDialog {...CONFIRMS.reanalyze} onCancel={() => setAsking(false)}
          onConfirm={() => { setAsking(false); onRun(); }} />
      )}
    </div>
  );
}

/** Пункт меню: встречу ещё не анализировали — «Анализировать», иначе «Переанализировать». */
export const reanalyzeLabel = (state: AnalysisState | null): string =>
  state?.state === "none" ? "Анализировать" : "Переанализировать";

/**
 * Почему «Переанализировать» сейчас недоступно; null — доступно. `noModel` —
 * модель по умолчанию недоступна (`reason` — точный текст): это запирает только
 * основное нажатие — выбор другой модели стрелкой проверяется с `noModel=false`.
 */
export function reanalyzeBlocked(state: AnalysisState | null, noModel: boolean, reason?: string): string | null {
  if (state?.state === "queued") return "Анализ уже в очереди";
  if (state?.state === "running") return "Анализ уже идёт";
  if (noModel) return reason ?? "Подключите Claude Code, Codex или OpenCode в настройках";
  return null;
}

type Suggest = { busy: true } | { busy: false; got: TitleSuggestion } | { busy: false; error: string };

/**
 * «Предложить название»: запрос предложения (из свежего анализа сразу, иначе —
 * короткий вызов модели) и окно «Применить / Отмена» у названия карточки.
 * Применённое отмечается как название от ИИ (бейдж «ИИ»).
 */
export function useTitleSuggest(endpoint: Endpoint, id: string, onApplied: (rec: Recording) => void) {
  const [suggest, setSuggest] = useState<Suggest | null>(null);
  const ask = useRef(0);

  useEffect(() => { setSuggest(null); ask.current += 1; }, [endpoint, id]);

  /** `provider` — модель, выбранная человеком: зовём её (не название из анализа). */
  const open = useCallback((provider?: string) => {
    const n = ++ask.current;
    setSuggest({ busy: true });
    (provider ? suggestTitle(endpoint, id, provider) : suggestTitle(endpoint, id)).then(
      (got) => { if (ask.current === n) setSuggest({ busy: false, got }); },
      (e) => { if (ask.current === n) setSuggest({ busy: false, error: errorText(e) }); },
    );
  }, [endpoint, id]);
  const close = useCallback(() => { ask.current += 1; setSuggest(null); }, []);
  // Какая модель предложила показанное название — уходит вместе с ним (`title_llm`).
  const by = suggest && !suggest.busy && "got" in suggest ? suggest.got.llm : undefined;
  const apply = useCallback(async (title: string) => {
    try {
      const rec = await patchRecording(endpoint, id, by ? { title, title_source: "ai", title_llm: by }
        : { title, title_source: "ai" });
      setSuggest(null);
      onApplied(rec);
    } catch (e) {
      setSuggest({ busy: false, error: errorText(e) });
    }
  }, [endpoint, id, onApplied, by]);
  return { suggest, open, close, apply };
}

export function TitleSuggestPopover({ anchor, suggest, onApply, onClose }: {
  anchor: HTMLElement;
  suggest: Suggest;
  onApply: (title: string) => void;
  onClose: () => void;
}) {
  return (
    <Popover anchor={anchor} onClose={onClose} label="Предложенное название" width={320}>
      <div className="title-suggest">
        {suggest.busy ? (
          <div className="title-suggest__busy" role="status">
            <span className="analysis-chip__pulse" aria-hidden="true" />Подбираю название…
          </div>
        ) : "got" in suggest ? (
          <>
            <div className="muted title-suggest__label">
              {suggest.got.from === "analysis" ? "Название из анализа встречи" : "Название по началу встречи"}
              {suggest.got.llm && ` · ${llmLabel(suggest.got.llm)}`}
            </div>
            <div className="title-suggest__title">{suggest.got.title}</div>
          </>
        ) : (
          <div className="error" role="alert">Не удалось предложить название: {suggest.error}</div>
        )}
        <div className="title-suggest__row">
          {!suggest.busy && "got" in suggest && (
            <Button variant="primary" onClick={() => onApply(suggest.got.title)}>Применить</Button>
          )}
          <Button onClick={onClose}>{!suggest.busy && "error" in suggest ? "Закрыть" : "Отмена"}</Button>
        </div>
      </div>
    </Popover>
  );
}
