/**
 * Адрес резидента для React с переlive-резолвом.
 *
 * IMPORTANT: резидент при каждом запуске берёт новый порт и токен. Окно,
 * нашедшее адрес один раз при старте, после перезапуска дежурного стучится на
 * мёртвый порт («резидент не отвечает / Failed to fetch»). Поэтому:
 *
 * * на старте резолвим с повтором, пока дежурного нет;
 * * `reresolve()` перечитывает `daemon.json` (в Tauri это делает invoke), и
 *   меняет endpoint только если он изменился — иначе лишние переподключения.
 *
 * Всё, что делает запросы, при сбое зовёт `reresolve()`. Так окно переживает
 * любой перезапуск резидента, а не виснет на протухшем адресе.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { NoResidentError, type Endpoint, resolveEndpoint } from "./api";

const RETRY_MS = 2000;

export type EndpointState = {
  endpoint: Endpoint | null;
  /** Перечитать адрес; вернёт актуальный или null, если дежурного нет. */
  reresolve: () => Promise<Endpoint | null>;
  /** Текст последней НЕ-«резидента нет» ошибки (её показывают экраны). */
  error: string | null;
  /** Дежурного нет вовсе (в отличие от «есть, но запрос упал»). */
  missing: boolean;
};

export function useEndpoint(): EndpointState {
  const [endpoint, setEndpoint] = useState<Endpoint | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const current = useRef<Endpoint | null>(null);

  const reresolve = useCallback(async (): Promise<Endpoint | null> => {
    try {
      const found = await resolveEndpoint();
      setMissing(false);
      setError(null);
      const prev = current.current;
      if (!prev || prev.base !== found.base || prev.token !== found.token) {
        current.current = found;
        setEndpoint(found);
      }
      return found;
    } catch (cause) {
      if (cause instanceof NoResidentError) {
        setMissing(true);
      } else {
        setError(String(cause));
      }
      return null;
    }
  }, []);

  // Старт: пробуем, пока не найдём (дежурный мог подняться после окна).
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const find = async () => {
      if (cancelled) return;
      const found = await reresolve();
      if (!found && !cancelled) timer = window.setTimeout(find, RETRY_MS);
    };
    void find();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [reresolve]);

  return { endpoint, reresolve, error, missing };
}
