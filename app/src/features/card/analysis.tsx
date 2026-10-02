/**
 * Анализ встречи в карточке (M2): состояние (`GET /recordings/{id}/analysis`),
 * тихие строки «Анализ…», «Анализ устарел — Переанализировать», «Анализ не
 * удался — Повторить».
 *
 * Сама разметка (типы реплик, главы, наблюдения) рисуется в «Расшифровке» и
 * плеере — это M3; здесь только состояние и действия.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getAnalysis, type Endpoint } from "../../lib/api";
import type { AnalysisState, Job } from "../../lib/types";
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
          <span className="muted">Анализ не удался{state.error ? `: ${state.error}` : ""}</span>
          {onRun && <button type="button" className="link-btn" onClick={onRun} disabled={busy}>Повторить</button>}
        </div>
      );
    default:
      return null;
  }
}

/** Почему «Переанализировать» сейчас недоступно; null — доступно. */
export function reanalyzeBlocked(state: AnalysisState | null, noModel: boolean): string | null {
  if (state?.state === "queued" || state?.state === "running") return "Анализ уже идёт";
  if (noModel) return "Подключите Claude Code или Codex в настройках";
  return null;
}
