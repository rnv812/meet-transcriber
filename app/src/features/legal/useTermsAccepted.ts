/**
 * Приняты ли условия — для окон без заслонки (панель трея, панель
 * ассистента): Meet часто стартует в трей, и запись оттуда можно начать, ни
 * разу не открыв главное окно с `TermsGate`.
 *
 * `true`/`false` — по ответу `GET /settings`; `null` — не знаем (резидента нет
 * или он не ответил): как у заслонки, ничего не закрываем. Перечитывает при
 * смене `endpoint` или `refresh` (номер показа панели) и когда окно получает
 * фокус: условия принимают в главном окне, а событие `TERMS_ACCEPTED_EVENT`
 * живёт только в своём окне.
 */

import { useEffect, useState } from "react";

import { type Endpoint, getSettings } from "../../lib/api";
import { TERMS_ACCEPTED_EVENT, termsAccepted } from "../../lib/terms";

export function useTermsAccepted(endpoint: Endpoint | null, refresh: unknown = 0): boolean | null {
  const [accepted, setAccepted] = useState<boolean | null>(null);
  const base = endpoint?.base ?? null;
  const token = endpoint?.token ?? null;

  useEffect(() => {
    if (base === null) return;
    let alive = true;
    let seq = 0;
    const check = () => {
      const mine = ++seq;
      getSettings({ base, token })
        .then((s) => { if (alive && mine === seq) setAccepted(termsAccepted(s)); })
        .catch(() => {});
    };
    check();
    window.addEventListener("focus", check);
    return () => { alive = false; window.removeEventListener("focus", check); };
  }, [base, token, refresh]);

  useEffect(() => {
    const done = () => setAccepted(true);
    window.addEventListener(TERMS_ACCEPTED_EVENT, done);
    return () => window.removeEventListener(TERMS_ACCEPTED_EVENT, done);
  }, []);

  return accepted;
}
