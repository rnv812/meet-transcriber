import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, type Endpoint, getJobs, getRecordings, libraryFilterKey, searchLibrary } from "../lib/api";
import { errorText } from "../lib/format";
import { searchable } from "../lib/search";
import type { Job, LibraryFilter, LibraryItem } from "../lib/types";

const SEARCH_DELAY_MS = 250;

/**
 * С запросом (от двух символов) — поиск по тексту встреч с фрагментами;
 * резидент без него — прежний поиск по названию и тексту. Короче — весь список.
 */
async function find(ep: Endpoint, q: string, signal: AbortSignal, filter: LibraryFilter): Promise<LibraryItem[]> {
  // Без фильтра — прежние вызовы, как у резидента до категорий.
  const f = libraryFilterKey(filter) ? [filter] as const : [] as const;
  if (!searchable(q)) return (await (f.length ? getRecordings(ep, undefined, ...f) : getRecordings(ep))).items;
  try {
    return (await searchLibrary(ep, q, signal, ...f)).items;
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) return (await getRecordings(ep, q, ...f)).items;
    throw cause;
  }
}

const NO_FILTER: LibraryFilter = {};

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
 *
 * `typed` — строка поиска, как её набирают (по умолчанию `q`): если изменилась она — запрос с
 * задержкой, иначе (щелчок по группе, метке, «Фильтрам») — сразу.
 */
export function useLibrary(ep: Endpoint | null, q: string, libraryTick = 0, contentTick = libraryTick,
  filter: LibraryFilter | string[] = NO_FILTER, typed: string = q): Library {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const qRef = useRef(q);
  qRef.current = q;
  // Фильтр (категории, группы, участники…) — у резидента, до лимита списка: старые записи не теряются.
  // Массив — прежний вид, только категории. Перечитываем по ключу, а не по объекту: новый объект
  // с теми же условиями на каждом рендере не должен перезапрашивать список.
  const normalized: LibraryFilter = Array.isArray(filter) ? { categories: filter } : filter;
  const filterKey = libraryFilterKey(normalized);
  const filterRef = useRef(normalized);
  filterRef.current = normalized;
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
        find(ep, qRef.current, controller.signal, filterRef.current), getJobs(ep)]);
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

  // Загрузка. С задержкой — только набор текста (`typed` — строка, как её набирают: префикс
  // `участник:Ан…` меняет фильтр на каждую букву); щелчки — область группы, метки, «Фильтры» —
  // перечитывают сразу, иначе под новым заголовком мелькает прежний список. Первая — сразу.
  const first = useRef(true);
  const typedRef = useRef(typed);
  typedRef.current = typed;
  const lastTyped = useRef(typed);
  useEffect(() => {
    if (!ep) return;
    const typing = typedRef.current !== lastTyped.current;
    const delay = first.current || !typing ? 0 : SEARCH_DELAY_MS;
    first.current = false;
    const timer = setTimeout(() => void refresh(), delay);
    return () => clearTimeout(timer);
  }, [ep, q, filterKey, refresh]);
  // После эффекта загрузки: набранное, которое список не поменяло (`уч…`), — тоже уже не «набор».
  useEffect(() => { lastTyped.current = typed; }, [typed]);

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
