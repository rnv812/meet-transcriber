import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, type Endpoint, getJobs, getRecordings, searchLibrary } from "../lib/api";
import { errorText } from "../lib/format";
import type { Job, LibraryItem } from "../lib/types";

const SEARCH_DELAY_MS = 250;

/** С запросом — поиск по тексту встреч (с фрагментами); резидент без него — прежний поиск по названию и тексту. */
async function find(ep: Endpoint, q: string): Promise<LibraryItem[]> {
  if (!q.trim()) return (await getRecordings(ep)).items;
  try {
    return (await searchLibrary(ep, q)).items;
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) return (await getRecordings(ep, q)).items;
    throw cause;
  }
}

export type Library = {
  items: LibraryItem[];
  jobs: Job[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

export function useLibrary(ep: Endpoint | null, q: string, libraryTick = 0): Library {
  const [items, setItems] = useState<LibraryItem[]>([]);
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
      const [recs, jobList] = await Promise.all([find(ep, qRef.current), getJobs(ep)]);
      if (mine !== seq.current) return; // пришёл более новый запрос
      setItems(recs);
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
