/**
 * Заслонка «Прежде чем продолжить»: условия использования один раз — и у
 * новых, и у обновившихся пользователей (`ui.terms_accepted` ≠ TERMS_VERSION).
 *
 * Оборачивает окно: `<TermsGate endpoint={endpoint}>{окно}</TermsGate>`. Окно
 * под заслонкой отрисовано, но `inert`. Заслонка появляется, только когда
 * настройки прочитаны и условия текущей версии не приняты: резидента нет или
 * он не ответил — окно не заслоняется (спросим, когда резидент появится).
 *
 * Закрыть её можно только двумя кнопками: «Продолжить» (после флажка; PATCH
 * версии условий; ошибка — в окне, окно остаётся) и «Закрыть Meet» (закрывает
 * окно, ничего не принимая). Esc и щелчок по затемнению не закрывают; Tab
 * ходит по кругу внутри; фокус сразу на флажке.
 *
 * `deferred` — пока показывается что-то со своим шагом условий (мастер первого
 * запуска), заслонка ждёт; потом перечитывает настройки. Принятие условий в
 * другом месте окна (acceptTerms) убирает её сразу.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { type Endpoint, getSettings } from "../../lib/api";
import { errorText } from "../../lib/format";
import { closeWindow } from "../../lib/shell";
import {
  acceptTerms, TERMS_ACCEPTED_EVENT, TERMS_CHECKBOX, TERMS_INTRO, TERMS_TITLE, termsAccepted,
} from "../../lib/terms";
import { Button } from "../../ui/Button";
import { holdInert, releaseInert } from "../../ui/ConfirmDialog";
import "../../ui/primitives.css";
import { TermsText } from "./TermsText";
import "./terms.css";

export function TermsGate({ endpoint, deferred = false, children }: {
  /** Адрес резидента; null — его ещё нет (заслонки нет, спросим позже). */
  endpoint: Endpoint | null;
  /** Не показывать сейчас (мастер первого запуска со своим шагом условий). */
  deferred?: boolean;
  children?: ReactNode;
}) {
  const [needed, setNeeded] = useState(false);
  const base = endpoint?.base ?? null;
  const token = endpoint?.token ?? null;

  useEffect(() => {
    if (base === null || deferred) return;
    let alive = true;
    getSettings({ base, token })
      .then((s) => { if (alive) setNeeded(!termsAccepted(s)); })
      .catch((cause) => console.warn("ui.terms_accepted:", cause));
    return () => { alive = false; };
  }, [base, token, deferred]);

  useEffect(() => {
    const done = () => setNeeded(false);
    window.addEventListener(TERMS_ACCEPTED_EVENT, done);
    return () => window.removeEventListener(TERMS_ACCEPTED_EVENT, done);
  }, []);

  const show = needed && !deferred && endpoint !== null;
  return (
    <>
      {children}
      {show && <TermsDialog endpoint={endpoint} />}
    </>
  );
}

const FOCUSABLE = "button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex='-1'])";

function TermsDialog({ endpoint }: { endpoint: Endpoint }) {
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const layer = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const check = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const introId = useId();
  const errorId = useId();

  // Остальное окно — inert (общий счётчик с другими модальными окнами); фокус — на флажке.
  useEffect(() => {
    const held = holdInert(layer.current);
    check.current?.focus();
    return () => releaseInert(held);
  }, []);

  // Перехват раньше всех (window, фаза захвата): Esc не закрывает ни заслонку,
  // ни то, что под ней; Tab ходит по кругу внутри окна.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
        return;
      }
      if (e.key !== "Tab" || !box.current) return;
      const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (items.length === 0) return;
      const first = items[0]!;
      const last = items[items.length - 1]!;
      const at = document.activeElement;
      const inside = at instanceof HTMLElement && box.current.contains(at);
      if (!inside || (e.shiftKey && at === first) || (!e.shiftKey && at === last)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  const accept = async () => {
    if (!agreed || busy) return;
    setBusy(true);
    setError(null);
    try {
      await acceptTerms(endpoint); // событие уберёт заслонку
    } catch (cause) {
      setError(`Не удалось сохранить согласие: ${errorText(cause)}`);
      setBusy(false);
    }
  };

  return createPortal(
    <div ref={layer} className="backdrop backdrop--modal confirm-layer terms-layer" role="presentation">
      <div ref={box} className="sheet confirm terms" role="dialog" aria-modal="true"
        aria-labelledby={titleId} aria-describedby={introId}>
        <h2 className="confirm__title terms__title" id={titleId}>{TERMS_TITLE}</h2>
        <p className="confirm__text" id={introId}>{TERMS_INTRO}</p>
        <TermsText className="terms__body" />
        <label className="check-row terms__agree">
          <input ref={check} type="checkbox" className="cb" checked={agreed}
            aria-describedby={error ? errorId : undefined}
            onChange={(e) => { setAgreed(e.target.checked); setError(null); }} />
          <span>{TERMS_CHECKBOX}</span>
        </label>
        {error && <div className="terms__error" id={errorId} role="alert">{error}</div>}
        <div className="confirm__actions">
          <Button onClick={() => { void closeWindow().catch((cause) => console.warn("close:", cause)); }}>
            Закрыть Meet
          </Button>
          <Button variant="primary" disabled={!agreed} busy={busy} onClick={accept}>Продолжить</Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
