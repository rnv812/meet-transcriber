/**
 * Задачи резидента одного вида, за которыми следит экран (скачивание моделей):
 * по одной на `folder` задачи (у загрузки это id модели) — последняя
 * поставленная. Разные модели качаются одновременно, у каждой свой ход.
 * Подхватывает уже идущие задачи этого вида и опрашивает `/jobs`, пока
 * хоть одна идёт. Опрос переживает обрывы потока событий.
 */

import { useCallback, useEffect, useState } from "react";
import { type Endpoint, getJobs } from "../lib/api";
import type { Job } from "../lib/types";

export const POLL_JOB_MS = 2000;

export const jobActive = (j: Job | null | undefined) => j?.state === "queued" || j?.state === "running";

/** `folder` задачи → задача. */
export type TrackedJobs = Record<string, Job>;

/** `onSettled` — задача кончилась (успехом или нет); должна быть стабильной (useCallback). */
export function useTrackedJobs(endpoint: Endpoint, kind: string, onSettled: () => void) {
  const [jobs, setJobs] = useState<TrackedJobs>({});
  /** Следить за задачей, которую только что поставили (или вернул резидент). */
  const track = useCallback((job: Job) => setJobs((cur) => ({ ...cur, [job.folder]: job })), []);

  // Вернулись на экран, пока задачи идут, — подхватить их, чтобы виден был ход.
  useEffect(() => {
    let live = true;
    getJobs(endpoint).then((r) => {
      const mine = r.items.filter((i) => i.kind === kind && jobActive(i));
      if (!live || mine.length === 0) return;
      setJobs((cur) => {
        const next = { ...cur };
        for (const job of mine) next[job.folder] ??= job;
        return next;
      });
    }).catch(() => {});
    return () => { live = false; };
  }, [endpoint, kind]);

  // Ключ идущих задач: опрос перезапускается, только когда их набор меняется.
  const active = Object.values(jobs).filter(jobActive).map((j) => j.id).sort().join(" ");

  useEffect(() => {
    if (!active) return;
    const ids = new Set(active.split(" "));
    let live = true;
    const timer = window.setInterval(async () => {
      const list = await getJobs(endpoint).catch(() => null);
      if (!live || !list) return;
      const fresh = list.items.filter((i) => ids.has(i.id));
      if (fresh.length === 0) return;
      setJobs((cur) => {
        const next = { ...cur };
        // Модель могли поставить заново — старую задачу на её место не возвращаем
        // (запас: опрос со старым набором задач и так глушит смена набора).
        for (const job of fresh) if (next[job.folder]?.id === job.id) next[job.folder] = job;
        return next;
      });
      if (fresh.some((i) => !jobActive(i))) onSettled();
    }, POLL_JOB_MS);
    return () => { live = false; window.clearInterval(timer); };
  }, [endpoint, active, onSettled]);

  return [jobs, track] as const;
}
