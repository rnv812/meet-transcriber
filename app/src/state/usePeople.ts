import { useCallback, useEffect, useRef, useState } from "react";

import { type Endpoint, getPeople } from "../lib/api";
import type { Person } from "../lib/types";

const NONE: Person[] = [];

/** База людей: один экземпляр на окно; массив стабилен, пока не пришли новые данные. */
export function usePeople(ep: Endpoint | null): { people: Person[]; refresh: () => Promise<void> } {
  const [people, setPeople] = useState<Person[]>(NONE);
  const seq = useRef(0);

  const refresh = useCallback(async () => {
    if (!ep) return;
    const mine = ++seq.current;
    try {
      const r = await getPeople(ep);
      if (mine === seq.current) setPeople(r.items);
    } catch {
      /* подсказки необязательны: без базы окно работает */
    }
  }, [ep]);

  useEffect(() => { void refresh(); }, [refresh]);

  return { people, refresh };
}
