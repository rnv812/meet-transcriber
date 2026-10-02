/**
 * Внимание без суеты: что подсветить и что посчитать «новым».
 *
 * `useFresh` — пункты, которые появились или изменились: подсветка на
 * FRESH_MS, потом сама гаснет. Первое состояние после открытия — исходное:
 * его не подсвечиваем. В режиме «Не отвлекать» подсветки нет вовсе.
 *
 * `useUnseen` — сколько пунктов человек ещё не видел: пока вкладка на
 * экране, всё в ней считается увиденным. Счётчики на вкладках и в строке
 * свёрнутой панели — отсюда.
 */

import { useEffect, useRef, useState } from "react";

/**
 * «Не отвлекать»: свой переключатель окна. Начальное значение — настройка
 * «Не отвлекать по умолчанию», как только ассистент её прислал; дальше
 * решает человек.
 */
export function useQuiet(byDefault: boolean | null): [boolean, (v: boolean) => void] {
  const [quiet, setQuiet] = useState(false);
  const decided = useRef(false);
  useEffect(() => {
    if (decided.current || byDefault === null) return;
    decided.current = true;
    if (byDefault) setQuiet(true);
  }, [byDefault]);
  const set = (v: boolean) => {
    decided.current = true;
    setQuiet(v);
  };
  return [quiet, set];
}

export const FRESH_MS = 6000;

/** `entries` — [ключ, подпись содержимого]; `ready` — первое состояние пришло. */
export function useFresh(entries: [string, string][], { enabled, ready }: { enabled: boolean; ready: boolean }): Set<string> {
  const known = useRef<Map<string, string> | null>(null);
  const [fresh, setFresh] = useState<Map<string, number>>(new Map());
  const sig = entries.map(([k, v]) => `${k}\u0000${v}`).join("\u0001");

  useEffect(() => {
    if (!ready) return;
    const next = new Map(entries);
    const before = known.current;
    known.current = next;
    if (before === null || !enabled) return; // исходное состояние или «Не отвлекать»
    const changed = [...next].filter(([k, v]) => before.get(k) !== v).map(([k]) => k);
    if (changed.length === 0) return;
    const now = Date.now();
    setFresh((cur) => {
      const out = new Map(cur);
      for (const k of changed) out.set(k, now);
      return out;
    });
    // Подпись меняется — пересчёт; entries читаем из того же рендера.
  }, [sig, ready, enabled]);

  useEffect(() => {
    if (!enabled) setFresh(new Map());
  }, [enabled]);

  useEffect(() => {
    if (fresh.size === 0) return;
    const oldest = Math.min(...fresh.values());
    const timer = setTimeout(() => {
      const now = Date.now();
      setFresh((cur) => new Map([...cur].filter(([, at]) => now - at < FRESH_MS)));
    }, Math.max(0, oldest + FRESH_MS - Date.now()));
    return () => clearTimeout(timer);
  }, [fresh]);

  return new Set(fresh.keys());
}

/**
 * Сколько ключей ещё не видели. `visible` — пункты сейчас на экране; `ready` —
 * первое состояние пришло (оно считается увиденным: открыли панель посреди
 * встречи — не сыпать счётчиками).
 */
export function useUnseen(keys: string[], visible: boolean, ready: boolean): number {
  const seen = useRef<Set<string> | null>(null);
  const [, bump] = useState(0);
  const sig = keys.join("\u0001");

  useEffect(() => {
    if (!ready) return;
    if (seen.current === null) {
      seen.current = new Set(keys);
      bump((n) => n + 1);
      return;
    }
    if (!visible) return;
    const set = seen.current;
    let added = false;
    for (const k of keys) if (!set.has(k)) { set.add(k); added = true; }
    if (added) bump((n) => n + 1);
  }, [sig, visible, ready]);

  if (!ready || seen.current === null) return 0;
  if (visible) return 0;
  return keys.filter((k) => !seen.current!.has(k)).length;
}
