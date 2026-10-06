import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, type Endpoint, getGroups, libraryFilterKey } from "../lib/api";
import type { GroupInfo, LibraryFilter, UnknownGroup } from "../lib/types";

const NO_GROUPS: GroupInfo[] = [];
const NO_UNKNOWN: UnknownGroup[] = [];
const NO_FILTER: LibraryFilter = {};

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
  refresh: () => Promise<void>;
};

/**
 * Группы встреч с резидента — как useCategories: перечитываются, когда растёт `tick` (события
 * `groups.changed` и смены содержимого библиотеки из useResident) или меняются запрос и фильтр
 * (счётчики — среди найденного). Ошибка чтения — прежний список: группы — подписи, а не повод
 * для ошибки; 404 — старый резидент без групп (`supported: false`).
 */
export function useGroups(ep: Endpoint | null, tick = 0, q = "", filter: LibraryFilter = NO_FILTER): Groups {
  const [groups, setGroups] = useState<GroupInfo[]>(NO_GROUPS);
  const [unknown, setUnknown] = useState<UnknownGroup[]>(NO_UNKNOWN);
  const [loaded, setLoaded] = useState(false);
  const [supported, setSupported] = useState<boolean | null>(null);
  const seq = useRef(0);
  const key = libraryFilterKey(filter);
  const filterRef = useRef(filter);
  filterRef.current = filter;
  const qRef = useRef(q);
  qRef.current = q;

  const refresh = useCallback(async () => {
    if (!ep) return;
    const mine = ++seq.current;
    try {
      const info = await getGroups(ep, qRef.current || undefined,
        libraryFilterKey(filterRef.current) ? filterRef.current : undefined);
      if (mine !== seq.current) return;
      setGroups(info.groups ?? NO_GROUPS);
      setUnknown(info.unknown ?? NO_UNKNOWN);
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

  useEffect(() => { void refresh(); }, [refresh, tick, q, key]);

  return { groups, unknown, loaded, supported, refresh };
}
