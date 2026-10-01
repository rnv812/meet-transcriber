import { useCallback, useEffect, useRef, useState } from "react";

import { type Endpoint, getJobs, getRecordings } from "../lib/api";
import { errorText } from "../lib/format";
import type { Job, Recording } from "../lib/types";

const SEARCH_DELAY_MS = 250;

export type Library = {
  items: Recording[];
  jobs: Job[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

export function useLibrary(ep: Endpoint | null, q: string, libraryTick = 0): Library {
  const [items, setItems] = useState<Recording[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const qRef = useRef(q);
  qRef.current = q;

  const refresh = useCallback(async () => {
    if (!ep) return;
    const mine = ++seq.current;
    setLoading(true);
    try {
      const [recs, jobList] = await Promise.all([getRecordings(ep, qRef.current || undefined), getJobs(ep)]);
      if (mine !== seq.current) return; // пришёл более новый запрос
      setItems(recs.items);
      setJobs(jobList.items);
      setError(null);
    } catch (cause) {
      if (mine === seq.current) setError(errorText(cause));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [ep]);

  // Загрузка; поиск — с задержкой, первая загрузка — сразу.
  const first = useRef(true);
  useEffect(() => {
    if (!ep) return;
    const delay = first.current ? 0 : SEARCH_DELAY_MS;
    first.current = false;
    const timer = setTimeout(() => void refresh(), delay);
    return () => clearTimeout(timer);
  }, [ep, q, refresh]);

  // Обновление по событиям: первый тик (0) — начальная загрузка, её делает эффект выше.
  useEffect(() => {
    if (libraryTick > 0) void refresh();
  }, [libraryTick, refresh]);

  return { items, jobs, loading, error, refresh };
}
