import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, type Endpoint, getGroups, libraryFilterKey } from "../lib/api";
import { searchable } from "../lib/search";
import type { GroupInfo, LibraryFilter, UnknownGroup } from "../lib/types";

const NO_GROUPS: GroupInfo[] = [];
const NO_UNKNOWN: UnknownGroup[] = [];
const NO_FILTER: LibraryFilter = {};
/** Как у списка (useLibrary): счётчики по запросу — не на каждую букву. */
export const GROUPS_QUERY_DELAY_MS = 250;

export type Groups = {
  /** Группы по порядку со счётчиками встреч (с запросом и фильтром — среди найденного). */
  groups: GroupInfo[];
  /** Id из meta.json встреч, которых нет в списке: «Группа без названия · N встреч». */
  unknown: UnknownGroup[];
  /** Ответ уже пришёл: до этого не трогаем запомненную область. */
  loaded: boolean;
  /**
   * Умеет ли резидент группы: null — ещё не знаем, false — резидент старее 0.3.5 (`/groups` нет),
   * и интерфейс групп надо спрятать.
   */
  supported: boolean | null;
  /** Файл групп повреждён: первая запись отложит его (`brokenCopy` — уже отложенный). */
  broken: boolean;
  brokenCopy: string | null;
  /** Файл групп от более новой версии Meet: менять группы нельзя. */
  newer: boolean;
  refresh: () => Promise<void>;
};

/**
 * Группы встреч с резидента — как useCategories: перечитываются, когда растёт `tick` (события
 * `groups.changed` и смены содержимого библиотеки из useResident) или меняются запрос и фильтр
 * (счётчики — среди найденного). Запрос — с той же задержкой, что у списка, и только искомый
 * (`searchable`): резидент считает по нему встречи. Ошибка чтения — прежний список: группы —
 * подписи, а не повод для ошибки; 404 — старый резидент без групп (`supported: false`).
 */
export function useGroups(ep: Endpoint | null, tick = 0, q = "", filter: LibraryFilter = NO_FILTER): Groups {
  const [groups, setGroups] = useState<GroupInfo[]>(NO_GROUPS);
  const [unknown, setUnknown] = useState<UnknownGroup[]>(NO_UNKNOWN);
  const [loaded, setLoaded] = useState(false);
  const [supported, setSupported] = useState<boolean | null>(null);
  const [broken, setBroken] = useState(false);
  const [brokenCopy, setBrokenCopy] = useState<string | null>(null);
  const [newer, setNewer] = useState(false);
  const seq = useRef(0);
  const key = libraryFilterKey(filter);
  const filterRef = useRef(filter);
  filterRef.current = filter;
  // Запрос для счётчиков: короче двух символов — как без запроса.
  const query = searchable(q) ? q : "";
  const queryRef = useRef(query);
  queryRef.current = query;

  const refresh = useCallback(async () => {
    if (!ep) return;
    const mine = ++seq.current;
    try {
      const info = await getGroups(ep, queryRef.current || undefined,
        libraryFilterKey(filterRef.current) ? filterRef.current : undefined);
      if (mine !== seq.current) return;
      setGroups(info.groups ?? NO_GROUPS);
      setUnknown(info.unknown ?? NO_UNKNOWN);
      setBroken(Boolean(info.broken));
      setBrokenCopy(info.broken_copy ?? null);
      setNewer(Boolean(info.newer));
      setSupported(true);
      setLoaded(true);
    } catch (cause) {
      if (mine !== seq.current) return;
      if (cause instanceof ApiError && cause.status === 404) {
        setGroups(NO_GROUPS);
        setUnknown(NO_UNKNOWN);
        setSupported(false);
        setLoaded(true);
      }
      /* иначе остаётся прежний список */
    }
  }, [ep]);

  // Тик и фильтр — сразу; новый запрос — с задержкой (первая загрузка — сразу).
  const lastQuery = useRef<string | null>(null);
  useEffect(() => {
    const typed = lastQuery.current !== null && lastQuery.current !== query;
    lastQuery.current = query;
    if (!typed) {
      void refresh();
      return;
    }
    const timer = setTimeout(() => void refresh(), GROUPS_QUERY_DELAY_MS);
    return () => clearTimeout(timer);
  }, [refresh, tick, query, key]);

  return { groups, unknown, loaded, supported, broken, brokenCopy, newer, refresh };
}
