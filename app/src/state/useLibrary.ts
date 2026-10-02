import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, type Endpoint, getJobs, getRecordings, searchLibrary } from "../lib/api";
import { errorText } from "../lib/format";
import { searchable } from "../lib/search";
import type { Job, LibraryItem } from "../lib/types";

const SEARCH_DELAY_MS = 250;

/**
 * С запросом (от двух символов) — поиск по тексту встреч с фрагментами;
 * резидент без него — прежний поиск по названию и тексту. Короче — весь список.
 */
async function find(ep: Endpoint, q: string, signal: AbortSignal, categories: string[]): Promise<LibraryItem[]> {
  // Без фильтра — прежние вызовы, как у резидента до категорий.
  const cats = categories.length ? [categories] as const : [] as const;
  if (!searchable(q)) return (await (cats.length ? getRecordings(ep, undefined, ...cats) : getRecordings(ep))).items;
  try {
    return (await searchLibrary(ep, q, signal, ...cats)).items;
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) return (await getRecordings(ep, q, ...cats)).items;
    throw cause;
  }
}

const NO_FILTER: string[] = [];

export type Library = {
  items: LibraryItem[];
  jobs: Job[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

/**
 * `libraryTick` растёт на каждое событие задачи, включая прогресс; `contentTick`
 * — только когда меняется само содержимое библиотеки (задача поставлена,
 * готова, упала; запись началась, кончилась, изменилась). Прогресс обновляет
 * лишь задачи (бейджи), а список и поиск перечитываются по `contentTick`:
 * полный проход по библиотеке на каждый процент не нужен.
 */
export function useLibrary(ep: Endpoint | null, q: string, libraryTick = 0, contentTick = libraryTick,
  categories: string[] = NO_FILTER): Library {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const qRef = useRef(q);
  qRef.current = q;
  // Фильтр по категориям — у резидента, до лимита списка: старые записи нужной категории не теряются.
  const catKey = categories.join(",");
  const catRef = useRef(categories);
  catRef.current = categories;
  const pending = useRef<AbortController | null>(null);
  const contentTickRef = useRef(contentTick);
  contentTickRef.current = contentTick;

  const refresh = useCallback(async () => {
    if (!ep) return;
    const mine = ++seq.current;
    // Прежний запрос больше не нужен: резидент не дочитывает ответ, который никто не ждёт.
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    try {
      const [recs, jobList] = await Promise.all([
        find(ep, qRef.current, controller.signal, catRef.current), getJobs(ep)]);
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
  }, [ep, q, catKey, refresh]);

  const refreshJobs = useCallback(async () => {
    if (!ep) return;
    try {
      setJobs((await getJobs(ep)).items);
    } catch {
      /* прогресс задач — не повод показывать ошибку списка */
    }
  }, [ep]);

  // Обновление по событиям: первый тик (0) — начальная загрузка, её делает эффект выше.
  // Прогресс задачи (до двух событий в секунду на задачу) обновляет только задачи —
  // бейджи и проценты; список (с фильтром по категориям резидент обходит для него
  // всю библиотеку) — только когда изменилось содержимое.
  const lastContent = useRef(contentTick);
  useEffect(() => {
    if (libraryTick <= 0) return;
    // В том же событии изменилось и содержимое: список перечитает эффект ниже, задачи — вместе с ним.
    if (contentTickRef.current !== lastContent.current) return;
    void refreshJobs();
  }, [libraryTick, refreshJobs]);
  useEffect(() => {
    if (contentTick === lastContent.current) return;
    lastContent.current = contentTick;
    if (contentTick > 0) void refresh();
  }, [contentTick, refresh]);

  useEffect(() => () => pending.current?.abort(), []);

  return { items, jobs, loading, error, refresh };
}
