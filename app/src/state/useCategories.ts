import { useCallback, useEffect, useRef, useState } from "react";
import { categoriesOf, getSettings, type Endpoint } from "../lib/api";
import type { Category } from "../lib/types";

/**
 * Категории встреч из настроек — для списка и карточки. Перечитываются, когда
 * меняется `key` (например, раздел окна: из настроек вернулись — список могли
 * поправить). Ошибка чтения — прежний список (категории — подписи, а не повод
 * для ошибки).
 */
export function useCategories(ep: Endpoint | null, key: unknown = 0):
  { list: Category[]; loaded: boolean; refresh: () => Promise<void> } {
  const [list, setList] = useState<Category[]>([]);
  /** Список уже пришёл: до этого не показываем «Без категории» и не трогаем запомненный фильтр. */
  const [loaded, setLoaded] = useState(false);
  const seq = useRef(0);

  const refresh = useCallback(async () => {
    if (!ep) return;
    const mine = ++seq.current;
    try {
      const next = categoriesOf(await getSettings(ep));
      if (mine === seq.current) { setList(next); setLoaded(true); }
    } catch {
      /* остаётся прежний список */
    }
  }, [ep]);

  useEffect(() => { void refresh(); }, [refresh, key]);

  return { list, loaded, refresh };
}
