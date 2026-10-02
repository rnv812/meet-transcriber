/**
 * Анализ встречи в карточке (M2): состояние (`GET /recordings/{id}/analysis`),
 * тихие строки «Анализ…», «Анализ устарел — Переанализировать», «Анализ не
 * удался — Повторить», и «Предложить название» с подтверждением.
 *
 * Сама разметка (типы реплик, главы, наблюдения) рисуется в «Расшифровке» и
 * плеере — это M3; здесь только состояние и действия.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getAnalysis, patchRecording, suggestTitle, type Endpoint } from "../../lib/api";
import { errorText } from "../../lib/format";
import type { AnalysisState, Job, Recording, TitleSuggestion } from "../../lib/types";
import { Button } from "../../ui/Button";
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

/** Тихая строка о состоянии анализа под действиями карточки; нечего сказать — ничего. */
export function AnalysisStatus({ state, busy, onRun }: {
  state: AnalysisState | null;
  busy: boolean;
  /** Переанализировать; нет — без кнопки (модель не подключена). */
  onRun?: () => void;
}) {
  if (!state) return null;
  switch (state.state) {
    case "queued":
    case "running":
      return (
        <div className="analysis-status" role="status">
          <span className="analysis-chip" title={state.state === "queued" ? "Анализ встречи ждёт в очереди" : "Агент размечает встречу"}>
            <span className="analysis-chip__pulse" aria-hidden="true" />
            {state.state === "queued" ? "Анализ в очереди…" : "Анализ…"}
          </span>
        </div>
      );
    case "stale":
      return (
        <div className="analysis-status" role="status">
          <span className="muted">Анализ устарел: расшифровку изменили после него</span>
          {onRun && <button type="button" className="link-btn" onClick={onRun} disabled={busy}>Переанализировать</button>}
        </div>
      );
    case "failed":
      return (
        <div className="analysis-status" role="status">
          {/* Подробности (часто длинные и технические) — в подсказке, в строке — коротко. */}
          <span className="muted" title={state.error || undefined}>Анализ не удался</span>
          {onRun && <button type="button" className="link-btn" onClick={onRun} disabled={busy}>Повторить</button>}
        </div>
      );
    default:
      return null;
  }
}

/** Пункт меню: встречу ещё не анализировали — «Анализировать», иначе «Переанализировать». */
export const reanalyzeLabel = (state: AnalysisState | null): string =>
  state?.state === "none" ? "Анализировать" : "Переанализировать";

/** Почему «Переанализировать» сейчас недоступно; null — доступно. */
export function reanalyzeBlocked(state: AnalysisState | null, noModel: boolean): string | null {
  if (state?.state === "queued") return "Анализ уже в очереди";
  if (state?.state === "running") return "Анализ уже идёт";
  if (noModel) return "Подключите Claude Code или Codex в настройках";
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

  const open = useCallback(() => {
    const n = ++ask.current;
    setSuggest({ busy: true });
    suggestTitle(endpoint, id).then(
      (got) => { if (ask.current === n) setSuggest({ busy: false, got }); },
      (e) => { if (ask.current === n) setSuggest({ busy: false, error: errorText(e) }); },
    );
  }, [endpoint, id]);
  const close = useCallback(() => { ask.current += 1; setSuggest(null); }, []);
  const apply = useCallback(async (title: string) => {
    try {
      const rec = await patchRecording(endpoint, id, { title, title_source: "ai" });
      setSuggest(null);
      onApplied(rec);
    } catch (e) {
      setSuggest({ busy: false, error: errorText(e) });
    }
  }, [endpoint, id, onApplied]);
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
