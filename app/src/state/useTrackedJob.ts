/**
 * Задача резидента, за которой следит экран (скачивание модели): подхватывает
 * уже идущую задачу этого вида и опрашивает `/jobs`, пока она не кончится.
 * Опрос переживает обрывы потока событий.
 */

import { useEffect, useState } from "react";
import { type Endpoint, getJobs } from "../lib/api";
import type { Job } from "../lib/types";

export const POLL_JOB_MS = 2000;

export const jobActive = (j: Job | null) => j?.state === "queued" || j?.state === "running";

/** `onSettled` — задача кончилась (успехом или нет); должна быть стабильной (useCallback). */
export function useTrackedJob(endpoint: Endpoint, kind: string, onSettled: () => void) {
  const [job, setJob] = useState<Job | null>(null);

  // Вернулись на экран, пока задача идёт, — подхватить её, чтобы виден был ход.
  useEffect(() => {
    let live = true;
    getJobs(endpoint).then((r) => {
      const mine = r.items.find((i) => i.kind === kind && jobActive(i));
      if (live && mine) setJob((cur) => cur ?? mine);
    }).catch(() => {});
    return () => { live = false; };
  }, [endpoint, kind]);

  useEffect(() => {
    if (!job || !jobActive(job)) return;
    let live = true;
    const timer = window.setInterval(async () => {
      const list = await getJobs(endpoint).catch(() => null);
      if (!live) return;
      const mine = list?.items.find((i) => i.id === job.id);
      if (mine) setJob(mine);
      if (mine && !jobActive(mine)) onSettled();
    }, POLL_JOB_MS);
    return () => { live = false; window.clearInterval(timer); };
  }, [endpoint, job, onSettled]);

  return [job, setJob] as const;
}
